import pandas as pd
import pytest

from draupnir.api import create_app
from draupnir.config import Settings
from draupnir.services import storage


@pytest.fixture
def client(tmp_path):
    conn = storage.connect(tmp_path / "draupnir.sqlite")
    storage.replace_companies(conn, pd.DataFrame([{
        "ein_id": "1", "instance": "abc", "name": "ABC CORP", "fy": "2010", "fp": "Q2",
        "bas1": "1 Main St", "cityba": "Town", "stprba": "NY", "countryba": "US", "zipba": "10001",
    }]))
    rows = [("BS", "Cash And Cash Equivalents", 50.0), ("BS", "Long-Term Debt (Non-current)", 30.0),
            ("CF", "Cash From Operating Activities", 100.0), ("CF", "Capital Expenditures", 20.0),
            ("BS", "Shares Outstanding", 10.0)]
    storage.append_financials(conn, pd.DataFrame([
        {"ein_id": "1", "stmt": s, "tag_renamed": n, "value": v, "fy": "2010", "fq": "Q2", "qtrs": "0",
         "data_date": "20100630"} for s, n, v in rows
    ]))
    conn.close()
    app = create_app(Settings(data_dir=tmp_path, sec_user_agent=None), price=lambda ticker, day: 50.0)
    return app.test_client()


def test_index_renders(client):
    assert b"Draupnir" in client.get("/").data


def test_statement_pages(client):
    page = client.get("/data_query/abc_2010Q2")
    assert page.status_code == 200 and b"ABC CORP" in page.data and b"Shares Outstanding" in page.data
    data = client.get("/REST/abc_2010Q2").get_json()
    assert data["request_summary"]["address"] == "1 Main St, Town, NY US 10001"
    assert data["financials"]["value"] == [50.0, 30.0, 100.0, 20.0, 10.0]
    csv = client.get("/data_query/download_csv/abc_2010Q2")
    assert csv.mimetype == "text/csv" and csv.data.decode().startswith("tag_sec,")


def test_missing_data_is_a_message_not_a_crash(client):
    page = client.get("/data_query/abc_2010Q3")
    assert page.status_code == 404 and b"No data loaded for abc" in page.data
    assert client.get("/REST/zzz_2010Q2").status_code == 404
    assert client.get("/data_query/abc_2010-2").status_code == 404


def test_valuation_page(client):
    assert client.get("/valuation/dcf").status_code == 200
    page = client.get("/valuation/dcf?ticker=ABC&period=2010Q2&years=2&growth=0.1&decline=0.5&discount=0.1&terminal_growth=0")
    assert page.status_code == 200 and b"101.64" in page.data and b"Undervalued by 103.27%" in page.data
    bad = client.get("/valuation/dcf?ticker=abc&period=2010Q2&growth=lots")
    assert bad.status_code == 422 and b"growth must be a number" in bad.data
    assert client.get("/valuation/nope").status_code == 404


@pytest.mark.parametrize("path", ["/ticker_search", "/data_manual", "/compounding_interest"])
def test_static_pages(client, path):
    assert client.get(path).status_code == 200
