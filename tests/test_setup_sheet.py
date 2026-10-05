"""Sheet formatting: checks the batchUpdate requests against a fake client. No Google calls."""

import copy

import pytest

from pipeline.setup_sheet import MARKER, STATUS_OPTIONS, USER_COLUMNS, build_requests, setup
from pipeline.sheets import COLUMNS

SHEET_ID = 7


class FakeSpreadsheet:
    """Records batch_update bodies and applies just enough of them to model a second run."""

    def __init__(self, header, conditional_formats=None, filter_views=None):
        self.header = list(header)
        self.conditional_formats = list(conditional_formats or [])
        self.filter_views = list(filter_views or [])
        self.bodies = []

    def fetch_sheet_metadata(self, params=None):
        return {"sheets": [
            {"properties": {"sheetId": 99}},  # another tab, must be ignored
            {"properties": {"sheetId": SHEET_ID},
             "conditionalFormats": copy.deepcopy(self.conditional_formats),
             "filterViews": copy.deepcopy(self.filter_views)},
        ]}

    def batch_update(self, body):
        self.bodies.append(body)
        for request in body["requests"]:
            (kind, args), = request.items()
            if kind == "updateCells":
                start = args["range"]["startColumnIndex"]
                names = [v["userEnteredValue"]["stringValue"] for v in args["rows"][0]["values"]]
                self.header[start:start + len(names)] = names
            elif kind == "deleteConditionalFormatRule":
                del self.conditional_formats[args["index"]]
            elif kind == "addConditionalFormatRule":
                self.conditional_formats.insert(args["index"], args["rule"])
            elif kind == "addFilterView":
                self.filter_views.append({**args["filter"], "filterViewId": 1000 + len(self.filter_views)})
            elif kind == "updateFilterView":
                view = next(v for v in self.filter_views if v["filterViewId"] == args["filter"]["filterViewId"])
                view.update(args["filter"])


class FakeWorksheet:
    id = SHEET_ID

    def __init__(self, spreadsheet, col_count=None):
        self.spreadsheet = spreadsheet
        self._col_count = col_count

    @property
    def col_count(self):
        return self._col_count or len(self.spreadsheet.header)

    def row_values(self, row):
        assert row == 1
        return list(self.spreadsheet.header)


def kinds(requests):
    return [next(iter(r)) for r in requests]


def of_kind(requests, kind):
    return [r[kind] for r in requests if kind in r]


def letter_of(header, name):
    index = header.index(name)
    assert index < 26
    return "ABCDEFGHIJKLMNOPQRSTUVWXYZ"[index]


@pytest.fixture
def first_run():
    book = FakeSpreadsheet(COLUMNS)
    requests = setup(FakeWorksheet(book))
    return book, requests


# --- the whole thing goes in one call and touches no data ---

def test_one_batch_update_call(first_run):
    book, requests = first_run
    assert len(book.bodies) == 1
    assert book.bodies[0] == {"requests": requests}


def test_only_formatting_requests_and_no_cell_values_below_the_header(first_run):
    _, requests = first_run
    allowed = {"appendDimension", "updateCells", "updateSheetProperties", "repeatCell", "updateDimensionProperties",
               "setDataValidation", "deleteConditionalFormatRule", "addConditionalFormatRule",
               "addFilterView", "updateFilterView"}
    assert set(kinds(requests)) <= allowed
    for update in of_kind(requests, "updateCells"):
        assert (update["range"]["startRowIndex"], update["range"]["endRowIndex"]) == (0, 1)
    for repeat in of_kind(requests, "repeatCell"):
        assert repeat["fields"].startswith("userEnteredFormat")  # format only, never values
        assert "userEnteredValue" not in repeat["cell"]
    assert "sortRange" not in kinds(requests) and "deleteDimension" not in kinds(requests)


# --- 1. freeze and header style ---

def test_freezes_header_row_and_first_three_columns(first_run):
    [props] = of_kind(first_run[1], "updateSheetProperties")
    assert props["properties"] == {"sheetId": SHEET_ID, "gridProperties": {"frozenRowCount": 1, "frozenColumnCount": 3}}
    assert props["fields"] == "gridProperties.frozenRowCount,gridProperties.frozenColumnCount"


