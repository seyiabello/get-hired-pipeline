"""Google Sheets upsert, keyed by url.

Only the pipeline's own columns are ever written. Columns you add yourself
(notes, status) and rows for jobs that have disappeared are left alone.
"""

import json
import os
from datetime import date

import gspread
from gspread.utils import rowcol_to_a1

JOB_COLUMNS = [
    "company", "ats", "title", "location", "url", "posted", "ext_id",
    "level", "age_days", "min_yoe", "salary_min", "salary_max", "salary_currency", "clearance",
    "lead_type", "connection", "connection_title",
]
COLUMNS = JOB_COLUMNS + ["first_seen", "last_seen"]
SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]


def plan_upsert(values: list[list], jobs: list[dict], today: date) -> tuple[list[dict], dict]:
    """Work out the writes needed to upsert jobs into a sheet.

    values is the sheet as it is now (first row is the header). Returns
    (updates, stats), where updates is a list of {"range", "values"} for a
    single batch call.
    """
    updates = []
    header = [str(h).strip() for h in values[0]] if values else []
    missing = [c for c in COLUMNS if c not in header]
    if missing:
        # New pipeline columns go on the right, after any columns you added.
        updates.append({"range": rowcol_to_a1(1, len(header) + 1), "values": [missing]})
        header = header + missing
    col = {name: header.index(name) for name in COLUMNS}

    row_of = {}
    for number, row in enumerate(values[1:], start=2):
        url = row[col["url"]].strip() if col["url"] < len(row) else ""
        if url:
            row_of.setdefault(url, number)

    jobs = list({job["url"]: job for job in jobs if job.get("url")}.values())
    stamp = today.isoformat()
    new_rows = []
    new_today = []  # urls whose first_seen is today: added now, or by an earlier run today
    for job in jobs:
        cells = {name: job[name] for name in JOB_COLUMNS if name in job}
        cells["last_seen"] = stamp
        if job["url"] in row_of:
            updates.extend(_row_updates(row_of[job["url"]], cells, col))
            row = values[row_of[job["url"]] - 1]
            if col["first_seen"] < len(row) and str(row[col["first_seen"]]).strip() == stamp:
                new_today.append(job["url"])
        else:
            new_today.append(job["url"])
            cells["first_seen"] = stamp
            row = [""] * len(header)
            for name, value in cells.items():
                row[col[name]] = value
            new_rows.append(row)

    if new_rows:
        first_free = max(len(values), 1) + 1
        updates.append({"range": rowcol_to_a1(first_free, 1), "values": new_rows})

    stats = {
        "new": len(new_rows),
        "updated": len(jobs) - len(new_rows),
        "rows_needed": max(len(values), 1) + len(new_rows),
        "cols_needed": len(header),
        "new_today": new_today,
    }
    return updates, stats


def _row_updates(row_number: int, cells: dict, col: dict) -> list[dict]:
    """One update per run of adjacent pipeline columns, so columns in between are not touched."""
    updates = []
    run_start, run = None, []
    for index, name in sorted((col[name], name) for name in cells):
        if run and index != run_start + len(run):
            updates.append({"range": rowcol_to_a1(row_number, run_start + 1), "values": [run]})
            run = []
        if not run:
            run_start = index
        run.append(cells[name])
    updates.append({"range": rowcol_to_a1(row_number, run_start + 1), "values": [run]})
    return updates


def upsert(worksheet, jobs: list[dict], today: date) -> dict:
    """Read the tab once, then send every change in one batch write."""
    updates, stats = plan_upsert(worksheet.get_all_values(), jobs, today)
    if stats["rows_needed"] > worksheet.row_count or stats["cols_needed"] > worksheet.col_count:
        worksheet.resize(
            rows=max(stats["rows_needed"], worksheet.row_count),
            cols=max(stats["cols_needed"], worksheet.col_count),
        )
    if updates:
        # RAW: job titles are stored as text and never interpreted as formulas.
        worksheet.batch_update(updates, value_input_option="RAW")
    return stats


def _client() -> gspread.Client:
    """GOOGLE_SERVICE_ACCOUNT_FILE (a path, for local runs) wins over
    GOOGLE_SERVICE_ACCOUNT_JSON (the key itself, for GitHub Actions)."""
    path = os.environ.get("GOOGLE_SERVICE_ACCOUNT_FILE")
    raw = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON")
    if not (path or raw):
        raise RuntimeError("set GOOGLE_SERVICE_ACCOUNT_FILE or GOOGLE_SERVICE_ACCOUNT_JSON")
    source = "GOOGLE_SERVICE_ACCOUNT_FILE" if path else "GOOGLE_SERVICE_ACCOUNT_JSON"
    try:
        if path:
            return gspread.service_account(filename=path, scopes=SCOPES)
        return gspread.service_account_from_dict(json.loads(raw), scopes=SCOPES)
    except Exception as exc:
        # Only the error type is reported, so no part of the key or its path can reach the logs.
        raise RuntimeError(f"could not load credentials from {source} ({type(exc).__name__})") from None


def open_worksheet(tab: str):
    """Open the tab in the sheet named by SHEET_ID, creating the tab if needed."""
    if not os.environ.get("SHEET_ID"):
        raise RuntimeError("missing environment variable: SHEET_ID")
    spreadsheet = _client().open_by_key(os.environ["SHEET_ID"])
    try:
        return spreadsheet.worksheet(tab)
    except gspread.WorksheetNotFound:
        return spreadsheet.add_worksheet(title=tab, rows=1000, cols=len(COLUMNS))
