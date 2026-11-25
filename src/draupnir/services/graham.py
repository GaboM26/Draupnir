"""Benjamin Graham's stock criteria, as summarized by GrahamValue's quick reference
(The Intelligent Investor, chapters 14 and 15).

All figures are annual: the fiscal-year (10-K) values. "t" is the latest fiscal year loaded.
EPS is diluted EPS; "eps3" is the average EPS of years t-2, t-1, t.

DEFENSIVE (chapter 14). Quality criteria, which decide the grade:
  1    Sales in year t >= $500 million                 (Graham's $100M, inflation-adjusted per GrahamValue)
  2-A  Current assets >= 2 x current liabilities
  2-B  Long-term debt <= net current assets            (net current assets = current assets - current liabilities)
  3    Net income > 0 in each of the 10 years t-9 .. t
  4    Dividends paid in each of the 20 years t-19 .. t
  5    EPS growth >= 1/3 over ten years, using three-year averages:
           average(EPS t-2 .. t) >= 4/3 x average(EPS t-9 .. t-7)
  +    Graham Number > 0
Price criteria:
  6    Price <= 15 x eps3
  7    Price <= 1.5 x book value per share
  6x7  Graham's rule of thumb, which lets a low P/E offset a high P/B: P/E x P/B <= 22.5,
       i.e. price <= Graham Number
  Graham Number = sqrt(22.5 x eps3 x book value per share)

ENTERPRISING (chapter 15). Quality criteria:
  1-A  Current assets >= 1.5 x current liabilities
  1-B  Long-term debt <= 1.1 x net current assets
  2    Net income > 0 in each of the 5 years t-4 .. t
  3    Some dividend paid in year t
  4    EPS in year t > EPS in year t-4                   (GrahamValue's reading of "more than 1966")
  +    Serenity Number > 0
Price criteria:
  -    P/E under 10: price < 10 x eps3
  5    Price < 1.2 x net tangible assets per share
  Serenity Number = sqrt(12 x eps3 x tangible book value per share)   (GrahamValue's derivation)

Per share: book value = stockholders' equity / shares outstanding; tangible book value = (equity -
goodwill - other intangibles) / shares outstanding; all from the year-t balance sheet.

Each criterion is Pass, Fail, or Not enough data. A multi-year test fails as soon as one reported year
fails it; it passes only when every year in its window is reported. Choice: a fiscal year with an
income or cash flow statement but no dividend line counts as paying no dividend.

Not applied (stated on the page): Graham's substitute tests for utilities and financial companies,
the interest-rate adjustment of intrinsic value, and GrahamValue's NCAV (net-net) grade.
"""

import math
from collections.abc import Callable
from datetime import date

import pandas as pd

from draupnir.models.errors import AnalysisError
from draupnir.models.graham import Criterion, GrahamCategory, GrahamRating, Outcome
from draupnir.services.valuation.base import PriceLookup, parse_data_date

MIN_SALES = 500_000_000

# --- Which statement lines are used ---------------------------------------------------------------
# By display name (services/tag_map.csv, else the SEC tag split into words). For each year the first
# name in a list that has a value is used.

SALES = ("Total Revenue", "Net Sales", "Revenue From Contract With Customer Excluding Assessed Tax",
         "Sales Revenue Goods Net", "Sales Revenue Services Net")                                   # IS
# "Profit Loss" includes minority interests; used only when no net income line is reported.
NET_INCOME = ("Net Income", "Net Income Loss Available To Common Stockholders Basic", "Profit Loss")  # IS
EPS = ("EPS (Diluted)", "EPS (Basic)")                                                             # IS
DIVIDENDS_PER_SHARE = ("Dividends Per Share", "Common Stock Dividends Per Share Cash Paid")         # IS
DIVIDENDS_PAID = ("Dividends Paid", "Payments Of Dividends Common Stock")                          # CF
CURRENT_ASSETS = ("Total Current Assets",)                                                         # BS
CURRENT_LIABILITIES = ("Total Current Liabilities",)                                               # BS
# Non-current debt first; the last two include the current portion and are used only as a fallback.
LONG_TERM_DEBT = ("Long-Term Debt (Non-current)", "Long Term Debt And Capital Lease Obligations",
                  "Unsecured Long Term Debt", "Long Term Debt",
                  "Long Term Debt And Capital Lease Obligations Including Current Maturities")     # BS