def test_columns_are_not_frozen_if_company_ats_title_are_not_first(caplog):
    header = ["notes"] + COLUMNS
    with caplog.at_level("WARNING"):
        requests = build_requests(SHEET_ID, header, len(header) + 3, {})
    [props] = of_kind(requests, "updateSheetProperties")
    assert props["properties"]["gridProperties"] == {"frozenRowCount": 1}
    assert props["fields"] == "gridProperties.frozenRowCount"
    assert "no columns are frozen" in caplog.text


def test_header_is_bold_with_a_fill(first_run):
    header_format = next(r for r in of_kind(first_run[1], "repeatCell") if r["range"].get("endRowIndex") == 1)
    assert header_format["range"] == {"sheetId": SHEET_ID, "startRowIndex": 0, "endRowIndex": 1}
    assert header_format["cell"]["userEnteredFormat"]["textFormat"] == {"bold": True}
    assert set(header_format["cell"]["userEnteredFormat"]["backgroundColor"]) == {"red", "green", "blue"}
    assert header_format["fields"] == "userEnteredFormat.textFormat.bold,userEnteredFormat.backgroundColor"


# --- 2. hidden columns, found by name ---

def test_hides_the_four_columns_by_header_name(first_run):
    book, requests = first_run
    hidden = of_kind(requests, "updateDimensionProperties")
    assert [h["range"]["startIndex"] for h in hidden] == [book.header.index(n) for n in ("ats", "posted", "ext_id", "salary_currency")]
    for h in hidden:
        assert h["range"]["endIndex"] == h["range"]["startIndex"] + 1 and h["range"]["dimension"] == "COLUMNS"
        assert h["properties"] == {"hiddenByUser": True} and h["fields"] == "hiddenByUser"


def test_columns_are_found_by_name_when_the_order_is_different():
    header = ["company", "ats", "title", "my_score", "salary_max", "posted", "url", "level", "lead_type",
              "first_seen", "last_seen", "status"]
    requests = build_requests(SHEET_ID, header, 20, {})

    hidden = [h["range"]["startIndex"] for h in of_kind(requests, "updateDimensionProperties")]
    assert hidden == [1, 5]  # ats and posted; ext_id and salary_currency do not exist here
    [salary] = [r for r in of_kind(requests, "repeatCell") if "numberFormat" in r["cell"]["userEnteredFormat"]]
    assert salary["range"]["startColumnIndex"] == 4
    formulas = [r["rule"]["booleanRule"]["condition"]["values"][0]["userEnteredValue"] for r in of_kind(requests, "addConditionalFormatRule")]
    assert "$K2" in formulas[0] and "$J2" in formulas[1] and "$H2" in formulas[2] and "$I2" in formulas[3]
    [validation] = of_kind(requests, "setDataValidation")
    assert validation["range"]["startColumnIndex"] == 11  # the existing status column is reused
    assert [u["rows"][0]["values"][0]["userEnteredValue"]["stringValue"] for u in of_kind(requests, "updateCells")] == ["notes"]


# --- 3. salary format ---

def test_salary_columns_are_pounds_with_no_decimals(first_run):
    book, requests = first_run
    salary = [r for r in of_kind(requests, "repeatCell") if "numberFormat" in r["cell"]["userEnteredFormat"]]
    assert [r["range"]["startColumnIndex"] for r in salary] == [book.header.index("salary_min"), book.header.index("salary_max")]
    for r in salary:
        assert r["range"]["startRowIndex"] == 1 and "endRowIndex" not in r["range"]  # data rows, including future ones
        assert r["cell"]["userEnteredFormat"]["numberFormat"] == {"type": "CURRENCY", "pattern": '"£"#,##0'}
        assert r["fields"] == "userEnteredFormat.numberFormat"


# --- 4. status, notes, applied_on ---

def test_adds_the_three_columns_at_the_end(first_run):
    book, requests = first_run
    [append] = of_kind(requests, "appendDimension")
    assert append == {"sheetId": SHEET_ID, "dimension": "COLUMNS", "length": 3}
    [update] = of_kind(requests, "updateCells")
    assert update["range"]["startColumnIndex"] == len(COLUMNS) and update["range"]["endColumnIndex"] == len(COLUMNS) + 3
    assert update["fields"] == "userEnteredValue"
    assert book.header == COLUMNS + USER_COLUMNS
    assert kinds(requests).index("appendDimension") < kinds(requests).index("updateCells")


