"""Raw SEC tables -> normalized company and financial rows. Pure pandas: no network, no database.

  clean_companies / apply_previous_tickers / carry_forward_companies  company rows per quarter
  build_statements                                                     balance sheet, income, cash flow rows
  to_quarterly                                                         single-quarter values from cumulative ones

Known bugs are marked KNOWN-BUG (see CLAUDE.md). Text columns such as `qtrs` use string
semantics ("0" = single quarter / instant).
"""

import re
from collections.abc import Callable
from importlib import resources

import pandas as pd

from draupnir.models.company import COMPANY_COLUMNS
from draupnir.models.financial import FINANCIAL_COLUMNS, STATEMENTS
from draupnir.models.sec_dataset import SecDataset

SharesLookup = Callable[[str, str], float | None]
"""(filing_url, report) -> shares outstanding scraped from the filing, or None."""

SHARES_TAG = "EntityCommonStockSharesOutstanding"
SHARES_TAG_SEC = "Entity Common Stock Shares Outstanding"
CASH_END_TAG = "CashAndCashEquivalentsEndingBalances"
NON_ADDITIVE_IS_TAGS = (  # Q4 values of these are never derived by subtracting Q3
    "Weighted Average Number Of Shares Outstanding Basic",
    "Weighted Average Number Of Diluted Shares Outstanding",
)
_CASH_END = re.compile(r"^Cash.+---.+[Ee][Nn][Dd]")
_CAMEL = re.compile(r"([a-z])([A-Z])")
_QUARTER = re.compile(r"^\d{4}Q[1-4]$")


def load_tag_map() -> pd.DataFrame:
    """Our SEC tag -> display name map (services/tag_map.csv)."""
    with resources.files("draupnir.services").joinpath("tag_map.csv").open(encoding="utf-8") as f:
        return pd.read_csv(f, dtype=str, keep_default_na=False)


def previous_quarter(quarter: str) -> str:
    if not _QUARTER.match(quarter):
        raise ValueError(f"quarter must look like 2009Q4, got {quarter!r}")
    year, q = int(quarter[:4]), int(quarter[5])
    return f"{year - 1}Q4" if q == 1 else f"{year}Q{q - 1}"


# --- companies -----------------------------------------------------------------------------


def normalize_submissions(sub: pd.DataFrame) -> pd.DataFrame:
    """Store EINs as integers-in-text ('000000000' -> '0'), as a numeric parse would."""
    sub = sub.copy()
    ein = pd.to_numeric(sub["ein"], errors="coerce")
    sub["ein"] = ein.map(lambda v: "" if pd.isna(v) else str(int(v)))
    return sub


def apply_previous_tickers(sub: pd.DataFrame, previous: pd.DataFrame) -> pd.DataFrame:
    """Reuse the last load's ticker where EIN and company name both match.

    Instance filenames change format between filings (e.g. 'a10-qq320196292019_htm.xml'),
    so a previously derived ticker is more reliable than re-deriving it.
    """
    if previous.empty:
        return sub
    prev = previous.drop_duplicates("ein_id").set_index("ein_id")
    current = sub[sub["ein"] != ""].drop_duplicates("ein")
    tickers = {
        ein: prev.at[ein, "instance"]
        for ein, name in zip(current["ein"], current["name"])
        if ein in prev.index and prev.at[ein, "name"] == name
    }
    sub = sub.copy()
    remap = sub["ein"].isin(tickers.keys())
    sub.loc[remap, "instance"] = sub.loc[remap, "ein"].map(tickers)
    return sub


def clean_companies(sub: pd.DataFrame, quarter: str) -> pd.DataFrame:
    """10-K/10-Q filers with a derivable ticker, one row per ticker, CIK, and EIN."""
    c = sub.copy()
    # KNOWN-BUG(TICKER-GUESS): ticker is the XBRL instance filename up to the first '-' or '_'.
    c["instance"] = c["instance"].str.replace(r"-.+", "", regex=True)
    c = c[~c["instance"].str.contains(r".xml", regex=True)]
    c["instance"] = c["instance"].str.replace(r"_.+", "", regex=True)
    c = c[~c["instance"].str.contains(r"[0-9]", regex=True)]
    c = c[c["form"].isin(["10-Q", "10-K"])]
    c = c.drop_duplicates("instance").drop_duplicates("cik").drop_duplicates("ein")
    c = c.rename(columns={"cik": "cik_id", "ein": "ein_id"})
    c = c[(c["ein_id"] != "") & (c["fy"] != "")]
    c["edgar_year"] = quarter
    return c.reindex(columns=list(COMPANY_COLUMNS)).reset_index(drop=True)


