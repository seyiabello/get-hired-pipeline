"""Sheets upsert against an in-memory worksheet. No Google calls."""

from datetime import date

from gspread.utils import a1_to_rowcol

from pipeline.sheets import COLUMNS, JOB_COLUMNS, upsert

DAY1 = date(2026, 10, 1)
DAY2 = date(2026, 10, 2)


class FakeWorksheet:
    """Holds a grid and applies batch updates the way the Sheets API does."""

    def __init__(self, grid=None, rows=5, cols=3):
        self.grid = [list(r) for r in (grid or [])]
        self.row_count = max(rows, len(self.grid))
        self.col_count = max([cols] + [len(r) for r in self.grid])
        self.batch_calls = 0
        self.options = []

    def get_all_values(self):
        width = max((len(r) for r in self.grid), default=0)
        return [[str(c) for c in r] + [""] * (width - len(r)) for r in self.grid] or [[]]

    def resize(self, rows, cols):
        self.row_count, self.col_count = rows, cols

    def batch_update(self, data, value_input_option=None):
        self.batch_calls += 1
        self.options.append(value_input_option)
        for update in data:
            top, left = a1_to_rowcol(update["range"])
            for r, values in enumerate(update["values"], start=top):
                assert r <= self.row_count and left + len(values) - 1 <= self.col_count, "write outside the grid"
                while len(self.grid) < r:
                    self.grid.append([])
                row = self.grid[r - 1]
                row.extend([""] * (left - 1 + len(values) - len(row)))
                row[left - 1:left - 1 + len(values)] = values

    def records(self):
        header = self.grid[0]
        return [dict(zip(header, row + [""] * (len(header) - len(row)))) for row in self.grid[1:]]


def job(n, **overrides):
    base = {
        "company": "Acme", "ats": "ashby", "title": f"AI Engineer {n}", "location": "London",
        "url": f"https://jobs.example.com/{n}", "posted": "2026-09-20T00:00:00+00:00", "ext_id": str(n),
        "level": "mid", "age_days": 11,
    }
    return {**base, **overrides}


def test_empty_tab_gets_headers_and_rows_in_one_write():
    ws = FakeWorksheet()
    stats = upsert(ws, [job(1), job(2)], DAY1)

    assert ws.grid[0] == COLUMNS
    assert (stats["new"], stats["updated"]) == (2, 0)
    assert ws.batch_calls == 1
    assert ws.options == ["RAW"]
    first = ws.records()[0]
    assert first["url"] == "https://jobs.example.com/1"
    assert first["first_seen"] == first["last_seen"] == "2026-10-01"
    assert first["age_days"] == 11          # a number, not text
    assert first["lead_type"] == ""         # not known until Tier C runs


def test_second_run_updates_last_seen_and_keeps_first_seen():
    ws = FakeWorksheet()
    upsert(ws, [job(1), job(2)], DAY1)
    stats = upsert(ws, [job(1, title="AI Engineer (renamed)", age_days=12), job(3)], DAY2)

    assert (stats["new"], stats["updated"]) == (1, 1)
    assert ws.batch_calls == 2              # one batch write per run
    rows = {r["url"][-1]: r for r in ws.records()}
    assert rows["1"]["title"] == "AI Engineer (renamed)"
    assert rows["1"]["age_days"] == 12
    assert (rows["1"]["first_seen"], rows["1"]["last_seen"]) == ("2026-10-01", "2026-10-02")
    assert (rows["2"]["first_seen"], rows["2"]["last_seen"]) == ("2026-10-01", "2026-10-01")  # gone, left alone
    assert (rows["3"]["first_seen"], rows["3"]["last_seen"]) == ("2026-10-02", "2026-10-02")
    assert len(ws.grid) == 4


def test_user_columns_are_never_touched():
    ws = FakeWorksheet()
    upsert(ws, [job(1), job(2)], DAY1)
    # You add two columns on the right and fill them in.
    ws.grid[0] += ["status", "notes"]
    ws.grid[1] += ["applied", "spoke to Sam"]
    ws.grid[2] += ["", "=A1"]
    ws.col_count += 2

    upsert(ws, [job(1, location="Remote"), job(2), job(3)], DAY2)

    rows = ws.records()
    assert (rows[0]["status"], rows[0]["notes"]) == ("applied", "spoke to Sam")
    assert rows[0]["location"] == "Remote"
    assert rows[1]["notes"] == "=A1"
    assert (rows[2]["status"], rows[2]["notes"]) == ("", "")
    assert ws.grid[0] == COLUMNS + ["status", "notes"]


