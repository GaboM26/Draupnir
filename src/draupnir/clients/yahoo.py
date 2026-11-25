"""Yahoo Finance client (via yfinance).

`closing_price` is the PriceLookup passed to valuation models; valuation code never imports
this module.
"""

import functools
from datetime import date, timedelta

import yfinance as yf

from draupnir.models.errors import AnalysisError

LOOKAHEAD = timedelta(days=4)  # the date may fall on a weekend or holiday


@functools.lru_cache(maxsize=4096)
def closing_price(ticker: str, day: date) -> float:
    """Close on `day`, or on the first trading day within the next four days.

    KNOWN-BUG(PRICE-SPLIT): Yahoo closes are split-adjusted, but SEC per-share figures (EPS,
    shares outstanding) are as reported, so prices before a later split don't match them.
    """
    history = yf.Ticker(ticker.upper()).history(start=day, end=day + LOOKAHEAD, auto_adjust=False)
    if history.empty:
        raise AnalysisError(f"Yahoo Finance has no price for {ticker.upper()} from {day} to {day + LOOKAHEAD}.")
    return float(history["Close"].iloc[0])
