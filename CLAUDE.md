# Draupnir

Fundamental stock analysis on SEC EDGAR data: pull a company's financial statements,
normalize them into quarterly figures, and estimate intrinsic value (DCF and P/E models).
Served to the owner through a small localhost web UI: enter a ticker, see statements and valuations.

## Known bugs

Valuation and pipeline behavior has known flaws, fixed in a planned pass. Do not change that behavior
unless asked. Each is marked at its site with `# KNOWN-BUG(<id>)`.

| id | where | bug |
|----|-------|-----|
| DCF-TTM | services/valuation/dcf.py | uses one quarter's operating cash flow and capex instead of trailing twelve months |
| DCF-TV | services/valuation/dcf.py | terminal value discounted by `(1+r)^(n-1)` instead of `(1+r)^n` |
| PE-QEPS | services/valuation/pe.py | historical P/E = price / *quarterly* EPS, then applied to TTM EPS (~4x inflated) |
| PE-MEAN | services/valuation/pe.py | mean P/E where the method calls for median |
| TAG-OR | services/valuation/*.py | statement filter applies only to the first tag in OR'd tag lists |
| CAPEX-TAGS | services/valuation/dcf.py | capex list includes non-cash / net concepts |
| EIN-KEY | services/storage.py | companies keyed by EIN (can be 0/missing); should be CIK |
| TICKER-GUESS | services/ingest.py | ticker derived from XBRL instance filename |
| COREG | services/ingest.py | `num.coreg` ignored: segment/parent-company values mix with consolidated ones |
| DEDUP-NAME | services/ingest.py | rows deduped on (ein, display name, value, date), so tag_map.csv choices drop/keep rows |
| SHARES-DIGITS | clients/sec.py | shares scrape keeps every digit in the row, label digits included |
| PRICE-SPLIT | clients/yahoo.py | Yahoo closes are split-adjusted, SEC per-share figures are not: historical prices vs EPS/shares are off by every later split (AAPL 2010: $9.58 vs ~$268) |

**Not deferred:** security and safety. Always use parameterized SQL, no hardcoded secrets,
no bare `except:` that swallows errors silently. These are never deferred.

## Status

Ingest, storage, the DCF and P/E models, the Graham rating, Yahoo prices and the localhost UI work
end to end (checked on 2009Q4-2010Q4). Legend: **decide** = needs an owner decision; **risk** = known
weakness in what exists. Update this list as items land.

### Open decisions (decide)

- SEC User-Agent contact for `.env` (owner's name/email). Test runs so far used a generic one.
- Git: public GitHub repo `GaboM26/Draupnir` since 2026-11-25. License not chosen (until one is, all rights reserved).
- Fix phase order for the KNOWN-BUG list, and whether to move from data sets to SEC's companyfacts
  JSON API (fixes TICKER-GUESS, EIN-KEY, COREG, the shares scrape; needed anyway for a Vercel deploy).
- How far back to load (each data set quarter from 2009Q2 onward; later quarters are large).

### Graham rating (services/graham.py)

Follows GrahamValue's quick reference (Intelligent Investor ch. 14-15) on annual (10-K) figures:
defensive criteria 1-7 + Graham Number, enterprising 1-A..5 + P/E < 10 + Serenity Number, grade per
GrahamValue's ladder. The module docstring is the spec; "Choice:" comments mark our own decisions.
Not implemented: NCAV grade, utility/financial substitute tests (flagged by SIC), interest-rate
adjustment. Multi-year criteria need many 10-K years loaded; the 20-year dividend record cannot be
confirmed from SEC structured data (starts 2009).

### Known weaknesses in what exists (risk)

- **Ingest is not atomic.** `replace_companies` and `append_financials` commit separately; a crash
  between them leaves the company table updated without its rows. Wrap one quarter's writes in a
  single transaction.
- **No re-run/repair path.** A quarter cannot be reloaded (the previous-quarter check blocks it); fixing
  bad data means deleting the database and reloading from the start.
- **`to_quarterly`** is covered by unit tests on synthetic rows only.
- **Shares scrape** is untested against live R pages; it is unknown how many companies actually triggered a scrape.
- **Speed:** per-company pandas loops; 2009Q4-2010Q4 took ~5 min. Fine for now, slow for 60+ quarters.
- **SEC republishes old data sets**, so reloading a quarter later can give different rows.

## Layout

```
pyproject.toml           # deps, `draupnir` console script, pytest config
.env.example             # settings template; real .env is gitignored
src/draupnir/
  cli.py                 # THE entry point (`draupnir <command>` / `python -m draupnir`). Wires everything.
  __main__.py            # enables `python -m draupnir`
  config.py              # Settings from env. Only module that reads env vars.
  models/                # data shapes (dataclasses). No I/O, no logic beyond derived properties.
    company.py           #   Company
    financial.py         #   FinancialRow (one statement line item value)
    valuation.py         #   ValuationResult, Step
    errors.py            #   AnalysisError: expected "no answer" failures shown to the user
    graham.py            #   Criterion, GrahamCategory, GrahamRating, Outcome (Pass / Fail / Not enough data)
  api/                   # localhost Flask UI + HTTP API: app factory (create_app) + blueprint
    routes.py            #   thin handlers: parse request, call services, render/serialize
    serializers.py       #   models -> JSON / CSV
    templates/, static/
  services/              # business logic. Receives data and clients as arguments.
    ingest.py            #   raw SEC tables -> FinancialRows Pure pandas.
    storage.py           #   SQLite schema + read/write. Only module that writes SQL.
    graham.py            #   Graham defensive/enterprising criteria and grade (pure; page /graham)
    valuation/           #   the valuation calculator. Pure: no DB, no network.
      base.py            #     ValuationModel ABC, PriceLookup
      dcf.py, pe.py      #     concrete models; register new ones in MODELS (valuation/__init__.py)
  clients/               # external services. The only package that does network I/O.
    sec.py               #   SEC EDGAR: data set downloads, R-page scrape, rate limit, User-Agent
    yahoo.py             #   Yahoo Finance prices (yfinance)
  utils/
    helpers.py           # generic helpers with no domain knowledge; only when shared by 2+ modules
tests/                   # mirrors src/draupnir/ (tests/api/, tests/services/, tests/models/)
data/                    # gitignored, created at runtime: raw SEC files, draupnir.sqlite
```

When a module in `services/` grows too large, turn it into a subpackage (as `valuation/` is).
Add `docs/` or `scripts/` at the root only when there is something to put in them.

Dependency direction: `cli` -> `api` -> `services` -> `models`; `cli` wires `clients` into `services`.
`models` imports nothing from the project. `services/ingest.py` and `services/valuation/` never
import `clients`, `storage`, or `api`: they receive data and callables (e.g. `PriceLookup`) as
arguments, so they stay pure, testable, and portable (e.g. to a serverless function later).

To add or replace a valuation model: subclass `services.valuation.base.ValuationModel`, implement
`default_params` and `calculate`, add an instance to `MODELS` in `services/valuation/__init__.py`;
it gets a page at `/valuation/<name>` automatically.

## Commands

```sh
uv sync                    # install (Python >=3.11 managed by uv)
uv run draupnir ingest 2009Q4 2010Q1   # load quarters in order (first load seeds an empty db)
uv run draupnir serve      # http://127.0.0.1:5000
uv run pytest
```

## Conventions

- Python 3.11+, packaged with `pyproject.toml`, `src/` layout.
- SEC User-Agent comes from `DRAUPNIR_SEC_USER_AGENT` (e.g. `"Draupnir you@example.com"`); fail loudly if unset.
  Never hardcode anyone's email.
- Monetary values stored as REAL, not TEXT.
- Large data never committed: `data/` is gitignored.
- Tests: `pytest`. Valuation functions get worked-example tests.

## Deployment note

SEC endpoints (`data.sec.gov`, `www.sec.gov`) send no CORS headers, so a browser cannot fetch
them directly. Serving this from the owner's Vercel site would need a thin server-side proxy
(a Vercel serverless function); the math itself can run anywhere. For now: localhost only.
