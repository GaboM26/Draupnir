"""HTTP routes. Thin: parse the request, call services, render a template.

AnalysisError (unknown ticker, period not loaded, missing line item, no price) is the expected
"no answer" case and is shown to the user; anything else is a bug and surfaces as a 500.
"""

import sqlite3

from flask import Blueprint, Response, abort, current_app, g, redirect, render_template, request, url_for

from draupnir.api import serializers
from draupnir.models.errors import AnalysisError
from draupnir.models.financial import split_fiscal_period
from draupnir.services import storage
from draupnir.services.valuation import MODELS

bp = Blueprint("main", __name__)

DEFAULT_PERIOD = "2010Q3"


def _db() -> sqlite3.Connection:
    if "db" not in g:
        g.db = storage.connect(current_app.config["DRAUPNIR_SETTINGS"].db_path)
    return g.db


@bp.teardown_app_request
def _close_db(_exc: BaseException | None) -> None:
    conn = g.pop("db", None)
    if conn is not None:
        conn.close()


def _period_statements(ticker: str, fiscal_period: str) -> tuple[dict, object]:
    year, quarter = split_fiscal_period(fiscal_period)
    company = storage.find_company(_db(), ticker, fiscal_period)
    statements = storage.read_period_statements(_db(), company["ein_id"], year, quarter)
    if statements.empty:
        raise AnalysisError(f"No statements loaded for {ticker} in {fiscal_period}.")
    return company, statements


@bp.get("/")
def index():
    ticker = request.args.get("ticker", "").strip().lower()
    period = request.args.get("period", "").strip().upper()
    if ticker and period:
        return redirect(url_for("main.statements", ticker=ticker, fiscal_period=period))
    return render_template("index.html", period=period or DEFAULT_PERIOD, models=MODELS)


@bp.get("/data_query/<ticker>_<fiscal_period>")
def statements(ticker: str, fiscal_period: str):
    try:
        company, rows = _period_statements(ticker, fiscal_period)
    except AnalysisError as exc:
        return render_template("index.html", period=fiscal_period, models=MODELS, ticker=ticker, message=str(exc)), 404
    period_end = rows["data_date"].max()
    return render_template(
        "statements.html",
        company=company,
        ticker=ticker,
        fiscal_period=fiscal_period,
        period_end=f"{period_end[:4]}-{period_end[4:6]}-{period_end[6:]}",
        tables={stmt: rows[rows["stmt"] == stmt] for stmt in ("BS", "IS", "CF")},
    )


@bp.get("/data_query/download_csv/<ticker>_<fiscal_period>")
def statements_csv(ticker: str, fiscal_period: str):
    try:
        _, rows = _period_statements(ticker, fiscal_period)
    except AnalysisError as exc:
        abort(404, str(exc))
    return Response(
        serializers.statements_csv(rows),
        mimetype="text/csv",
        headers={"Content-Disposition": f"attachment; filename={ticker}_{fiscal_period}.csv"},
    )


@bp.get("/REST/<ticker>_<fiscal_period>")
def statements_json(ticker: str, fiscal_period: str):
    try:
        company, rows = _period_statements(ticker, fiscal_period)
    except AnalysisError as exc:
        return {"error": str(exc)}, 404
    return serializers.statements_json(company, ticker, fiscal_period, rows)


@bp.get("/ticker_search")
def ticker_search():
    return render_template("ticker_search.html", companies=storage.list_companies(_db()))


@bp.get("/valuation/<name>")
def valuation(name: str):
    model = MODELS.get(name)
    if model is None:
        abort(404)
    defaults = model.default_params()
    form = {"ticker": request.args.get("ticker", "").strip().lower(),
            "period": request.args.get("period", DEFAULT_PERIOD).strip().upper()}
    form.update({key: request.args.get(key, str(value)) for key, value in defaults.items()})
    context = {"model": model, "params": defaults, "form": form}
    if not form["ticker"]:
        return render_template("valuation.html", **context)
    try:
        params = _parse_params(form, defaults)
        company = storage.find_company(_db(), form["ticker"], form["period"])
        financials = storage.read_company_financials(_db(), company["ein_id"])
        result = model.calculate(form["ticker"], form["period"], financials, current_app.config["DRAUPNIR_PRICE"], params)
    except AnalysisError as exc:
        return render_template("valuation.html", message=str(exc), **context), 422
    return render_template("valuation.html", company=company, result=result, **context)


def _parse_params(form: dict, defaults: dict[str, float]) -> dict[str, float]:
    params = {}
    for key in defaults:
        try:
            params[key] = float(form[key])
        except ValueError:
            raise AnalysisError(f"{key} must be a number, got {form[key]!r}.") from None
    return params


@bp.get("/data_manual")
def data_manual():
    return render_template("data_manual.html")


@bp.get("/compounding_interest")
def compounding_interest():
    return render_template("compounding_interest.html")
