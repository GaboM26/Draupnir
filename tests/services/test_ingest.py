import pandas as pd
import pytest

from draupnir.models.company import COMPANY_COLUMNS
from draupnir.services import ingest


def test_previous_quarter():
    assert ingest.previous_quarter("2010Q1") == "2009Q4"
    assert ingest.previous_quarter("2010Q3") == "2010Q2"
    with pytest.raises(ValueError):
        ingest.previous_quarter("2010-3")


def _sub(**overrides):
    row = {c: "" for c in COMPANY_COLUMNS if c not in ("cik_id", "ein_id", "edgar_year")}
    row.update(cik="1", ein="000123", form="10-Q", fy="2009", fp="Q3", instance="abc-20090930.xml", name="ABC")
    row.update(overrides)
    return row


def test_clean_companies_derives_ticker_and_drops_unusable_rows():
    sub = ingest.normalize_submissions(pd.DataFrame([
        _sub(),
        _sub(cik="2", ein="9", instance="a10-qq320196292019_htm.xml"),  # digits in derived ticker
        _sub(cik="3", ein="8", instance="xyz-2009.xml", form="8-K"),
        _sub(cik="4", ein="", instance="nop-2009.xml"),                 # missing EIN
    ]))
    companies = ingest.clean_companies(sub, "2009Q4")
    assert companies["instance"].tolist() == ["abc"]
    assert companies["ein_id"].tolist() == ["123"]
    assert companies["edgar_year"].tolist() == ["2009Q4"]


def test_apply_previous_tickers_requires_matching_name():
    sub = pd.DataFrame([_sub(ein="5", name="ACME", instance="a10q.xml"), _sub(ein="6", name="NEW", instance="b.xml")])
    previous = pd.DataFrame({"ein_id": ["5", "6"], "name": ["ACME", "OLD"], "instance": ["acme", "old"]})
    assert ingest.apply_previous_tickers(sub, previous)["instance"].tolist() == ["acme", "b.xml"]


def _row(stmt, tag, fq, qtrs, value, fy="2010", ein="1"):
    return {"tag_sec": tag, "label_sec": tag, "report": "1", "line": "1", "stmt": stmt, "data_date": "20100630",
            "fy": fy, "fq": fq, "qtrs": qtrs, "uom": "USD", "tag_renamed": tag, "value": value,
            "line_item": None, "url": "u", "ein_id": ein}


def test_to_quarterly_derives_q2_cash_flow_from_prior_cumulative():
    final = pd.DataFrame([_row("CF", "Capex", "Q2", "2", 30.0), _row("BS", "Cash", "Q2", "0", 5.0)])
    history = pd.DataFrame([_row("CF", "Capex", "Q1", "1", 10.0)])
    out = ingest.to_quarterly(final, history)
    capex = out[out["tag_sec"] == "Capex"].set_index("qtrs")["value"]
    assert capex.to_dict() == {"0": 20.0, "2": 30.0}


def test_to_quarterly_splits_fiscal_year_and_derives_q4_income():
    final = pd.DataFrame([_row("IS", "Revenue", "FY", "4", 100.0)])
    history = pd.DataFrame([_row("IS", "Revenue", "Q3", "3", 70.0)])
    out = ingest.to_quarterly(final, history)
    got = sorted(zip(out["fq"], out["qtrs"], out["value"]))
    assert got == [("FY", "4", 100.0), ("Q4", "0", 30.0), ("Q4", "4", 100.0)]
