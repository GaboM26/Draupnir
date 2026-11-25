"""Discounted cash flow model. Intermediate values are rounded to cents.

Free cash flow (operating cash flow - capex) is grown for `years` years at a growth rate that
declines each year, discounted, and capped with a perpetuity terminal value. Value per share =
(sum of discounted flows + cash - long-term debt) / shares outstanding.
"""

from collections.abc import Mapping, Sequence

import pandas as pd

from draupnir.models.errors import AnalysisError
from draupnir.models.financial import split_fiscal_period
from draupnir.models.valuation import Step, ValuationResult
from draupnir.services.valuation.base import PriceLookup, ValuationModel, parse_data_date, period_end

# Line items by our display names (services/tag_map.csv, else the SEC tag split into words).
CASH = "Cash And Cash Equivalents"
CASH_END_OF_PERIOD = "Cash And Cash Equivalents (End Of Period)"
LONG_TERM_DEBT = (
    "Long-Term Debt (Non-current)",
    "Unsecured Long Term Debt",
    "Long Term Debt And Capital Lease Obligations",
    "Long Term Debt And Capital Lease Obligations Including Current Maturities",
    "Long Term Debt",
    "Long Term Debt And Finance Leases (Non-current)",
)
OPERATING_CASH_FLOW = (
    "Cash From Operating Activities",
    "Net Cash Provided By Used In Continuing Operations",
    "Net Cash Provided By Used In Operating Activities Continuing Operations",
)
# KNOWN-BUG(CAPEX-TAGS): accrued (non-cash) and net-of-proceeds concepts count as capex.
CAPEX = (
    "Capital Expenditures",
    "Capital Expenditures Incurred But Not Yet Paid",
    "Payments To Acquire Productive Assets",
    "Capital Expenditures Net",
    "Payments For Proceeds From Productive Assets",
    "Payments To Acquire Machinery And Equipment",
    "Capital Expenditures Development Redevelopment",
    "Payments For Capital Improvements",
)
SHARES = "Shares Outstanding"
EXIT_MULTIPLE = 12


class DcfModel(ValuationModel):
    name = "dcf"
    label = "Discounted Cash Flow"

    def default_params(self) -> dict[str, float]:
        return {"years": 5, "growth": 0.09, "decline": 0.05, "discount": 0.09, "terminal_growth": 0.025}

    def calculate(
        self,
        ticker: str,
        fiscal_period: str,
        financials: pd.DataFrame,
        price: PriceLookup,
        params: Mapping[str, float],
    ) -> ValuationResult:
        years = int(params["years"])
        growth, decline = params["growth"], params["decline"]
        discount, terminal_growth = params["discount"], params["terminal_growth"]
        if years < 1:
            raise AnalysisError("Project at least one year.")
        if discount <= terminal_growth:
            raise AnalysisError("The discount rate must exceed the terminal growth rate.")

        year, quarter = split_fiscal_period(fiscal_period)
        rows = financials[financials["qtrs"] == "0"]
        end = period_end(rows, year, quarter)
        latest = rows[rows["data_date"] == end]  # every single-quarter row on that date, whatever its period
        fiscal_period = f"{latest['fy'].iloc[0]}{latest['fq'].iloc[0]}"
        price_date = parse_data_date(end)
        current_price = round(price(ticker, price_date), 2)

        cash_rows = _matching(latest, "BS", (CASH,))
        if cash_rows.empty:
            cash_rows = _matching(latest, "CF", (CASH_END_OF_PERIOD,))
        cash = _first(cash_rows, "cash and cash equivalents", fiscal_period)
        debt = _first(_matching(latest, "BS", LONG_TERM_DEBT), "long-term debt", fiscal_period)
        # KNOWN-BUG(DCF-TTM): one quarter's flows, not the trailing twelve months.
        operating = _first(_matching(latest, "CF", OPERATING_CASH_FLOW), "operating cash flow", fiscal_period)
        capex = _first(_matching(latest, "CF", CAPEX), "capital expenditures", fiscal_period)
        shares = _first(_matching(latest, "BS", (SHARES,)), "shares outstanding", fiscal_period)

        fcf = operating - capex
        steps = [
            Step("Cash and cash equivalents", cash),
            Step("Long-term debt", debt),
            Step("Operating cash flow", operating),
            Step("Capital expenditures", capex),
            Step("Shares outstanding", shares),
            Step("Year 0 free cash flow", fcf, f"{operating} - {capex}"),
        ]
        present_values = []
        for yr in range(1, years + 1):
            if yr == 1:
                formula = f"{fcf} * (1 + {growth})"
                fcf = round(fcf * (1 + growth), 2)
            else:
                formula = f"{fcf} * (1 + ({growth} * ((1 - {decline})^({yr} - 1))))"
                fcf = round(fcf * (1 + growth * (1 - decline) ** (yr - 1)), 2)
            present = round(fcf / (1 + discount) ** yr, 2)
            present_values.append(present)
            steps.append(Step(f"Year {yr} free cash flow", fcf, formula))
            steps.append(Step(f"Year {yr} present value", present, f"{fcf} / ((1 + {discount})^({yr}))"))

        terminal = round(fcf * (1 + terminal_growth) / (discount - terminal_growth), 2)
        # KNOWN-BUG(DCF-TV): discounted over years - 1 periods (the formula text shows `years`).
        terminal_present = round(terminal / (1 + discount) ** (years - 1), 2)
        steps.append(Step("Terminal value", terminal, f"({fcf} * (1 + {terminal_growth})) / ({discount} - {terminal_growth})"))
        steps.append(Step("Terminal present value", terminal_present, f"{terminal} / ((1 + {discount})^({years}))"))

        total_present = round(sum([*present_values, terminal_present]), 2)
        company_value = round(total_present + cash - debt, 2)
        steps.append(Step("Company value", company_value, f"{total_present} (present values) + {cash} (cash) - {debt} (debt)"))
        intrinsic = company_value / shares
        steps.append(Step("Value per share", intrinsic, f"{company_value} / {shares}"))

        exit_terminal = present_values[-1] * EXIT_MULTIPLE
        flows_present = round(sum(present_values), 2)
        exit_value = round(exit_terminal + flows_present + cash - debt, 2)
        steps.append(Step(
            "Company value, exit multiple",
            exit_value,
            f"{exit_terminal} (final-year present value x {EXIT_MULTIPLE}) + {flows_present} (present values) + {cash} (cash) - {debt} (debt)",
        ))
        steps.append(Step("Value per share, exit multiple", round(exit_value / shares, 2), f"{exit_value} / {shares}"))

        return ValuationResult(self.name, ticker, fiscal_period, price_date, current_price, intrinsic, tuple(steps))


def _matching(rows: pd.DataFrame, stmt: str, names: Sequence[str]) -> pd.DataFrame:
    """Rows named any of `names`.

    KNOWN-BUG(TAG-OR): only the first name is restricted to `stmt`; the rest match any statement.
    """
    first, *rest = names
    return rows[((rows["stmt"] == stmt) & (rows["tag_renamed"] == first)) | rows["tag_renamed"].isin(rest)]


def _first(rows: pd.DataFrame, what: str, fiscal_period: str) -> float:
    value = rows["value"].iloc[0] if len(rows) else None
    if value is None or pd.isna(value):
        raise AnalysisError(f"No {what} reported for {fiscal_period}; the DCF model needs it.")
    return float(value)