def test_user_columns_between_pipeline_columns_and_reordered_rows():
    header = ["status", "url", "notes", "title", "company"]
    ws = FakeWorksheet([
        header,
        ["applied", "https://jobs.example.com/2", "keep me", "old title", "Acme"],
        ["", "", "a row I typed myself", "", ""],
        ["rejected", "https://jobs.example.com/1", "keep me too", "old title", "Acme"],
    ], cols=5)

    stats = upsert(ws, [job(1), job(2), job(3)], DAY2)

    assert (stats["new"], stats["updated"]) == (1, 2)
    assert ws.grid[0][:5] == header                       # existing header order kept
    assert set(ws.grid[0][5:]) == set(COLUMNS) - set(header)  # missing pipeline columns added on the right
    rows = ws.records()
    assert (rows[0]["status"], rows[0]["notes"], rows[0]["title"]) == ("applied", "keep me", "AI Engineer 2")
    assert rows[1]["notes"] == "a row I typed myself" and rows[1]["title"] == ""
    assert (rows[2]["status"], rows[2]["notes"], rows[2]["title"]) == ("rejected", "keep me too", "AI Engineer 1")
    assert rows[3]["url"] == "https://jobs.example.com/3" and rows[3]["status"] == ""
    assert rows[2]["first_seen"] == ""                    # unknown for rows that predate the column
    assert rows[2]["last_seen"] == "2026-10-02"


def test_columns_missing_from_a_job_are_not_blanked():
    ws = FakeWorksheet()
    upsert(ws, [job(1, lead_type="Warm", connection="Sam Lee", connection_title="CTO")], DAY1)
    upsert(ws, [job(1)], DAY2)  # a run where Tier C columns are absent

    row = ws.records()[0]
    assert (row["lead_type"], row["connection"], row["connection_title"]) == ("Warm", "Sam Lee", "CTO")


def test_duplicate_urls_in_one_run_make_one_row():
    ws = FakeWorksheet()
    stats = upsert(ws, [job(1), job(1, title="later copy"), {**job(2), "url": ""}], DAY1)

    assert stats["new"] == 1
    assert [r["title"] for r in ws.records()] == ["later copy"]


def test_grid_is_grown_when_needed():
    ws = FakeWorksheet(rows=2, cols=2)
    upsert(ws, [job(n) for n in range(10)], DAY1)
    assert ws.row_count >= 11 and ws.col_count >= len(COLUMNS)
    assert len(ws.grid) == 11


def test_no_jobs_on_a_ready_sheet_writes_nothing():
    ws = FakeWorksheet([COLUMNS])
    upsert(ws, [], DAY1)
    assert ws.batch_calls == 0


def test_job_columns_are_a_subset_of_sheet_columns():
    assert COLUMNS == JOB_COLUMNS + ["first_seen", "last_seen"]


# --- credentials ---

import pytest

from pipeline import sheets

FAKE_KEY = '{"type": "service_account", "private_key": "TOP-SECRET-KEY", "client_email": "x@y.iam.gserviceaccount.com"}'


@pytest.fixture
def no_credentials(monkeypatch):
    for name in ("GOOGLE_SERVICE_ACCOUNT_FILE", "GOOGLE_SERVICE_ACCOUNT_JSON", "SHEET_ID"):
        monkeypatch.delenv(name, raising=False)


def test_key_file_is_used_before_inline_json(monkeypatch, no_credentials):
    calls = []
    monkeypatch.setattr(sheets.gspread, "service_account", lambda filename, scopes: calls.append(("file", filename)))
    monkeypatch.setattr(sheets.gspread, "service_account_from_dict", lambda info, scopes: calls.append(("json", info)))

    monkeypatch.setenv("GOOGLE_SERVICE_ACCOUNT_JSON", '{"a": 1}')
    sheets._client()
    monkeypatch.setenv("GOOGLE_SERVICE_ACCOUNT_FILE", "C:/keys/key.json")
    sheets._client()

    assert calls == [("json", {"a": 1}), ("file", "C:/keys/key.json")]


def test_missing_credentials_and_sheet_id_are_reported(monkeypatch, no_credentials):
    with pytest.raises(RuntimeError, match="SHEET_ID"):
        sheets.open_worksheet("Jobs")
    monkeypatch.setenv("SHEET_ID", "abc")
    with pytest.raises(RuntimeError, match="GOOGLE_SERVICE_ACCOUNT_FILE or GOOGLE_SERVICE_ACCOUNT_JSON"):
        sheets.open_worksheet("Jobs")


@pytest.mark.parametrize("name, value", [
    ("GOOGLE_SERVICE_ACCOUNT_JSON", FAKE_KEY),                    # valid JSON, unusable key
    ("GOOGLE_SERVICE_ACCOUNT_JSON", FAKE_KEY[:-1]),               # broken JSON
    ("GOOGLE_SERVICE_ACCOUNT_FILE", "C:/secret-folder/missing-key.json"),
])
def test_credential_errors_never_contain_the_key_or_its_path(monkeypatch, no_credentials, name, value):
    monkeypatch.setenv(name, value)
    with pytest.raises(RuntimeError) as error:
        sheets._client()
    message = str(error.value)
    assert name in message
    assert "TOP-SECRET-KEY" not in message and "secret-folder" not in message
    assert error.value.__cause__ is None
