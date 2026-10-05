"""One-off formatting of the Jobs tab. Run with: python -m pipeline.setup_sheet

Safe to run again: every step either sets a property to a fixed value or replaces
what an earlier run made. No cell values are changed, apart from adding the
status, notes and applied_on headers if they are missing. Columns are always
found by header name.
"""

import logging
import re
import sys
from pathlib import Path

from gspread.utils import rowcol_to_a1

log = logging.getLogger("get-hired")

FREEZE_COLUMNS = ["company", "ats", "title"]
HIDE_COLUMNS = ["ats", "posted", "ext_id", "salary_currency"]
SALARY_COLUMNS = ["salary_min", "salary_max"]
USER_COLUMNS = ["status", "notes", "applied_on"]
STATUS_OPTIONS = ["To review", "Applying", "Applied", "Interviewing", "Offer", "Rejected", "Not interested"]
EARLY_LEVELS = ["intern", "graduate", "junior"]
FILTER_VIEW = "To review"
RECENT_DAYS = 2

HEADER_FILL = {"red": 0.91, "green": 0.93, "blue": 0.96}
GREEN = {"red": 0.85, "green": 0.94, "blue": 0.83}
ORANGE = {"red": 0.99, "green": 0.90, "blue": 0.80}
GREY = {"red": 0.6, "green": 0.6, "blue": 0.6}

# Conditional format rules have no name, so each of ours carries this harmless tag
# inside its formula. N("text") is 0, so the tag never changes the result.
MARKER = 'N("get-hired:'


def _letter(index: int) -> str:
    return re.sub(r"\d", "", rowcol_to_a1(1, index + 1))


def _days_since(letter: str) -> str:
    """Days since the date in a column, whether it is stored as a date or as text like 2026-10-05."""
    return f"TODAY()-IF(ISNUMBER(${letter}2),${letter}2,DATEVALUE(${letter}2))"


def _tagged(name: str, test: str) -> str:
    return f'=AND({MARKER}{name}")=0,{test})'


def _rules(col: dict[str, int]) -> list[tuple[str, dict]]:
    """(formula, format) for each rule, highest priority first. Rules whose column is missing are left out."""
    rules = []
    if "last_seen" in col:  # no longer listed
        test = f"IFERROR({_days_since(_letter(col['last_seen']))}>{RECENT_DAYS},FALSE)"
        rules.append((_tagged("stale", test), {"textFormat": {"foregroundColor": GREY, "strikethrough": True}}))
    if "first_seen" in col:  # new
        test = f"IFERROR({_days_since(_letter(col['first_seen']))}<={RECENT_DAYS},FALSE)"
        rules.append((_tagged("new", test), {"backgroundColor": GREEN}))
    if "level" in col:
        letter = _letter(col["level"])
        test = "OR(" + ",".join(f'${letter}2="{level}"' for level in EARLY_LEVELS) + ")"
        rules.append((_tagged("early", test), {"textFormat": {"bold": True}}))
    if "lead_type" in col:
        rules.append((_tagged("warm", f'${_letter(col["lead_type"])}2="Warm"'), {"backgroundColor": ORANGE}))
    return rules


def _is_ours(rule: dict) -> bool:
    values = rule.get("booleanRule", {}).get("condition", {}).get("values", [])
    return any(MARKER in value.get("userEnteredValue", "") for value in values)


