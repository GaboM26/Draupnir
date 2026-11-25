import pandas as pd
import pytest

from draupnir.models.errors import AnalysisError
from draupnir.services.valuation import MODELS


def _eps(data_date, fy, fq, value, stmt="IS", qtrs="0"):
    return {"stmt": stmt, "tag_renamed": "EPS (Diluted)", "value": value, "data_date": data_date,
            "fy": fy, "fq": fq, "qtrs": qtrs}


FINANCIALS = pd.DataFrame([
    _eps("20050930", "2005", "Q3", 9.0),    # before the five-year window
    _eps("20090930", "2009", "Q3", 0.25),
    _eps("20100331", "2010", "Q1", 0.5),
    _eps("20100630", "2010", "Q2", 0.0),    # zero EPS: skipped
    _eps("20100930", "2010", "Q3", 1.0),
    _eps("20101231", "2010", "Q4", 1.5),
    _eps("20101231", "2010", "FY", 4.0, qtrs="4"),
    _eps("20110331", "2011", "Q1", 2.0),    # after the requested period
])


def test_worked_example():
    calls = []
    result = MODELS["pe"].calculate(
        "abc", "2010Q4", FINANCIALS, lambda t, d: calls.append(d) or 20.0, {"years": 2, "growth": 0.1, "discount": 0.1}
    )
    steps = {s.label: s.value for s in result.steps}
    assert steps["EPS (TTM), year 0"] == 3.25            # 1.5 + 1.0 + 0.5 + 0.25
    assert steps["Average P/E"] == 38.33                  # mean(13.33, 20, 40, 80): KNOWN-BUG PE-QEPS/PE-MEAN
    assert steps["EPS, year 2"] == 3.94                   # 3.25 -> 3.58 -> 3.94
    assert steps["Value in 2 years"] == 151.02
    assert result.intrinsic_value == 124.81               # 151.02 / 1.21
    assert len(calls) == 5                                # current price + one per quarter
    assert result.current_price == 20.0


def test_no_eps():
    with pytest.raises(AnalysisError, match="EPS"):
        MODELS["pe"].calculate("abc", "2010Q2", FINANCIALS.iloc[[3]], lambda t, d: 1.0, {"years": 1, "growth": 0, "discount": 0})
