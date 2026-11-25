"""Price / earnings (P/E) model.

The method, step by step (names match the code below):

  1. Earnings per share over the last year (trailing twelve months):
         ttm_eps = sum of the latest 4 quarterly diluted EPS figures (zero quarters skipped)

  2. The company's typical P/E: for every quarter in the five years up to the period end,
         pe_ratio[q] = price on that quarter's end date / that quarter's diluted EPS   <- KNOWN-BUG(PE-QEPS)
         average_pe  = mean of pe_ratio[q]                                             <- KNOWN-BUG(PE-MEAN)

  3. Project EPS forward at a constant growth rate:
         eps[0] = ttm_eps
         eps[y] = eps[y - 1] * (1 + growth_rate)                       for y = 1 .. years

  4. Price in `years` years if the stock trades at its typical P/E, discounted back to today:
         future_value    = eps[years] * average_pe
         value_per_share = future_value / (1 + discount_rate) ** years

Every intermediate amount is rounded to cents, at the same points as listed, so results can be
reproduced by hand from the step table the model returns.

Known flaws, kept until the fix pass (see CLAUDE.md):
  PE-QEPS  step 2 divides by one quarter's EPS, so ratios are about 4x a normal annual P/E,
           and step 4 then applies them to annual EPS. Values come out roughly 4x too high.
  PE-MEAN  step 2 averages; a median resists the extreme ratios that near-zero EPS quarters produce.
"""

from collections.abc import Mapping

import pandas as pd

from draupnir.models.errors import AnalysisError
from draupnir.models.financial import split_fiscal_period
from draupnir.models.valuation import Step, ValuationResult
from draupnir.services.valuation.base import PriceLookup, ValuationModel, cents, parse_data_date, period_end

EPS_DILUTED = "EPS (Diluted)"   # income statement line, by display name (services/tag_map.csv)
HISTORY_YEARS = 5               # how far back step 2 looks
TTM_QUARTERS = 4


class PeModel(ValuationModel):
    name = "pe"
    label = "Price / Earnings"

    def default_params(self) -> dict[str, float]:
        return {
            "years": 5,         # projection horizon
            "growth": 0.09,     # annual EPS growth
            "discount": 0.09,   # required annual return
        }

    def calculate(
        self,
        ticker: str,
        fiscal_period: str,
        financials: pd.DataFrame,
        price: PriceLookup,
        params: Mapping[str, float],
    ) -> ValuationResult:
        years = int(params["years"])
        growth_rate = params["growth"]
        discount_rate = params["discount"]
        if years < 1:
            raise AnalysisError("Project at least one year.")

        eps_history, found_period, end = read_eps_history(financials, fiscal_period)
        price_date = parse_data_date(end)
        current_price = cents(price(ticker, price_date))
        steps = []

        # Step 1: trailing-twelve-month EPS (eps_history is newest first).
        ttm_eps = cents(eps_history["value"].iloc[:TTM_QUARTERS].sum())

        # Step 2: historical P/E, one ratio per quarter.
        pe_ratios = []
        for quarter in eps_history.itertuples():
            quarter_price = price(ticker, parse_data_date(quarter.data_date))
            # KNOWN-BUG(PE-QEPS): divides by one quarter's EPS; a P/E should divide by annual (TTM) EPS.
            pe_ratio = cents(quarter_price / quarter.value)
            pe_ratios.append(pe_ratio)
            steps.append(Step(
                f"{quarter.fy}{quarter.fq} P/E", pe_ratio,
                f"{cents(quarter_price)} (price) / {cents(quarter.value)} (EPS)",
            ))
        # KNOWN-BUG(PE-MEAN): the mean; the method calls for the median.
        average_pe = cents(sum(pe_ratios) / len(pe_ratios))
        steps.append(Step("Average P/E", average_pe, f"mean of {len(pe_ratios)} quarters"))

        # Step 3: project EPS.
        steps.append(Step(
            "EPS (TTM), year 0", ttm_eps,
            f"sum of the latest {min(TTM_QUARTERS, len(eps_history))} quarters' diluted EPS",
        ))
        eps = ttm_eps
        for year in range(1, years + 1):
            previous_eps = eps
            eps = cents(previous_eps * (1 + growth_rate))
            steps.append(Step(f"EPS, year {year}", eps, f"{previous_eps} * (1 + {growth_rate})"))

        # Step 4: future price at the average P/E, discounted to today.
        future_value = cents(eps * average_pe)
        value_per_share = cents(future_value / (1 + discount_rate) ** years)
        steps.append(Step(f"Value in {years} years", future_value, f"{eps} (EPS) * {average_pe} (average P/E)"))
        steps.append(Step("Present value", value_per_share, f"{future_value} / (1 + {discount_rate})^{years}"))

        return ValuationResult(self.name, ticker, found_period, price_date, current_price, value_per_share, tuple(steps))


def read_eps_history(financials: pd.DataFrame, fiscal_period: str) -> tuple[pd.DataFrame, str, str]:
    """Quarterly diluted EPS rows for the five years up to the period end, newest first.

    Returns (rows, fiscal period found, period end as YYYYMMDD). Quarters with zero EPS are left out,
    since a P/E cannot be computed for them.
    """
    year, quarter = split_fiscal_period(fiscal_period)
    income = financials[(financials["qtrs"] == "0") & (financials["stmt"] == "IS")]
    end = period_end(income, year, quarter)
    first_row_on_end_date = income[income["data_date"] == end].iloc[0]
    found_period = f"{first_row_on_end_date['fy']}{first_row_on_end_date['fq']}"

    window_start = f"{int(end[:4]) - HISTORY_YEARS}{end[4:]}"   # same month and day, five years earlier
    in_window = income[(income["data_date"] >= window_start) & (income["data_date"] <= end)]
    eps = in_window[in_window["tag_renamed"] == EPS_DILUTED]
    eps = eps.sort_values("data_date", ascending=False, kind="stable")
    eps = eps[eps["value"] != 0]
    if eps.empty:
        raise AnalysisError(f"No quarterly diluted EPS loaded for the five years to {found_period}.")
    return eps, found_period, end