EQUITY = ("Total Stockholders' Equity",
          "Stockholders Equity Including Portion Attributable To Noncontrolling Interest")        # BS
GOODWILL = ("Goodwill",)                                                                           # BS
INTANGIBLES = ("Intangible Assets, Net",)                                                          # BS
SHARES = ("Shares Outstanding",)                                                                   # BS

UTILITY_SIC = range(4900, 5000)
FINANCIAL_SIC = range(6000, 6800)


def rate(ticker: str, company_name: str, sic: int | None, financials: pd.DataFrame, price: PriceLookup) -> GrahamRating:
    annual = AnnualFigures(financials)
    t = annual.latest_year
    notes = []

    current_price, price_date = None, annual.balance_sheet_date(t)
    if price_date is not None:
        try:
            current_price = round(price(ticker, price_date), 2)
        except AnalysisError as exc:
            notes.append(f"No price, so the price criteria are not evaluated: {exc}")

    # Balance sheet, year t.
    current_assets = annual.value(CURRENT_ASSETS, "BS", t)
    current_liabilities = annual.value(CURRENT_LIABILITIES, "BS", t)
    net_current_assets = _minus(current_assets, current_liabilities)
    long_term_debt = annual.value(LONG_TERM_DEBT, "BS", t)
    if long_term_debt is None and annual.has_balance_sheet(t):
        # Choice: no debt line on a filed balance sheet is read as no long-term debt.
        long_term_debt = 0.0
        notes.append(f"No long-term debt line in FY{t}; counted as zero.")
    shares = annual.value(SHARES, "BS", t)
    equity = annual.value(EQUITY, "BS", t)
    tangible_equity = _minus(equity, (annual.value(GOODWILL, "BS", t) or 0.0) + (annual.value(INTANGIBLES, "BS", t) or 0.0))
    book_per_share = _per_share(equity, shares)
    tangible_book_per_share = _per_share(tangible_equity, shares)

    # Income statement history.
    dividends = annual.dividends_by_year()
    net_income = annual.by_year(NET_INCOME, "IS")
    eps = annual.by_year(EPS, "IS")
    eps3 = _average([eps.get(y) for y in (t - 2, t - 1, t)])
    graham_number = _sqrt_of_product(22.5, eps3, book_per_share)
    serenity_number = _sqrt_of_product(12, eps3, tangible_book_per_share)

    defensive = GrahamCategory(
        name="Defensive",
        quality=(
            _at_least("1", "Annual sales of at least $500 million", annual.value(SALES, "IS", t), MIN_SALES, f"FY{t} sales", _usd),
            _ratio_at_least("2-A", "Current assets at least 2 x current liabilities", current_assets, current_liabilities, 2),
            _debt_within("2-B", "Long-term debt no more than net current assets", long_term_debt, net_current_assets, 1),
            _every_year("3", "Positive earnings in each of the past 10 years", net_income, t, 10, lambda v: v > 0),
            _every_year("4", "Dividends paid in each of the past 20 years", dividends, t, 20, lambda v: v > 0),
            _eps_growth(eps, t),
            _positive("+", "Graham Number is positive", graham_number, eps3, book_per_share),
        ),
        price=(
            _price_at_most("6", "Price at most 15 x average EPS of the past 3 years", current_price, eps3, 15, "3-year average EPS"),
            _price_at_most("7", "Price at most 1.5 x book value per share", current_price, book_per_share, 1.5, "book value per share"),
            _price_at_most("6x7", "P/E x P/B at most 22.5 (price at most the Graham Number)", current_price, graham_number, 1, "Graham Number"),
        ),
        intrinsic_value_name="Graham Number",
        intrinsic_value=graham_number,
        intrinsic_value_formula=f"sqrt(22.5 x {_num(eps3)} (3-year avg EPS) x {_num(book_per_share)} (book value per share))",
        current_price=current_price,
    )
    enterprising = GrahamCategory(
        name="Enterprising",
        quality=(
            _ratio_at_least("1-A", "Current assets at least 1.5 x current liabilities", current_assets, current_liabilities, 1.5),
            _debt_within("1-B", "Long-term debt no more than 110% of net current assets", long_term_debt, net_current_assets, 1.1),
            _every_year("2", "No deficit in the past 5 years", net_income, t, 5, lambda v: v > 0),
            _current_dividend(annual, t),
            _eps_above_four_years_ago(eps, t),
            _positive("+", "Serenity Number is positive", serenity_number, eps3, tangible_book_per_share),
        ),
        price=(
            _price_below("-", "P/E under 10 (on 3-year average EPS)", current_price, eps3, 10, "3-year average EPS"),
            _price_below("5", "Price under 120% of net tangible assets per share", current_price, tangible_book_per_share, 1.2, "tangible book per share"),
        ),
        intrinsic_value_name="Serenity Number",
        intrinsic_value=serenity_number,
        intrinsic_value_formula=f"sqrt(12 x {_num(eps3)} (3-year avg EPS) x {_num(tangible_book_per_share)} (tangible book per share))",
        current_price=current_price,
    )

    if sic in UTILITY_SIC or sic in FINANCIAL_SIC:
        kind = "a utility" if sic in UTILITY_SIC else "a financial company"
        notes.append(f"SIC {sic} marks this as {kind}; Graham used different size and balance-sheet tests "
                     "for these, which are not applied here.")
    notes.append("Intrinsic values are not adjusted for current interest rates.")
    notes.append("KNOWN-BUG(PRICE-SPLIT): prices are split-adjusted but per-share figures are as reported, "
                 "so price tests are wrong for companies that split their stock after this fiscal year.")

    return GrahamRating(
        ticker=ticker,
        company_name=company_name,
        fiscal_year=t,
        years_loaded=tuple(sorted(annual.years)),
        price_date=price_date,
        current_price=current_price,
        defensive=defensive,
        enterprising=enterprising,
        notes=tuple(notes),
    )


