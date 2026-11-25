"""Statement tables -> JSON and CSV."""

import pandas as pd

from draupnir.models.financial import FINANCIAL_COLUMNS

EXPORT_COLUMNS = [c for c in FINANCIAL_COLUMNS if c != "ein_id"]


def statements_csv(statements: pd.DataFrame) -> str:
    return statements[EXPORT_COLUMNS].to_csv(index=False)


def statements_json(company: dict, ticker: str, fiscal_period: str, statements: pd.DataFrame) -> dict:
    """Request summary plus one list per column."""
    address = (
        f"{company['bas1']}, {company['cityba']}, {company['stprba']} {company['countryba']} {company['zipba']}"
    )
    frame = statements[EXPORT_COLUMNS].astype(object)
    frame = frame.where(frame.notna(), None)
    return {
        "request_summary": {
            "company": company["name"],
            "address": address,
            "ticker": ticker,
            "ein": str(company["ein_id"]),
            "fiscal_period": fiscal_period,
        },
        "financials": {column: frame[column].tolist() for column in EXPORT_COLUMNS},
    }