def carry_forward_companies(current: pd.DataFrame, previous: pd.DataFrame) -> pd.DataFrame:
    """Keep companies from earlier loads that did not file this quarter."""
    missing = previous[~previous["ein_id"].isin(current["ein_id"])]
    return pd.concat([current, missing.reindex(columns=list(COMPANY_COLUMNS))], ignore_index=True)


# --- statements ----------------------------------------------------------------------------


def build_statements(
    companies: pd.DataFrame,
    data: SecDataset,
    tag_map: pd.DataFrame,
    shares_lookup: SharesLookup | None = None,
) -> pd.DataFrame:
    """Latest income statement, balance sheet, and cash flow rows for each company's filing."""
    num_by_adsh = dict(tuple(data.num.groupby("adsh", sort=False)))
    pre_by_adsh = dict(tuple(data.pre.groupby("adsh", sort=False)))
    parts = []
    for stmt in STATEMENTS:
        mapping = tag_map.loc[tag_map["stmt"] == stmt, ["tag", "name", "line_item"]]
        rows = []
        for company in companies.itertuples(index=False):
            numbers = num_by_adsh.get(company.adsh)
            presentation = pre_by_adsh.get(company.adsh)
            if numbers is None or presentation is None:
                continue
            built = _build_statement(stmt, company, numbers, presentation, mapping, shares_lookup)
            if built is not None:
                rows.append(built)
        if not rows:
            continue
        part = pd.concat(rows, ignore_index=True)
        part = part.drop_duplicates(["ein_id", "tag_renamed", "value", "data_date"])
        parts.append(part[part["value"].notna()])
    if not parts:
        return pd.DataFrame(columns=list(FINANCIAL_COLUMNS))
    final = pd.concat(parts, ignore_index=True)
    for column in ("tag_sec", "label_sec", "tag_renamed"):
        final[column] = final[column].str.lstrip(" ")
    final = final[final["ein_id"].notna()]
    return final.sort_values("ein_id", kind="stable").reset_index(drop=True)


def _build_statement(stmt, company, numbers, presentation, mapping, shares_lookup):
    """One company's rows for one statement, or None when the filing does not qualify."""
    shares = None
    if stmt == "BS":
        loc = presentation[
            (presentation["stmt"] == "BS")
            | ((presentation["stmt"] == "CP") & (presentation["tag"] == SHARES_TAG))
        ]
        if loc.empty:
            return None
        shares = loc[loc["tag"] == SHARES_TAG]
        loc = loc[loc["stmt"] == "BS"]
    else:
        loc = presentation[presentation["stmt"] == stmt]
    if loc.empty:
        return None

    url = f"https://www.sec.gov/Archives/edgar/data/{company.cik_id}/{company.adsh.replace('-', '')}"
    loc = loc[loc["report"] == loc["report"].min()].copy()

    if stmt == "CF":
        # The cash line whose label mentions the period end becomes the ending balance,
        # valued at the latest date reported for that tag.
        is_end = (loc["tag"] + " --- " + loc["plabel"]).str.contains(_CASH_END)
        if is_end.any():
            end_tag = loc.loc[is_end, "tag"].iloc[0]
            matches = numbers[numbers["tag"] == end_tag]
            if matches.empty:
                return None
            matches = matches.sort_values("ddate", ascending=False, kind="stable")
            latest = matches.iloc[[0]].assign(tag=CASH_END_TAG)
            if len(matches) > 1:
                numbers = numbers[numbers["tag"] != end_tag]
            numbers = pd.concat([numbers, latest])
            loc.loc[is_end, "tag"] = CASH_END_TAG

    value_columns = ["tag", "ddate", "qtrs", "uom", "value"]
    merged = loc.merge(numbers[value_columns], on="tag", how="left").sort_values("tag", kind="stable")
    if merged["value"].isna().all():
        return None

    date = pd.NA
    if stmt == "CF":
        end_dates = merged.loc[merged["tag"] == CASH_END_TAG, "ddate"]
        if len(end_dates):
            date = end_dates.iloc[0]
    if pd.isna(date):
        date = merged["ddate"].max()
    if pd.isna(date):
        return None
    merged = merged[merged["ddate"].eq(date).fillna(False)]

    named = merged.merge(mapping, on="tag", how="left")
    named["tag"] = named["tag"].str.replace(_CAMEL, r"\1 \2", regex=True)
    named["tag_renamed"] = named["name"].fillna(named["tag"])
    named = named.sort_values("line", kind="stable")
    if stmt == "BS":
        named["tag_renamed"] = (
            named["tag_renamed"]
            .str.replace(r" Current$", " (Current)", regex=True)
            .str.replace(r" Noncurrent$", " (Non-current)", regex=True)
        )
    elif stmt == "CF":
        named["tag_renamed"] = (
            named["tag_renamed"]
            .str.replace(r"^Increase Decrease (I|i)n", "", regex=True)
            .str.replace(r"^Increase Decreasein", "", regex=True)
        )
        qtrs = named["qtrs"].astype(str)
        keep = {"4", "0"} if company.fp == "FY" else {company.fp.replace("Q", "", 1), "0"}
        named = named[qtrs.isin(keep)]
        if named.empty:
            return None

    out = pd.DataFrame(
        {
            "tag_sec": named["tag"],
            "label_sec": named["plabel"],
            "report": named["report"].astype(str),
            "line": named["line"].astype(str),
            "stmt": named["stmt"],
            "data_date": named["ddate"].astype(str),
            "fy": company.fy,
            "fq": company.fp,
            "qtrs": named["qtrs"].astype(str),
            "uom": named["uom"],
            "tag_renamed": named["tag_renamed"],
            "value": named["value"].astype(float),
            "line_item": named["line_item"],
            "url": url,
            "ein_id": company.ein_id,
        }
    )

    if stmt == "BS" and shares is not None:
        out = _append_shares_outstanding(out, shares, numbers[value_columns], company, url, shares_lookup)

    return _qualifies(stmt, out, company.fp)