class AnnualFigures:
    """A company's fiscal-year (10-K) figures, by fiscal year.

    Annual rows are those filed for fiscal period "FY": full-year values (qtrs "4") on the income
    and cash flow statements, and every balance-sheet row (year-end values; stored with qtrs "0" on a
    first load and "4" afterwards). When a year was filed more than once (e.g. an amended 10-K), the
    last row loaded wins.
    """

    def __init__(self, financials: pd.DataFrame):
        fy_rows = financials[(financials["fq"] == "FY") & financials["value"].notna()]
        flows = fy_rows[fy_rows["stmt"].isin(["IS", "CF"]) & (fy_rows["qtrs"] == "4")]
        balances = fy_rows[fy_rows["stmt"] == "BS"]
        self._rows = pd.concat([flows, balances])
        self._rows = self._rows.assign(fy=self._rows["fy"].astype(int))
        if self._rows.empty:
            raise AnalysisError("No annual (10-K) figures loaded for this company.")
        self.years = set(self._rows["fy"])
        self.latest_year = max(self.years)

    def by_year(self, names: tuple[str, ...], statement: str) -> dict[int, float]:
        """{fiscal year: value} using, for each year, the first of `names` reported that year."""
        rows = self._rows[self._rows["stmt"] == statement]
        values: dict[int, float] = {}
        for name in names:
            for year, group in rows[rows["tag_renamed"] == name].groupby("fy"):
                values.setdefault(int(year), float(group["value"].iloc[-1]))
        return values

    def value(self, names: tuple[str, ...], statement: str, year: int) -> float | None:
        return self.by_year(names, statement).get(year)

    def years_with(self, *statements: str) -> set[int]:
        """Fiscal years with at least one annual row on any of `statements`."""
        return set(self._rows.loc[self._rows["stmt"].isin(statements), "fy"])

    def has_balance_sheet(self, year: int) -> bool:
        return bool(((self._rows["stmt"] == "BS") & (self._rows["fy"] == year)).any())

    def balance_sheet_date(self, year: int) -> date | None:
        dates = self._rows.loc[(self._rows["stmt"] == "BS") & (self._rows["fy"] == year), "data_date"].dropna()
        return parse_data_date(dates.max()) if len(dates) else None

    def dividends_by_year(self) -> dict[int, float]:
        """{fiscal year: dividend measure}, 0 for years with an income or cash flow statement
        but no dividend line.

        Choice: a year counts as paying when either dividends per share or dividends paid (cash flow)
        is positive; the value is whichever is found first, per share preferred.
        """
        per_share = self.by_year(DIVIDENDS_PER_SHARE, "IS")
        paid = self.by_year(DIVIDENDS_PAID, "CF")
        return {year: per_share.get(year) or paid.get(year) or 0.0 for year in self.years_with("IS", "CF")}

    def describe_dividend(self, year: int) -> str:
        per_share = self.value(DIVIDENDS_PER_SHARE, "IS", year)
        if per_share:
            return f"FY{year} dividends per share: {per_share:,.2f}"
        paid = self.value(DIVIDENDS_PAID, "CF", year)
        if paid:
            return f"FY{year} dividends paid: {_usd(paid)}"
        return f"FY{year}: no dividend reported"


