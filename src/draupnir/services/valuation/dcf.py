"""Discounted cash flow (DCF) model.

The method, step by step (names match the code below):

  1. Free cash flow today:
         fcf[0] = operating_cash_flow - capital_expenditures

  2. Project it forward. Growth starts at `growth_rate` and shrinks by `growth_decline` each year:
         growth_in_year[y] = growth_rate * (1 - growth_decline) ** (y - 1)
         fcf[y]            = fcf[y - 1] * (1 + growth_in_year[y])          for y = 1 .. years

  3. Discount each projected year back to today:
         present_value[y] = fcf[y] / (1 + discount_rate) ** y

  4. Terminal value: everything after the last projected year, as a growing perpetuity:
         terminal_value         = fcf[years] * (1 + terminal_growth) / (discount_rate - terminal_growth)
         terminal_present_value = terminal_value / (1 + discount_rate) ** (years - 1)   <- KNOWN-BUG(DCF-TV)

  5. Company value and value per share:
         company_value   = sum(present_value) + terminal_present_value + cash - long_term_debt
         value_per_share = company_value / shares_outstanding

  6. Alternative ("exit multiple"): replace the terminal value with 12 x the last year's present value:
         exit_company_value = present_value[years] * 12 + sum(present_value) + cash - long_term_debt

Every intermediate amount is rounded to cents, at the same points as listed, so results can be
reproduced by hand from the step table the model returns.

Known flaws, kept until the fix pass (see CLAUDE.md):
  DCF-TTM     step 1 uses a single quarter's cash flows, not the trailing twelve months.
  DCF-TV      step 4 discounts the terminal value one year too few.
  CAPEX-TAGS  the capex line items include non-cash and net-of-proceeds concepts.
  TAG-OR      line-item matching only checks the statement for the first name in each list.
"""

from collections.abc import Mapping
from dataclasses import dataclass

import pandas as pd

from draupnir.models.errors import AnalysisError
from draupnir.models.financial import split_fiscal_period
from draupnir.models.valuation import Step, ValuationResult
from draupnir.services.valuation.base import PriceLookup, ValuationModel, cents, parse_data_date, period_end

EXIT_MULTIPLE = 12

# --- Which statement lines feed the model -------------------------------------------------------
# Lines are matched by display name: our curated name from services/tag_map.csv when there is one,
# otherwise the SEC tag split into words. Where several names are listed, the first row found wins.

CASH = "Cash And Cash Equivalents"                                  # balance sheet
CASH_END_OF_PERIOD = "Cash And Cash Equivalents (End Of Period)"    # cash flow statement, used if the above is missing

LONG_TERM_DEBT = (                                                  # balance sheet
    "Long-Term Debt (Non-current)",
    "Unsecured Long Term Debt",
    "Long Term Debt And Capital Lease Obligations",
    "Long Term Debt And Capital Lease Obligations Including Current Maturities",
    "Long Term Debt",
    "Long Term Debt And Finance Leases (Non-current)",
)

OPERATING_CASH_FLOW = (                                             # cash flow statement
    "Cash From Operating Activities",
    "Net Cash Provided By Used In Continuing Operations",
    "Net Cash Provided By Used In Operating Activities Continuing Operations",
)

# KNOWN-BUG(CAPEX-TAGS): "Incurred But Not Yet Paid" is not a cash payment, and "Net" /
# "Payments For Proceeds" items are net of asset sales. Only cash spent on assets belongs here.
CAPITAL_EXPENDITURES = (                                            # cash flow statement
    "Capital Expenditures",
    "Capital Expenditures Incurred But Not Yet Paid",
    "Payments To Acquire Productive Assets",
    "Capital Expenditures Net",
    "Payments For Proceeds From Productive Assets",
    "Payments To Acquire Machinery And Equipment",
    "Capital Expenditures Development Redevelopment",
    "Payments For Capital Improvements",
)

SHARES_OUTSTANDING = "Shares Outstanding"                           # balance sheet


