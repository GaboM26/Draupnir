from datetime import date

import pytest

from draupnir.models.valuation import ValuationResult


def test_upside():
    result = ValuationResult("m", "T", "2019Q3", date(2019, 9, 30), current_price=100, intrinsic_value=125)
    assert result.upside == pytest.approx(0.25)