def test_grid_is_not_grown_when_there_is_already_room():
    requests = build_requests(SHEET_ID, COLUMNS, len(COLUMNS) + 10, {})
    assert "appendDimension" not in kinds(requests)


def test_only_the_missing_columns_are_added():
    header = COLUMNS + ["notes"]
    requests = build_requests(SHEET_ID, header, len(header), {})
    [update] = of_kind(requests, "updateCells")
    assert [v["userEnteredValue"]["stringValue"] for v in update["rows"][0]["values"]] == ["status", "applied_on"]
    assert update["range"]["startColumnIndex"] == len(header)


def test_status_is_a_dropdown(first_run):
    book, requests = first_run
    [validation] = of_kind(requests, "setDataValidation")
    status = book.header.index("status")
    assert validation["range"] == {"sheetId": SHEET_ID, "startRowIndex": 1, "startColumnIndex": status, "endColumnIndex": status + 1}
    assert validation["rule"]["condition"]["type"] == "ONE_OF_LIST"
    assert [v["userEnteredValue"] for v in validation["rule"]["condition"]["values"]] == STATUS_OPTIONS
    assert STATUS_OPTIONS == ["To review", "Applying", "Applied", "Interviewing", "Offer", "Rejected", "Not interested"]
    assert validation["rule"]["showCustomUi"] is True


# --- 5. conditional formatting ---

def test_four_rules_in_priority_order_over_all_data_rows(first_run):
    book, requests = first_run
    rules = of_kind(requests, "addConditionalFormatRule")
    assert [r["index"] for r in rules] == [0, 1, 2, 3]
    formats = [r["rule"]["booleanRule"]["format"] for r in rules]
    assert formats[0]["textFormat"]["strikethrough"] is True and "foregroundColor" in formats[0]["textFormat"]
    assert set(formats[1]) == {"backgroundColor"}
    assert formats[2] == {"textFormat": {"bold": True}}
    assert set(formats[3]) == {"backgroundColor"} and formats[3] != formats[1]
    for r in rules:
        assert r["rule"]["ranges"] == [{"sheetId": SHEET_ID, "startRowIndex": 1, "startColumnIndex": 0, "endColumnIndex": len(book.header)}]
        assert r["rule"]["booleanRule"]["condition"]["type"] == "CUSTOM_FORMULA"


def test_rule_formulas(first_run):
    book, requests = first_run
    formulas = [r["rule"]["booleanRule"]["condition"]["values"][0]["userEnteredValue"] for r in of_kind(requests, "addConditionalFormatRule")]
    last, first = letter_of(book.header, "last_seen"), letter_of(book.header, "first_seen")
    level, lead = letter_of(book.header, "level"), letter_of(book.header, "lead_type")
    assert formulas == [
        f'=AND(N("get-hired:stale")=0,IFERROR(TODAY()-IF(ISNUMBER(${last}2),${last}2,DATEVALUE(${last}2))>2,FALSE))',
        f'=AND(N("get-hired:new")=0,IFERROR(TODAY()-IF(ISNUMBER(${first}2),${first}2,DATEVALUE(${first}2))<=2,FALSE))',
        f'=AND(N("get-hired:early")=0,OR(${level}2="intern",${level}2="graduate",${level}2="junior"))',
        f'=AND(N("get-hired:warm")=0,${lead}2="Warm")',
    ]


def test_date_rules_handle_text_dates_real_dates_and_blanks(first_run):
    formulas = [r["rule"]["booleanRule"]["condition"]["values"][0]["userEnteredValue"] for r in of_kind(first_run[1], "addConditionalFormatRule")]
    for formula in formulas[:2]:
        assert "DATEVALUE(" in formula   # dates stored as text
        assert "ISNUMBER(" in formula    # dates stored as real dates
        assert "IFERROR(" in formula     # blank or unreadable cells never match


# --- 6. filter view ---