# --- Criteria ---------------------------------------------------------------------------------------


def _at_least(number, rule, value, minimum, label, fmt) -> Criterion:
    if value is None:
        return Criterion(number, rule, f"{label}: not reported", Outcome.UNKNOWN)
    return Criterion(number, rule, f"{label}: {fmt(value)}", _outcome(value >= minimum))


def _ratio_at_least(number, rule, current_assets, current_liabilities, multiple) -> Criterion:
    if current_assets is None or current_liabilities is None:
        return Criterion(number, rule, "Current assets or liabilities not reported", Outcome.UNKNOWN)
    found = f"{_usd(current_assets)} vs {multiple} x {_usd(current_liabilities)} = {_usd(multiple * current_liabilities)}"
    return Criterion(number, rule, found, _outcome(current_assets >= multiple * current_liabilities))


def _debt_within(number, rule, debt, net_current_assets, multiple) -> Criterion:
    if debt is None or net_current_assets is None:
        return Criterion(number, rule, "Debt or net current assets not reported", Outcome.UNKNOWN)
    found = f"debt {_usd(debt)} vs {multiple} x net current assets {_usd(net_current_assets)}"
    return Criterion(number, rule, found, _outcome(debt <= multiple * net_current_assets))


def _every_year(number, rule, values: dict[int, float], t: int, span: int,
                passes: Callable[[float], bool]) -> Criterion:
    """`passes` must hold in each of the `span` years ending at t. Years with no value are unknown."""
    window = range(t - span + 1, t + 1)
    failing = [y for y in window if y in values and not passes(values[y])]
    missing = [y for y in window if y not in values]
    if failing:
        return Criterion(number, rule, f"Not met in FY{', FY'.join(map(str, failing))}", Outcome.FAIL)
    if missing:
        reported = span - len(missing)
        return Criterion(number, rule, f"Met in all {reported} reported years; {len(missing)} of {span} years not loaded or not reported", Outcome.UNKNOWN)
    return Criterion(number, rule, f"Met in every year FY{window[0]}-FY{t}", Outcome.PASS)