def build_requests(sheet_id: int, header: list[str], col_count: int, sheet_meta: dict) -> list[dict]:
    """Every Sheets API request needed to format the tab, for one batchUpdate call.

    header is row 1 as it is now. sheet_meta is this sheet's entry from the
    spreadsheet metadata, used to find rules and filter views from earlier runs.
    """
    header = [str(name).strip() for name in header]
    requests = []

    # 4a. Add status, notes, applied_on on the right if missing.
    missing = [name for name in USER_COLUMNS if name not in header]
    if missing:
        if len(header) + len(missing) > col_count:
            requests.append({"appendDimension": {
                "sheetId": sheet_id, "dimension": "COLUMNS", "length": len(header) + len(missing) - col_count,
            }})
        requests.append({"updateCells": {
            "range": {"sheetId": sheet_id, "startRowIndex": 0, "endRowIndex": 1,
                      "startColumnIndex": len(header), "endColumnIndex": len(header) + len(missing)},
            "rows": [{"values": [{"userEnteredValue": {"stringValue": name}} for name in missing]}],
            "fields": "userEnteredValue",
        }})
        header = header + missing
    col = {name: index for index, name in reversed(list(enumerate(header))) if name}

    def column(name: str) -> dict:
        """The data rows of one column: row 2 down, with no end so new rows are covered."""
        return {"sheetId": sheet_id, "startRowIndex": 1, "startColumnIndex": col[name], "endColumnIndex": col[name] + 1}

    def present(names: list[str], purpose: str) -> list[str]:
        for name in names:
            if name not in col:
                log.warning("no %r column, skipping it for %s", name, purpose)
        return [name for name in names if name in col]

    # 1. Freeze the header row and the leading columns, and style the header.
    grid = {"frozenRowCount": 1}
    fields = "gridProperties.frozenRowCount"
    if [col.get(name) for name in FREEZE_COLUMNS] == list(range(len(FREEZE_COLUMNS))):
        grid["frozenColumnCount"] = len(FREEZE_COLUMNS)
        fields += ",gridProperties.frozenColumnCount"
    else:
        log.warning("%s are not the first %d columns, so no columns are frozen", ", ".join(FREEZE_COLUMNS), len(FREEZE_COLUMNS))
    requests.append({"updateSheetProperties": {
        "properties": {"sheetId": sheet_id, "gridProperties": grid}, "fields": fields,
    }})
    requests.append({"repeatCell": {
        "range": {"sheetId": sheet_id, "startRowIndex": 0, "endRowIndex": 1},
        "cell": {"userEnteredFormat": {"textFormat": {"bold": True}, "backgroundColor": HEADER_FILL}},
        "fields": "userEnteredFormat.textFormat.bold,userEnteredFormat.backgroundColor",
    }})

    # 2. Hide the columns that are only useful to the pipeline.
    for name in present(HIDE_COLUMNS, "hiding"):
        requests.append({"updateDimensionProperties": {
            "range": {"sheetId": sheet_id, "dimension": "COLUMNS", "startIndex": col[name], "endIndex": col[name] + 1},
            "properties": {"hiddenByUser": True},
            "fields": "hiddenByUser",
        }})

    # 3. Salaries as whole pounds.
    for name in present(SALARY_COLUMNS, "currency format"):
        requests.append({"repeatCell": {
            "range": column(name),
            "cell": {"userEnteredFormat": {"numberFormat": {"type": "CURRENCY", "pattern": '"£"#,##0'}}},
            "fields": "userEnteredFormat.numberFormat",
        }})

    # 4b. Status dropdown.
    requests.append({"setDataValidation": {
        "range": column("status"),
        "rule": {
            "condition": {"type": "ONE_OF_LIST", "values": [{"userEnteredValue": option} for option in STATUS_OPTIONS]},
            "showCustomUi": True,
            "strict": True,
        },
    }})

    # 5. Conditional formatting: remove the rules from earlier runs, then add the current set
    #    at the top. Deleting from the highest index down keeps the other indexes valid.
    existing = sheet_meta.get("conditionalFormats", [])
    for index in reversed([i for i, rule in enumerate(existing) if _is_ours(rule)]):
        requests.append({"deleteConditionalFormatRule": {"sheetId": sheet_id, "index": index}})
    data_rows = {"sheetId": sheet_id, "startRowIndex": 1, "startColumnIndex": 0, "endColumnIndex": len(header)}
    for index, (formula, cell_format) in enumerate(_rules(col)):
        requests.append({"addConditionalFormatRule": {
            "index": index,
            "rule": {
                "ranges": [data_rows],
                "booleanRule": {
                    "condition": {"type": "CUSTOM_FORMULA", "values": [{"userEnteredValue": formula}]},
                    "format": cell_format,
                },
            },
        }})

    # 6. Filter view: update the one from an earlier run, or add it.
    view = {
        "title": FILTER_VIEW,
        "range": {"sheetId": sheet_id, "startRowIndex": 0, "startColumnIndex": 0, "endColumnIndex": len(header)},
        "filterSpecs": [{"columnIndex": col["status"], "filterCriteria": {"condition": {"type": "BLANK"}}}],
    }
    if "first_seen" in col:
        # ISO dates stored as text sort correctly as text.
        view["sortSpecs"] = [{"dimensionIndex": col["first_seen"], "sortOrder": "DESCENDING"}]
    else:
        log.warning("no 'first_seen' column, the filter view is not sorted")
    previous = next((v for v in sheet_meta.get("filterViews", []) if v.get("title") == FILTER_VIEW), None)
    if previous:
        view["filterViewId"] = previous["filterViewId"]
        requests.append({"updateFilterView": {"filter": view, "fields": "title,range,filterSpecs,sortSpecs"}})
    else:
        requests.append({"addFilterView": {"filter": view}})
    return requests


def setup(worksheet) -> list[dict]:
    """Format one worksheet. Reads the header and metadata, then sends a single batchUpdate."""
    header = worksheet.row_values(1)
    if not any(str(name).strip() for name in header):
        raise RuntimeError("the tab has no header row yet. Run the pipeline once first.")
    metadata = worksheet.spreadsheet.fetch_sheet_metadata(
        params={"fields": "sheets(properties(sheetId),conditionalFormats,filterViews)"}
    )
    sheet_meta = next(s for s in metadata["sheets"] if s["properties"]["sheetId"] == worksheet.id)
    requests = build_requests(worksheet.id, header, worksheet.col_count, sheet_meta)
    worksheet.spreadsheet.batch_update({"requests": requests})
    return requests


def main() -> int:
    import yaml

    from pipeline.sheets import open_worksheet

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    config = yaml.safe_load((Path(__file__).parent.parent / "config.yaml").read_text(encoding="utf-8"))
    try:
        requests = setup(open_worksheet(config["sheets"]["tab"]))
    except Exception as exc:
        log.error("Sheet setup failed: %s", exc)
        return 1
    log.info("Sheet setup done: %d formatting requests applied to the %r tab", len(requests), config["sheets"]["tab"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
