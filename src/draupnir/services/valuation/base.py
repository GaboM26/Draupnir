"""The abstract valuation calculator.

A model is a subclass of ValuationModel. To add or replace one, write a subclass,
fill in `name`, `label`, `default_params`, and `calculate`, and register it in
services/valuation/__init__.py. The API and CLI only talk to this interface.

Models are pure: they receive the company's statement rows and a price lookup, and
return a ValuationResult. They never touch the database or the network themselves.
"""

from abc import ABC, abstractmethod
from collections.abc import Callable, Mapping
from datetime import date, datetime
from typing import ClassVar

import pandas as pd

from draupnir.models.errors import AnalysisError
from draupnir.models.valuation import ValuationResult

PriceLookup = Callable[[str, date], float]
"""(ticker, date) -> closing price on that date or the next trading day."""


class ValuationModel(ABC):
    name: ClassVar[str]
    """Stable identifier used in URLs and the CLI, e.g. 'dcf'."""
    label: ClassVar[str]
    """Human-readable name, e.g. 'Discounted Cash Flow'."""

    @abstractmethod
    def default_params(self) -> dict[str, float]:
        """User-tunable parameters and their defaults (growth rate, discount rate, ...)."""

    @abstractmethod
    def calculate(
        self,
        ticker: str,
        fiscal_period: str,
        financials: pd.DataFrame,
        price: PriceLookup,
        params: Mapping[str, float],
    ) -> ValuationResult:
        """Value `ticker` as of `fiscal_period` (e.g. '2019Q3') from its statement rows.

        Raises AnalysisError when the rows lack what the model needs.
        """


def period_end(rows: pd.DataFrame, year: str, quarter: str) -> str:
    """Latest `data_date` (YYYYMMDD) among `rows` filed for fiscal `year` + `quarter`."""
    dates = rows.loc[(rows["fy"] == year) & (rows["fq"] == quarter), "data_date"].dropna()
    if dates.empty:
        raise AnalysisError(f"No single-quarter figures loaded for {year}{quarter}.")
    return dates.max()


def parse_data_date(value: str) -> date:
    return datetime.strptime(value, "%Y%m%d").date()