def _eps_growth(eps: dict[int, float], t: int) -> Criterion:
    rule = "EPS up at least one-third over 10 years (3-year averages at start and end)"
    start = _average([eps.get(y) for y in (t - 9, t - 8, t - 7)])
    end = _average([eps.get(y) for y in (t - 2, t - 1, t)])
    if start is None or end is None:
        return Criterion("5", rule, f"Needs EPS for FY{t - 9}-FY{t - 7} and FY{t - 2}-FY{t}", Outcome.UNKNOWN)
    found = f"average EPS {_num(start)} (FY{t - 9}-{t - 7}) -> {_num(end)} (FY{t - 2}-{t})"
    if start <= 0:
        return Criterion("5", rule, found + "; growth from a non-positive base is not measurable", Outcome.FAIL)
    return Criterion("5", rule, found, _outcome(end >= start * 4 / 3))


def _current_dividend(annual: AnnualFigures, t: int) -> Criterion:
    rule = "Some current dividend"
    dividends = annual.dividends_by_year()
    if t not in dividends:
        return Criterion("3", rule, f"No FY{t} income or cash flow statement loaded", Outcome.UNKNOWN)
    return Criterion("3", rule, annual.describe_dividend(t), _outcome(dividends[t] > 0))


def _eps_above_four_years_ago(eps: dict[int, float], t: int) -> Criterion:
    rule = "Last year's EPS above EPS four years earlier"
    now, then = eps.get(t), eps.get(t - 4)
    if now is None or then is None:
        return Criterion("4", rule, f"Needs EPS for FY{t - 4} and FY{t}", Outcome.UNKNOWN)
    return Criterion("4", rule, f"EPS {_num(then)} (FY{t - 4}) -> {_num(now)} (FY{t})", _outcome(now > then))


def _positive(number, rule, intrinsic_value, eps3, per_share_book) -> Criterion:
    if eps3 is None or per_share_book is None:
        return Criterion(number, rule, "Needs 3 years of EPS and a book value per share", Outcome.UNKNOWN)
    if intrinsic_value is None:
        return Criterion(number, rule, f"3-year avg EPS {_num(eps3)}, book per share {_num(per_share_book)}: not both positive", Outcome.FAIL)
    return Criterion(number, rule, _num(intrinsic_value), Outcome.PASS)


def _price_at_most(number, rule, price, base, multiple, base_label) -> Criterion:
    return _price_test(number, rule, price, base, multiple, base_label, lambda p, limit: p <= limit)


def _price_below(number, rule, price, base, multiple, base_label) -> Criterion:
    return _price_test(number, rule, price, base, multiple, base_label, lambda p, limit: p < limit)


def _price_test(number, rule, price, base, multiple, base_label, within) -> Criterion:
    if price is None or base is None:
        return Criterion(number, rule, f"Needs a price and the {base_label}", Outcome.UNKNOWN)
    limit = multiple * base
    found = f"price {_num(price)} vs limit {_num(limit)}" + ("" if multiple == 1 else f" ({multiple} x {_num(base)} {base_label})")
    return Criterion(number, rule, found, _outcome(within(price, limit)))


# --- Arithmetic and formatting ----------------------------------------------------------------------


def _outcome(passed: bool) -> Outcome:
    return Outcome.PASS if passed else Outcome.FAIL


def _minus(a: float | None, b: float | None) -> float | None:
    return None if a is None or b is None else a - b


def _per_share(amount: float | None, shares: float | None) -> float | None:
    return None if amount is None or not shares else amount / shares


def _average(values: list[float | None]) -> float | None:
    """Mean, or None if any value is missing."""
    if any(v is None for v in values):
        return None
    return sum(values) / len(values)


def _sqrt_of_product(constant: float, eps3: float | None, book: float | None) -> float | None:
    """sqrt(constant x eps3 x book), or None unless both are positive."""
    if eps3 is None or book is None or eps3 <= 0 or book <= 0:
        return None
    return math.sqrt(constant * eps3 * book)


def _usd(amount: float) -> str:
    for size, suffix in ((1e9, "B"), (1e6, "M")):
        if abs(amount) >= size:
            return f"${amount / size:,.2f}{suffix}"
    return f"${amount:,.0f}"


def _num(value: float | None) -> str:
    return "n/a" if value is None else f"{value:,.2f}"