def _append_shares_outstanding(out, shares, numbers, company, url, shares_lookup):
    shares = shares.merge(numbers, on="tag", how="left")
    if len(shares) != 1:
        return out
    row = shares.iloc[0]
    value = row["value"]
    already_present = (out["tag_sec"] == SHARES_TAG_SEC).sum()
    if pd.isna(value) and already_present == 0 and shares_lookup is not None:
        value = shares_lookup(url, str(row["report"]))
    extra = pd.DataFrame(
        [
            {
                "tag_sec": SHARES_TAG_SEC,
                "label_sec": row["plabel"],
                "report": str(row["report"]),
                "line": str(row["line"]),
                "stmt": "BS",
                "data_date": out["data_date"].iloc[0] if len(out) else None,
                "fy": company.fy,
                "fq": company.fp,
                "qtrs": "0",
                "uom": "shares",
                "tag_renamed": "Shares Outstanding",
                "value": float("nan") if value is None else float(value),
                "line_item": "yes",
                "url": url,
                "ein_id": company.ein_id,
            }
        ]
    )
    return pd.concat([out, extra], ignore_index=True)


def _qualifies(stmt, out, fp):
    """Per-statement sanity filters. Returns the (possibly narrowed) rows or None.

    Quarter comparisons are string comparisons.
    """
    if out.empty:
        return None
    max_qtrs = out["qtrs"].max()
    if stmt == "BS":
        if max_qtrs != "0":
            return None
        if "Q" in fp:
            q = fp.replace("Q", "", 1)
            if q <= "0" or q > "3":
                return None
        return out
    if fp == "FY":
        return None if max_qtrs != "4" else out
    if "Q" in fp:
        q = fp.replace("Q", "", 1)
        if q > "3" or q <= "0":
            return None
        single = "1" if stmt == "IS" else "0"
        out = out[out["qtrs"].isin([q, single])]
        return None if out.empty else out
    return out


# --- quarterly values ----------------------------------------------------------------------