def test_filter_view_shows_blank_status_newest_first(first_run):
    book, requests = first_run
    [added] = of_kind(requests, "addFilterView")
    view = added["filter"]
    assert view["title"] == "To review"
    assert view["range"] == {"sheetId": SHEET_ID, "startRowIndex": 0, "startColumnIndex": 0, "endColumnIndex": len(book.header)}
    assert view["filterSpecs"] == [{"columnIndex": book.header.index("status"), "filterCriteria": {"condition": {"type": "BLANK"}}}]
    assert view["sortSpecs"] == [{"dimensionIndex": book.header.index("first_seen"), "sortOrder": "DESCENDING"}]


# --- safe to run again ---

def test_second_run_adds_no_columns_and_replaces_its_own_rules_and_view(first_run):
    book, first = first_run
    rules_after_first = copy.deepcopy(book.conditional_formats)
    views_after_first = copy.deepcopy(book.filter_views)

    second = setup(FakeWorksheet(book))

    assert "appendDimension" not in kinds(second) and "updateCells" not in kinds(second)
    assert [d["index"] for d in of_kind(second, "deleteConditionalFormatRule")] == [3, 2, 1, 0]
    assert len(of_kind(second, "addConditionalFormatRule")) == 4
    assert "addFilterView" not in kinds(second)
    [updated] = of_kind(second, "updateFilterView")
    assert updated["filter"]["filterViewId"] == views_after_first[0]["filterViewId"]
    assert updated["fields"] == "title,range,filterSpecs,sortSpecs"

    # the sheet ends up exactly as it was after the first run
    assert book.header == COLUMNS + USER_COLUMNS
    assert book.conditional_formats == rules_after_first
    assert book.filter_views == views_after_first
    # everything else is identical, so repeating it changes nothing
    stable = [r for r in second if next(iter(r)) in ("updateSheetProperties", "repeatCell", "updateDimensionProperties", "setDataValidation")]
    assert stable == [r for r in first if next(iter(r)) in ("updateSheetProperties", "repeatCell", "updateDimensionProperties", "setDataValidation")]


def test_your_own_rules_and_filter_views_are_left_alone():
    mine = {"ranges": [{"sheetId": SHEET_ID}], "booleanRule": {
        "condition": {"type": "CUSTOM_FORMULA", "values": [{"userEnteredValue": '=$A2="Monzo"'}]}, "format": {}}}
    gradient = {"ranges": [{"sheetId": SHEET_ID}], "gradientRule": {}}
    my_view = {"filterViewId": 5, "title": "Applied only"}
    book = FakeSpreadsheet(COLUMNS, [mine, gradient], [my_view])
    worksheet = FakeWorksheet(book)

    setup(worksheet)
    second = setup(worksheet)

    assert [d["index"] for d in of_kind(second, "deleteConditionalFormatRule")] == [3, 2, 1, 0]
    assert book.conditional_formats[4:] == [mine, gradient]
    assert sum(MARKER in str(rule) for rule in book.conditional_formats) == 4
    assert [v["title"] for v in book.filter_views] == ["Applied only", "To review"]
    assert book.filter_views[0] == my_view


def test_our_rules_are_found_even_after_columns_move():
    book = FakeSpreadsheet(COLUMNS)
    setup(FakeWorksheet(book))
    # You drag a column to the front; Sheets rewrites the letters inside the formulas.
    for rule in book.conditional_formats:
        value = rule["booleanRule"]["condition"]["values"][0]
        value["userEnteredValue"] = value["userEnteredValue"].replace("$", "$Z")
    second = build_requests(SHEET_ID, book.header, 40, {"conditionalFormats": book.conditional_formats})
    assert len(of_kind(second, "deleteConditionalFormatRule")) == 4


# --- edge cases ---

def test_empty_tab_is_refused():
    book = FakeSpreadsheet([])
    with pytest.raises(RuntimeError, match="Run the pipeline once first"):
        setup(FakeWorksheet(book))
    assert book.bodies == []


def test_missing_pipeline_columns_are_skipped_with_a_warning(caplog):
    header = ["company", "ats", "title", "url"]
    with caplog.at_level("WARNING"):
        requests = build_requests(SHEET_ID, header, 10, {})
    assert "'salary_min'" in caplog.text and "'posted'" in caplog.text
    assert of_kind(requests, "addConditionalFormatRule") == []
    assert "sortSpecs" not in of_kind(requests, "addFilterView")[0]["filter"]
    assert len(of_kind(requests, "updateDimensionProperties")) == 1  # only ats exists
