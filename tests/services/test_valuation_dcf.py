import pandas as pd
import pytest

from draupnir.models.errors import AnalysisError
from draupnir.services.valuation import MODELS


def _row(stmt, name, value, data_date="20100630", fy="2010", fq="Q2", qtrs="0"):
    return {"stmt": stmt, "tag_renamed": name, "value": value, "data_date": data_date, "fy": fy, "fq": fq, "qtrs": qtrs}


FINANCIALS = pd.DataFrame([
    _row("BS", "Cash And Cash Equivalents", 50.0),
    _row("BS", "Long-Term Debt (Non-current)", 30.0),
    _row("CF", "Cash From Operating Activities", 100.0),
    _row("CF", "Capital Expenditures", 20.0),
    _row("BS", "Shares Outstanding", 10.0),
    _row("CF", "Cash From Operating Activities", 999.0, qtrs="2"),   # cumulative: ignored
    _row("CF", "Cash From Operating Activities", 7.0, "20100331", fq="Q1"),
])
PARAMS = {"years": 2, "growth": 0.1, "decline": 0.5, "discount": 0.1, "terminal_growth": 0.0}


def test_worked_example():
    prices = []
    result = MODELS["dcf"].calculate("abc", "2010Q2", FINANCIALS, lambda t, d: prices.append((t, d)) or 50.0, PARAMS)
    steps = {s.label: s.value for s in result.steps}
    # FCF 80 -> 88.0 (x1.1) -> 92.4 (x1.05); PVs 80.0, 76.36
    assert steps["Year 0 free cash flow"] == 80.0
    assert steps["Year 2 free cash flow"] == 92.4
    assert steps["Year 2 present value"] == 76.36
    # terminal 924.0 discounted one period, not two (KNOWN-BUG DCF-TV)
    assert steps["Terminal present value"] == 840.0
    assert steps["Company value"] == 1016.36  # 996.36 + 50 - 30
    assert result.intrinsic_value == pytest.approx(101.636)
    assert steps["Value per share, exit multiple"] == 109.27  # (916.32 + 156.36 + 20) / 10
    assert prices == [("abc", pd.Timestamp("2010-06-30").date())]
    assert result.fiscal_period == "2010Q2"
    assert result.verdict == "Undervalued by 103.27%."


def test_or_list_ignores_statement_after_first_name():
    # KNOWN-BUG(TAG-OR): a later name in the debt list matches on any statement
    rows = pd.concat([FINANCIALS.iloc[[0, 2, 3, 4]], pd.DataFrame([_row("IS", "Long Term Debt", 5.0)])])
    steps = {s.label: s.value for s in MODELS["dcf"].calculate("abc", "2010Q2", rows, lambda t, d: 1.0, PARAMS).steps}
    assert steps["Long-term debt"] == 5.0


def test_missing_line_item_and_period():
    with pytest.raises(AnalysisError, match="shares outstanding"):
        MODELS["dcf"].calculate("abc", "2010Q2", FINANCIALS.iloc[:4], lambda t, d: 1.0, PARAMS)
    with pytest.raises(AnalysisError, match="2010Q3"):
        MODELS["dcf"].calculate("abc", "2010Q3", FINANCIALS, lambda t, d: 1.0, PARAMS)
    with pytest.raises(AnalysisError, match="discount rate"):
        MODELS["dcf"].calculate("abc", "2010Q2", FINANCIALS, lambda t, d: 1.0, {**PARAMS, "terminal_growth": 0.1})
