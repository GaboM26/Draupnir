"""SQLite schema, connection, and read/write for companies and financial rows.

The only module that writes SQL. Every query is parameterized.
"""

import sqlite3
from pathlib import Path

import pandas as pd

from draupnir.models.company import COMPANY_COLUMNS
from draupnir.models.errors import AnalysisError
from draupnir.models.financial import FINANCIAL_COLUMNS

_COMPANY_INTEGER_COLUMNS = {
    "ein_id", "cik_id", "sic", "changed", "wksi", "fye", "period", "fy", "filed", "prevrpt", "detail", "nciks",
}

SCHEMA = f"""
CREATE TABLE IF NOT EXISTS company (
    {", ".join(f"{c} {'INTEGER' if c in _COMPANY_INTEGER_COLUMNS else 'TEXT'}" for c in COMPANY_COLUMNS)},
    PRIMARY KEY (ein_id)
);
CREATE TABLE IF NOT EXISTS financial (
    record_id INTEGER PRIMARY KEY,
    {", ".join(f"{c} {'REAL' if c == 'value' else 'TEXT'}" for c in FINANCIAL_COLUMNS)},
    FOREIGN KEY (ein_id) REFERENCES company (ein_id)
);
CREATE INDEX IF NOT EXISTS ix_financial_ein_id ON financial (ein_id);
CREATE INDEX IF NOT EXISTS ix_company_instance ON company (instance);
"""


def connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.executescript(SCHEMA)
    return conn


def latest_quarter(conn: sqlite3.Connection) -> str | None:
    """Most recent data set quarter loaded, e.g. '2009Q4', or None for an empty database."""
    return conn.execute("SELECT MAX(edgar_year) FROM company").fetchone()[0]


def read_companies(conn: sqlite3.Connection) -> pd.DataFrame:
    companies = pd.read_sql_query("SELECT * FROM company", conn)
    companies["ein_id"] = companies["ein_id"].astype(str)
    return companies


def read_financials(conn: sqlite3.Connection, min_fiscal_year: int) -> pd.DataFrame:
    return pd.read_sql_query(
        "SELECT * FROM financial WHERE CAST(fy AS INTEGER) >= ?", conn, params=(min_fiscal_year,)
    )


# A company row's own fiscal period as sortable text: fy 2009 + fp 'FY' -> '2009Q4'.
_COMPANY_PERIOD = "REPLACE(CAST(fy AS TEXT) || fp, 'FY', 'Q4')"


def list_companies(conn: sqlite3.Connection) -> pd.DataFrame:
    """Every company: name, ticker, latest loaded fiscal period."""
    return pd.read_sql_query(
        f"SELECT name, instance AS ticker, {_COMPANY_PERIOD} AS period FROM company ORDER BY name", conn
    )


def find_company(conn: sqlite3.Connection, ticker: str, period: str) -> dict:
    """The company row for `ticker` whose period is the first one at or after `period`.

    A ticker can appear under several EINs over time; each row's period says which one covers
    the request.
    """
    cursor = conn.execute(
        f"SELECT *, {_COMPANY_PERIOD} AS period_key FROM company WHERE instance = ? AND {_COMPANY_PERIOD} >= ? "
        "ORDER BY period_key, ein_id LIMIT 1",
        (ticker, period),
    )
    row = cursor.fetchone()
    if row is None:
        known = conn.execute("SELECT 1 FROM company WHERE instance = ? LIMIT 1", (ticker,)).fetchone()
        if known is None:
            raise AnalysisError(f"Ticker {ticker!r} is not in the database.")
        raise AnalysisError(f"No data loaded for {ticker} at or after {period}.")
    company = dict(zip((c[0] for c in cursor.description), row))
    del company["period_key"]
    return company


def find_latest_company(conn: sqlite3.Connection, ticker: str) -> dict:
    """The company row for `ticker` with the latest fiscal period."""
    cursor = conn.execute(
        f"SELECT *, {_COMPANY_PERIOD} AS period_key FROM company WHERE instance = ? "
        "ORDER BY period_key DESC, ein_id LIMIT 1",
        (ticker,),
    )
    row = cursor.fetchone()
    if row is None:
        raise AnalysisError(f"Ticker {ticker!r} is not in the database.")
    company = dict(zip((c[0] for c in cursor.description), row))
    del company["period_key"]
    return company


def read_company_financials(conn: sqlite3.Connection, ein_id: str) -> pd.DataFrame:
    """All statement rows of one company, in load order."""
    return pd.read_sql_query(
        "SELECT * FROM financial WHERE ein_id = ? ORDER BY record_id", conn, params=(str(ein_id),)
    )


def read_period_statements(conn: sqlite3.Connection, ein_id: str, year: str, quarter: str) -> pd.DataFrame:
    """The statement table shown for one fiscal period: single-quarter and point-in-time rows,
    plus the full-year rows when the quarter is Q4."""
    return pd.read_sql_query(
        "SELECT * FROM financial WHERE ein_id = ? AND fy = ? AND (fq = ? OR fq = 'FY') "
        "AND (qtrs = '0' OR (? = 'Q4' AND qtrs = '4' AND fq = 'FY')) ORDER BY record_id",
        conn,
        params=(str(ein_id), year, quarter, quarter),
    )


def replace_companies(conn: sqlite3.Connection, companies: pd.DataFrame) -> None:
    rows = _rows(companies, COMPANY_COLUMNS)
    placeholders = ", ".join("?" for _ in COMPANY_COLUMNS)
    with conn:
        conn.execute("DELETE FROM company")
        conn.executemany(f"INSERT INTO company ({', '.join(COMPANY_COLUMNS)}) VALUES ({placeholders})", rows)


def append_financials(conn: sqlite3.Connection, financials: pd.DataFrame) -> None:
    rows = _rows(financials, FINANCIAL_COLUMNS)
    placeholders = ", ".join("?" for _ in FINANCIAL_COLUMNS)
    with conn:
        conn.executemany(f"INSERT INTO financial ({', '.join(FINANCIAL_COLUMNS)}) VALUES ({placeholders})", rows)


def _rows(frame: pd.DataFrame, columns: tuple[str, ...]) -> list[tuple]:
    """DataFrame -> tuples with pandas missing values and empty strings stored as NULL."""
    frame = frame.reindex(columns=list(columns)).astype(object)
    frame = frame.where(frame.notna(), None).replace({"": None})
    return list(frame.itertuples(index=False, name=None))
