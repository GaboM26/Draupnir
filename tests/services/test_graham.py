import math
from datetime import date

import pandas as pd
import pytest

from draupnir.models.errors import AnalysisError
from draupnir.models.graham import Outcome
from draupnir.services import graham

M = 1_000_000


def _company(years=range(1991, 2011), loss_year=None, debt=100 * M):
    """A steady company: EPS 1.0 in 1991 rising 0.2 a year, dividends every year."""
    rows = []
    for y in years:
        eps = 1.0 + 0.2 * (y - 1991)
        income = {"Total Revenue": 1000 * M, "Net Income": -5 * M if y == loss_year else eps * 100 * M,
                  "EPS (Diluted)": eps, "Dividends Per Share": 0.5}
        balance = {"Total Current Assets": 400 * M, "Total Current Liabilities": 150 * M,
                   "Total Stockholders' Equity": 1000 * M, "Goodwill": 100 * M, "Intangible Assets, Net": 50 * M,
                   "Shares Outstanding": 100 * M}
        if debt is not None:
            balance["Long-Term Debt (Non-current)"] = debt
        rows += [{"stmt": "IS", "tag_renamed": k, "value": v, "fy": str(y), "fq": "FY", "qtrs": "4",
                  "data_date": f"{y}1231"} for k, v in income.items()]
        rows += [{"stmt": "BS", "tag_renamed": k, "value": v, "fy": str(y), "fq": "FY", "qtrs": "0",
                  "data_date": f"{y}1231"} for k, v in balance.items()]
        # quarterly rows must be ignored
        rows.append({"stmt": "IS", "tag_renamed": "Net Income", "value": -1.0, "fy": str(y), "fq": "Q4", "qtrs": "0",
                     "data_date": f"{y}1231"})
    return pd.DataFrame(rows)


def _rate(financials, price=lambda t, d: 30.0, sic=3571):
    return graham.rate("abc", "ABC CORP", sic, financials, price)


def _outcomes(criteria):
    return {c.number: c.outcome for c in criteria}


def test_full_history_company_is_defensive():
    calls = []
    rating = _rate(_company(), price=lambda t, d: calls.append(d) or 30.0)
    assert rating.fiscal_year == 2010 and calls == [date(2010, 12, 31)]
    assert set(_outcomes(rating.defensive.quality).values()) == {Outcome.PASS}
    assert rating.grade == "Defensive"
    # eps3 = 4.6, book/share = 10, tangible book/share = 8.5
    assert rating.defensive.intrinsic_value == pytest.approx(math.sqrt(22.5 * 4.6 * 10))   # 32.17
    assert rating.enterprising.intrinsic_value == pytest.approx(math.sqrt(12 * 4.6 * 8.5))  # 21.66
    assert _outcomes(rating.defensive.price) == {"6": Outcome.PASS, "7": Outcome.FAIL, "6x7": Outcome.PASS}
    assert _outcomes(rating.enterprising.price) == {"-": Outcome.PASS, "5": Outcome.FAIL}
    assert rating.defensive.intrinsic_value_pct == pytest.approx(32.17 / 30, abs=1e-3)


def test_short_history_is_undetermined_not_failed():
    rating = _rate(_company(years=range(2008, 2011)))
    d = _outcomes(rating.defensive.quality)
    assert d["3"] is d["4"] is d["5"] is Outcome.UNKNOWN
    assert d["1"] is d["2-A"] is d["2-B"] is Outcome.PASS
    assert _outcomes(rating.enterprising.quality)["2"] is Outcome.UNKNOWN
    assert rating.grade == "Undetermined"


def test_a_loss_fails_both_earnings_tests_even_with_short_history():
    rating = _rate(_company(years=range(2007, 2011), loss_year=2009))
    assert _outcomes(rating.defensive.quality)["3"] is Outcome.FAIL
    assert _outcomes(rating.enterprising.quality)["2"] is Outcome.FAIL
    assert rating.grade == "Ungraded"


def test_debt_rules_and_missing_debt_line():
    heavy = _rate(_company(debt=260 * M))       # net current assets 250
    assert _outcomes(heavy.defensive.quality)["2-B"] is Outcome.FAIL
    assert _outcomes(heavy.enterprising.quality)["1-B"] is Outcome.PASS   # 260 <= 1.1 x 250
    none = _rate(_company(debt=None))
    assert _outcomes(none.defensive.quality)["2-B"] is Outcome.PASS
    assert any("counted as zero" in n for n in none.notes)


def test_missing_price_leaves_price_tests_unknown():
    def no_price(t, d):
        raise AnalysisError("no price")
    rating = _rate(_company(), price=no_price)
    assert rating.current_price is None
    assert rating.defensive.price_outcome is Outcome.UNKNOWN
    assert rating.grade == "Defensive"   # the grade does not depend on price


def test_utility_note_and_no_annual_data():
    assert any("utility" in n for n in _rate(_company(), sic=4911).notes)
    with pytest.raises(AnalysisError, match="annual"):
        _rate(_company().assign(fq="Q1"))


def test_balance_sheet_stored_as_annual_copy_is_used():
    financials = _company()
    financials.loc[financials["stmt"] == "BS", "qtrs"] = "4"   # how later loads store 10-K balance sheets
    rating = _rate(financials)
    assert _outcomes(rating.defensive.quality)["2-A"] is Outcome.PASS
    assert rating.grade == "Defensive"


def test_missing_earnings_line_is_unknown_not_a_deficit():
    financials = _company()
    financials = financials[~((financials["tag_renamed"] == "Net Income") & (financials["fy"] == "2005"))]
    rating = _rate(financials)
    assert _outcomes(rating.defensive.quality)["3"] is Outcome.UNKNOWN
    assert _outcomes(rating.enterprising.quality)["2"] is Outcome.PASS   # 2005 is outside its 5-year window
