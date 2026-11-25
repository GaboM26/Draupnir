"""Price/earnings model. Intermediate values are rounded to cents.

Trailing-twelve-month diluted EPS is grown for `years` years, multiplied by the company's
average historical P/E over the last five years, and discounted back to today.
"""

from collections.abc import Mapping

import pandas as pd

from draupnir.models.errors import AnalysisError
from draupnir.models.financial import split_fiscal_period
from draupnir.models.valuation import Step, ValuationResult
from draupnir.services.valuation.base import PriceLookup, ValuationModel, parse_data_date, period_end

EPS_DILUTED = "EPS (Diluted)"
HISTORY_YEARS = 5


class PeModel(ValuationModel):
    name = "pe"
    label = "Price / Earnings"

    def default_params(self) -> dict[str, float]:
        return {"years": 5, "growth": 0.09, "discount": 0.09}

    def calculate(
        self,
        ticker: str,
        fiscal_period: str,
        financials: pd.DataFrame,
        price: PriceLookup,
        params: Mapping[str, float],
    ) -> ValuationResult:
        years = int(params["years"])
        growth, discount = params["growth"], params["discount"]
        if years < 1:
            raise AnalysisError("Project at least one year.")

        year, quarter = split_fiscal_period(fiscal_period)
        rows = financials[(financials["qtrs"] == "0") & (financials["stmt"] == "IS")]
        end = period_end(rows, year, quarter)
        rows = rows[rows["data_date"] <= end]
        latest = rows[rows["data_date"] == end]
        fiscal_period = f"{latest['fy'].iloc[0]}{latest['fq'].iloc[0]}"
        window_start = f"{int(end[:4]) - HISTORY_YEARS}{end[4:]}"
        rows = rows[rows["data_date"] >= window_start]

        price_date = parse_data_date(end)
        current_price = round(price(ticker, price_date), 2)

        eps = rows[rows["tag_renamed"] == EPS_DILUTED].sort_values("data_date", ascending=False, kind="stable")
        eps = eps[eps["value"] != 0]
        if eps.empty:
            raise AnalysisError(f"No quarterly diluted EPS loaded for the five years to {fiscal_period}.")
        ttm_eps = round(eps["value"].iloc[:4].sum(), 2)

        steps = []
        ratios = []
        for row in eps.itertuples():
            quarter_price = price(ticker, parse_data_date(row.data_date))
            # KNOWN-BUG(PE-QEPS): price over one quarter's EPS, later applied to annual EPS.
            ratio = round(quarter_price / row.value, 2)
            ratios.append(ratio)
            steps.append(Step(
                f"{row.fy}{row.fq} P/E", ratio, f"{round(quarter_price, 2)} (price) / {round(row.value, 2)} (EPS)"
            ))
        # KNOWN-BUG(PE-MEAN): mean where the method calls for the median.
        average_pe = round(sum(ratios) / len(ratios), 2)
        steps.append(Step("Average P/E", average_pe, f"mean of {len(ratios)} quarters"))
        steps.append(Step("EPS (TTM), year 0", ttm_eps, f"sum of the latest {min(4, len(eps))} quarters' diluted EPS"))

        projected = ttm_eps
        for yr in range(1, years + 1):
            formula = f"{projected} * (1 + {growth})"
            projected = round(projected * (1 + growth), 2)
            steps.append(Step(f"EPS, year {yr}", projected, formula))

        future_value = round(projected * average_pe, 2)
        steps.append(Step(f"Value in {years} years", future_value, f"{projected} (EPS) * {average_pe} (average P/E)"))
        intrinsic = round(future_value / (1 + discount) ** years, 2)
        steps.append(Step("Present value", intrinsic, f"{future_value} / (1 + {discount})^{years}"))

        return ValuationResult(self.name, ticker, fiscal_period, price_date, current_price, intrinsic, tuple(steps))