@dataclass(frozen=True)
class DcfInputs:
    """The five figures the model takes from the company's statements, plus where they came from."""

    fiscal_period: str          # e.g. "2010Q3"
    period_end: str             # YYYYMMDD; the price is taken on this date
    cash: float
    long_term_debt: float
    operating_cash_flow: float
    capital_expenditures: float
    shares_outstanding: float


class DcfModel(ValuationModel):
    name = "dcf"
    label = "Discounted Cash Flow"

    def default_params(self) -> dict[str, float]:
        return {
            "years": 5,               # projection horizon
            "growth": 0.09,           # free cash flow growth in year 1
            "decline": 0.05,          # each later year's growth is this fraction lower than the year before
            "discount": 0.09,         # required annual return
            "terminal_growth": 0.025, # growth forever after the horizon; must be below `discount`
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
        growth_decline = params["decline"]
        discount_rate = params["discount"]
        terminal_growth = params["terminal_growth"]
        if years < 1:
            raise AnalysisError("Project at least one year.")
        if discount_rate <= terminal_growth:
            raise AnalysisError("The discount rate must exceed the terminal growth rate.")

        inputs = read_inputs(financials, fiscal_period)
        price_date = parse_data_date(inputs.period_end)
        current_price = cents(price(ticker, price_date))
        steps = [
            Step("Cash and cash equivalents", inputs.cash),
            Step("Long-term debt", inputs.long_term_debt),
            Step("Operating cash flow", inputs.operating_cash_flow),
            Step("Capital expenditures", inputs.capital_expenditures),
            Step("Shares outstanding", inputs.shares_outstanding),
        ]

        # Step 1: free cash flow today.
        # KNOWN-BUG(DCF-TTM): one quarter's cash flows; should be the sum of the last four quarters.
        fcf = inputs.operating_cash_flow - inputs.capital_expenditures
        steps.append(Step("Year 0 free cash flow", fcf, f"{inputs.operating_cash_flow} - {inputs.capital_expenditures}"))

        # Steps 2 and 3: grow, then discount, each projected year.
        present_values = []
        for year in range(1, years + 1):
            growth_in_year = growth_rate * (1 - growth_decline) ** (year - 1)
            previous_fcf = fcf
            fcf = cents(previous_fcf * (1 + growth_in_year))
            present_value = cents(fcf / (1 + discount_rate) ** year)
            present_values.append(present_value)
            steps.append(Step(
                f"Year {year} free cash flow", fcf,
                f"{previous_fcf} * (1 + {growth_rate} * (1 - {growth_decline})^({year} - 1))",
            ))
            steps.append(Step(f"Year {year} present value", present_value, f"{fcf} / (1 + {discount_rate})^{year}"))

        # Step 4: terminal value.
        terminal_value = cents(fcf * (1 + terminal_growth) / (discount_rate - terminal_growth))
        # KNOWN-BUG(DCF-TV): discounted over `years - 1` periods; it should be `years`, like the last
        # projected year it grows from. This overstates the terminal value by a factor (1 + discount_rate).
        terminal_present_value = cents(terminal_value / (1 + discount_rate) ** (years - 1))
        steps.append(Step(
            "Terminal value", terminal_value,
            f"{fcf} * (1 + {terminal_growth}) / ({discount_rate} - {terminal_growth})",
        ))
        steps.append(Step("Terminal present value", terminal_present_value, f"{terminal_value} / (1 + {discount_rate})^{years - 1}"))

        # Step 5: company value and value per share.
        sum_present_values = cents(sum(present_values) + terminal_present_value)
        company_value = cents(sum_present_values + inputs.cash - inputs.long_term_debt)
        value_per_share = company_value / inputs.shares_outstanding
        steps.append(Step(
            "Company value", company_value,
            f"{sum_present_values} (present values incl. terminal) + {inputs.cash} (cash) - {inputs.long_term_debt} (debt)",
        ))
        steps.append(Step("Value per share", value_per_share, f"{company_value} / {inputs.shares_outstanding}"))

        # Step 6: exit-multiple alternative, shown for comparison only.
        exit_terminal_value = present_values[-1] * EXIT_MULTIPLE
        sum_projected_present_values = cents(sum(present_values))
        exit_company_value = cents(
            exit_terminal_value + sum_projected_present_values + inputs.cash - inputs.long_term_debt
        )
        steps.append(Step(
            "Company value, exit multiple", exit_company_value,
            f"{exit_terminal_value} (year {years} present value x {EXIT_MULTIPLE}) + {sum_projected_present_values}"
            f" (present values) + {inputs.cash} (cash) - {inputs.long_term_debt} (debt)",
        ))
        steps.append(Step(
            "Value per share, exit multiple", cents(exit_company_value / inputs.shares_outstanding),
            f"{exit_company_value} / {inputs.shares_outstanding}",
        ))

        return ValuationResult(
            self.name, ticker, inputs.fiscal_period, price_date, current_price, value_per_share, tuple(steps)
        )


def read_inputs(financials: pd.DataFrame, fiscal_period: str) -> DcfInputs:
    """Pick the model's five figures out of a company's statement rows.

    Only single-quarter and point-in-time rows (`qtrs == "0"`) are used. The figures come from every
    such row dated on the requested period's end date.
    """
    year, quarter = split_fiscal_period(fiscal_period)
    single_quarter = financials[financials["qtrs"] == "0"]
    end = period_end(single_quarter, year, quarter)
    on_end_date = single_quarter[single_quarter["data_date"] == end]
    first_row = on_end_date.iloc[0]
    found_period = f"{first_row['fy']}{first_row['fq']}"

    cash_rows = _rows_named(on_end_date, "BS", [CASH])
    if cash_rows.empty:
        cash_rows = _rows_named(on_end_date, "CF", [CASH_END_OF_PERIOD])

    shares = _first_value(_rows_named(on_end_date, "BS", [SHARES_OUTSTANDING]), "shares outstanding", found_period)
    if shares == 0:
        raise AnalysisError(f"Shares outstanding is reported as 0 for {found_period}; no per-share value.")

    return DcfInputs(
        fiscal_period=found_period,
        period_end=end,
        cash=_first_value(cash_rows, "cash and cash equivalents", found_period),
        long_term_debt=_first_value(_rows_named(on_end_date, "BS", LONG_TERM_DEBT), "long-term debt", found_period),
        operating_cash_flow=_first_value(
            _rows_named(on_end_date, "CF", OPERATING_CASH_FLOW), "operating cash flow", found_period
        ),
        capital_expenditures=_first_value(
            _rows_named(on_end_date, "CF", CAPITAL_EXPENDITURES), "capital expenditures", found_period
        ),
        shares_outstanding=shares,
    )


def _rows_named(rows: pd.DataFrame, statement: str, names: list[str] | tuple[str, ...]) -> pd.DataFrame:
    """Rows whose display name is one of `names`, in table order.

    KNOWN-BUG(TAG-OR): only the first name must be on `statement`; the other names match rows on
    any statement. E.g. a "Long Term Debt" row on the cash flow statement counts as balance-sheet debt.
    """
    first_name, other_names = names[0], list(names[1:])
    first_name_on_statement = (rows["stmt"] == statement) & (rows["tag_renamed"] == first_name)
    other_name_anywhere = rows["tag_renamed"].isin(other_names)
    return rows[first_name_on_statement | other_name_anywhere]


def _first_value(rows: pd.DataFrame, description: str, fiscal_period: str) -> float:
    """Value of the first row, or an error naming the missing figure."""
    value = rows["value"].iloc[0] if len(rows) else None
    if value is None or pd.isna(value):
        raise AnalysisError(f"No {description} reported for {fiscal_period}; the DCF model needs it.")
    return float(value)

