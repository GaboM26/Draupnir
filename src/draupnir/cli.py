"""Draupnir entry point. Every way of running the project starts here."""

import argparse
import logging
import sqlite3

from draupnir.clients.sec import SecClient
from draupnir.config import load_settings
from draupnir.services import ingest, storage

log = logging.getLogger("draupnir")


def ingest_quarter(quarter: str, sec: SecClient, conn: sqlite3.Connection) -> None:
    """Load one SEC data set quarter into the database.

    The first load into an empty database stores rows as built. Later loads must follow the
    latest loaded quarter, because single-quarter values are derived from the prior quarter.
    """
    expected_previous = ingest.previous_quarter(quarter)
    latest = storage.latest_quarter(conn)
    if latest is not None and latest != expected_previous:
        raise SystemExit(f"cannot load {quarter}: database ends at {latest}, expected {expected_previous}")

    data = sec.load_dataset(quarter)
    previous = storage.read_companies(conn)
    sub = ingest.apply_previous_tickers(ingest.normalize_submissions(data.sub), previous)
    companies = ingest.clean_companies(sub, quarter)
    log.info("%s: %d companies after cleaning", quarter, len(companies))

    financials = ingest.build_statements(companies, data, ingest.load_tag_map(), sec.shares_outstanding)
    log.info("%s: %d statement rows", quarter, len(financials))

    if latest is not None:
        companies = ingest.carry_forward_companies(companies, previous)
        history = storage.read_financials(conn, min_fiscal_year=int(quarter[:4]) - 2)
        financials = ingest.to_quarterly(financials, history)
        log.info("%s: %d rows after quarterly derivation", quarter, len(financials))

    storage.replace_companies(conn, companies)
    storage.append_financials(conn, financials)
    log.info("%s: stored %d companies, %d financial rows", quarter, len(companies), len(financials))


def _ingest(args: argparse.Namespace) -> int:
    settings = load_settings()
    sec = SecClient(settings.require_sec_user_agent(), settings.raw_dir)
    conn = storage.connect(settings.db_path)
    try:
        for quarter in args.quarters:
            ingest_quarter(quarter, sec, conn)
    finally:
        conn.close()
    return 0


def _serve(args: argparse.Namespace) -> int:
    from draupnir.api import create_app
    from draupnir.clients import yahoo

    app = create_app(load_settings(), price=yahoo.closing_price)
    app.run(host="127.0.0.1", port=args.port, debug=args.debug)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="draupnir", description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    load = commands.add_parser("ingest", help="load SEC data set quarters, in order (e.g. 2009Q4 2010Q1)")
    load.add_argument("quarters", nargs="+")
    load.set_defaults(handler=_ingest)

    serve = commands.add_parser("serve", help="run the localhost web UI")
    serve.add_argument("--port", type=int, default=5000)
    serve.add_argument("--debug", action="store_true")
    serve.set_defaults(handler=_serve)

    return parser


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    args = build_parser().parse_args(argv)
    return args.handler(args)
