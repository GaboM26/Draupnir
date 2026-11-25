"""Output of a valuation model."""

from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class Step:
    """One displayed line of a calculation, e.g. a projected year's cash flow."""

    label: str
    value: float
    formula: str = ""


@dataclass(frozen=True)
class ValuationResult:
    model: str
    ticker: str
    fiscal_period: str
    price_date: date
    current_price: float
    intrinsic_value: float
    """Estimated value per share."""
    steps: tuple[Step, ...] = ()

    @property
    def upside(self) -> float:
        """Fractional difference of intrinsic value over current price (0.25 = 25% undervalued)."""
        return self.intrinsic_value / self.current_price - 1

    @property
    def verdict(self) -> str:
        """Over/under-valued summary: undervaluation is measured relative to the price,
        overvaluation relative to the intrinsic value."""
        value, price = self.intrinsic_value, self.current_price
        if value > price:
            return f"Undervalued by {abs((value / price - 1) * 100):.2f}%."
        if value < price:
            return f"Overvalued by {abs((price / value - 1) * 100):.2f}%."
        return "Priced at its estimated intrinsic value."
