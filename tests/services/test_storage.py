import pandas as pd
import pytest

from draupnir.models.errors import AnalysisError
from draupnir.services import storage


def test_round_trip(tmp_path):
    conn = storage.connect(tmp_path / "db.sqlite")
    assert storage.latest_quarter(conn) is None
    storage.replace_companies(conn, pd.DataFrame([{"ein_id": "7", "name": "X'; DROP TABLE company;--", "fy": "2010", "edgar_year": "2010Q1"}]))
    storage.append_financials(conn, pd.DataFrame([{"ein_id": "7", "fy": "2010", "stmt": "IS", "value": 1.5, "qtrs": "0"}]))
    assert storage.latest_quarter(conn) == "2010Q1"
    assert storage.read_companies(conn)["name"].tolist() == ["X'; DROP TABLE company;--"]
    assert storage.read_financials(conn, 2009)["value"].tolist() == [1.5]
    assert storage.read_financials(conn, 2011).empty


@pytest.fixture
def conn(tmp_path):
    conn = storage.connect(tmp_path / "db.sqlite")
    storage.replace_companies(conn, pd.DataFrame([
        {"ein_id": "1", "instance": "abc", "name": "ABC OLD", "fy": "2009", "fp": "FY"},
        {"ein_id": "2", "instance": "abc", "name": "ABC NEW", "fy": "2010", "fp": "Q2"},
    ]))
    rows = [("BS", "FY", "0"), ("IS", "FY", "4"), ("IS", "FY", "0"), ("IS", "Q4", "0"), ("IS", "Q4", "4"), ("IS", "Q3", "0")]
    storage.append_financials(conn, pd.DataFrame(
        [{"ein_id": "1", "fy": "2009", "stmt": s, "fq": fq, "qtrs": q, "value": 1.0} for s, fq, q in rows]
    ))
    return conn


def test_find_company_takes_first_period_at_or_after_request(conn):
    assert storage.find_company(conn, "abc", "2009Q3")["ein_id"] == 1   # FY counts as Q4
    assert storage.find_company(conn, "abc", "2010Q1")["name"] == "ABC NEW"
    with pytest.raises(AnalysisError, match="No data loaded"):
        storage.find_company(conn, "abc", "2010Q3")
    with pytest.raises(AnalysisError, match="not in the database"):
        storage.find_company(conn, "abc' OR '1'='1", "2009Q1")


def test_read_period_statements(conn):
    q4 = storage.read_period_statements(conn, "1", "2009", "Q4")
    assert sorted(zip(q4["stmt"], q4["fq"], q4["qtrs"])) == [
        ("BS", "FY", "0"), ("IS", "FY", "0"), ("IS", "FY", "4"), ("IS", "Q4", "0")]
    q3 = storage.read_period_statements(conn, "1", "2009", "Q3")
    assert sorted(zip(q3["fq"], q3["qtrs"])) == [("FY", "0"), ("FY", "0"), ("Q3", "0")]
