"""Output of the Graham rating: criteria outcomes per investor category, and the grade."""

from dataclasses import dataclass
from datetime import date
from enum import Enum


class Outcome(Enum):
    PASS = "Pass"
    FAIL = "Fail"
    UNKNOWN = "Not enough data"


def combine(outcomes: list[Outcome]) -> Outcome:
    """All must pass: any failure fails; otherwise any unknown leaves the result unknown."""
    if Outcome.FAIL in outcomes:
        return Outcome.FAIL
    if Outcome.UNKNOWN in outcomes:
        return Outcome.UNKNOWN
    return Outcome.PASS


@dataclass(frozen=True)
class Criterion:
    number: str     # Graham's numbering, e.g. "2-A"
    rule: str       # the rule, in words
    found: str      # what the data shows
    outcome: Outcome


@dataclass(frozen=True)
class GrahamCategory:
    """One investor category (Defensive or Enterprising)."""

    name: str
    quality: tuple[Criterion, ...]          # decide the grade
    price: tuple[Criterion, ...]            # is the stock cheap enough today
    intrinsic_value_name: str               # "Graham Number" / "Serenity Number"
    intrinsic_value: float | None           # per share; None when it cannot be computed
    intrinsic_value_formula: str
    current_price: float | None

    @property
    def quality_outcome(self) -> Outcome:
        return combine([c.outcome for c in self.quality])

    @property
    def price_outcome(self) -> Outcome:
        return combine([c.outcome for c in self.price])

    @property
    def intrinsic_value_pct(self) -> float | None:
        """Intrinsic value / price. 1.0 or more means the price is at or below the intrinsic value."""
        if self.intrinsic_value is None or not self.current_price:
            return None
        return self.intrinsic_value / self.current_price


@dataclass(frozen=True)
class GrahamRating:
    ticker: str
    company_name: str
    fiscal_year: int                # latest fiscal year loaded; criteria are measured from it
    years_loaded: tuple[int, ...]   # fiscal years with annual figures, oldest first
    price_date: date | None
    current_price: float | None
    defensive: GrahamCategory
    enterprising: GrahamCategory
    notes: tuple[str, ...]

    @property
    def grade(self) -> str:
        """GrahamValue's ladder (without its NCAV grade): the best category whose quality criteria
        all pass. "Undetermined" when nothing passes yet but missing data could still change that."""
        defensive, enterprising = self.defensive.quality_outcome, self.enterprising.quality_outcome
        if defensive is Outcome.PASS:
            return "Defensive"
        if enterprising is Outcome.PASS:
            return "Enterprising"
        if Outcome.UNKNOWN in (defensive, enterprising):
            return "Undetermined"
        return "Ungraded"
