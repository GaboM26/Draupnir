"""Financial rows: one statement line item value for a company at a date.

Kept as a DataFrame with these columns; text columns use
string semantics (e.g. `qtrs` "0" marks a single-quarter or point-in-time value).
"""

import re

from draupnir.models.errors import AnalysisError

STATEMENTS = ("IS", "BS", "CF")  # income statement, balance sheet, cash flow

_FISCAL_PERIOD = re.compile(r"^(\d{4})(Q[1-4])$")


def split_fiscal_period(period: str) -> tuple[str, str]:
    """'2019Q3' -> ('2019', 'Q3'), matching the text `fy` / `fq` columns."""
    match = _FISCAL_PERIOD.match(period)
    if not match:
        raise AnalysisError(f"Fiscal period must look like 2019Q3, got {period!r}.")
    return match.group(1), match.group(2)

FINANCIAL_COLUMNS = (
    "tag_sec",      # SEC tag split into words, e.g. "Net Income Loss"
    "label_sec",    # the filer's own label for the line
    "report", "line", "stmt",
    "data_date",    # YYYYMMDD the value is as of / ends on
    "fy", "fq",     # fiscal year, fiscal period (Q1..Q4, FY)
    "qtrs",         # quarters of duration: 0 = instant or derived single quarter, 1..4 = cumulative
    "uom",
    "tag_renamed",  # display name from tag_map.csv, else tag_sec
    "value",        # float
    "line_item",    # "yes" for subtotal/total lines in tag_map.csv
    "url",          # filing folder on sec.gov
    "ein_id",
)
