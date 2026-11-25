import pytest

from draupnir.services.valuation import ValuationModel


def test_model_must_implement_interface():
    class Incomplete(ValuationModel):
        name = "x"
        label = "X"

    with pytest.raises(TypeError):
        Incomplete()