def to_quarterly(final: pd.DataFrame, history: pd.DataFrame) -> pd.DataFrame:
    """Add single-quarter rows (qtrs "0") derived from cumulative figures.

    `history` holds earlier loads' financial rows (fiscal years >= this year - 2). Cash flow
    Q2/Q3 values are reported year-to-date and income statement Q4 only as the full year, so
    the prior quarter's cumulative value is subtracted. Fiscal-year filings are split into a
    "Q4" copy and an "FY" copy.
    """
    history = history[~history["qtrs"].isin(["0", "4"])]
    staged = []
    for _, rows in final.groupby("ein_id", sort=False):
        staged.extend(_stage_company(rows))
    if not staged:
        return pd.DataFrame(columns=list(FINANCIAL_COLUMNS))
    staged_df = pd.concat(staged, ignore_index=True)

    history_by_ein = {ein: rows for ein, rows in history.groupby("ein_id", sort=False)}
    adjusted = []
    for ein, rows in staged_df.groupby("ein_id", sort=False):
        derived = _derive_single_quarters(rows, history_by_ein.get(ein))
        if derived is not None:
            adjusted.append(derived)

    out = pd.concat([staged_df, *adjusted], ignore_index=True)
    out = out.drop_duplicates(["stmt", "ein_id", "tag_sec", "tag_renamed", "fy", "fq", "qtrs", "value"])
    for column in ("tag_sec", "label_sec", "tag_renamed"):
        out[column] = out[column].str.lstrip(" ")
    cf_q1 = out[(out["stmt"] == "CF") & (out["fq"] == "Q1") & (out["qtrs"] == "1")].assign(qtrs="0")
    out = pd.concat([out, cf_q1], ignore_index=True)
    return out.sort_values(["ein_id", "stmt", "fq", "line"], kind="stable").reset_index(drop=True)


def _stage_company(rows: pd.DataFrame) -> list[pd.DataFrame]:
    rows = rows[rows["tag_sec"].notna()].copy()
    if rows.empty:
        return []
    fq = rows["fq"].iloc[0]
    quarter = rows["qtrs"].max()
    if fq not in ("Q1", "Q2", "Q3", "FY"):
        return []
    if fq != "FY" and fq.replace("Q", "", 1) != quarter and quarter != "0":
        return []
    is_rows = rows.loc[rows["stmt"] == "IS", "qtrs"]
    cf_rows = rows.loc[rows["stmt"] == "CF", "qtrs"]
    is_quarter = is_rows.max() if len(is_rows) else None
    cf_quarter = cf_rows.max() if len(cf_rows) else None
    if quarter > "4":
        return []
    if is_quarter is not None and cf_quarter is not None and is_quarter != cf_quarter and cf_quarter != "0":
        return []
    if quarter == "4" and fq != "FY":
        return []
    fiscal_year = None
    if (quarter == "4" and fq == "FY") or (quarter == "0" and fq == "FY" and is_quarter is None):
        fiscal_year = rows[rows["qtrs"].isin(["0", "4"])].assign(fq="FY", qtrs="4")
        rows["fq"] = "Q4"
    rows.loc[(rows["stmt"] == "IS") & (rows["qtrs"] == "1"), "qtrs"] = "0"
    rows = rows.sort_values(["stmt", "line"], kind="stable")
    return [rows] if fiscal_year is None else [rows, fiscal_year]


def _derive_single_quarters(rows: pd.DataFrame, history: pd.DataFrame | None) -> pd.DataFrame | None:
    fq = rows["fq"].iloc[0]
    prior = {"Q2": "Q1", "Q3": "Q2", "Q4": "Q3"}.get(fq)
    if prior is None or history is None:
        return None
    history = history[(history["fy"] == rows["fy"].iloc[0]) & (history["fq"] == prior)]
    if history.empty:
        return None

    def prior_value(record, stmt, qtrs_ok):
        candidates = history[
            (history["tag_sec"] == record["tag_sec"])
            & (history["tag_renamed"] == record["tag_renamed"])
            & (history["stmt"] == stmt)
            & history["qtrs"].map(qtrs_ok)
        ]
        return candidates["value"].iloc[0] if len(candidates) == 1 else None

    records = rows.to_dict("records")
    for record in records:
        if record["fq"] != fq or record["qtrs"] == "0":
            continue
        if record["stmt"] == "CF":
            previous = prior_value(record, "CF", lambda q: q != "0")
        elif record["stmt"] == "IS" and fq == "Q4":
            if record["tag_sec"] in NON_ADDITIVE_IS_TAGS:
                continue
            same_line = [
                r["qtrs"] for r in records
                if r["fq"] == "Q4" and r["tag_sec"] == record["tag_sec"] and r["tag_renamed"] == record["tag_renamed"]
            ]
            if min(same_line) == "0":
                continue  # a Q4-only value was already reported
            previous = prior_value(record, "IS", lambda q: q == "3")
        else:
            continue
        if previous is None:
            continue
        record["value"] = record["value"] - previous
        record["qtrs"] = "0"
    return pd.DataFrame(records, columns=rows.columns)
