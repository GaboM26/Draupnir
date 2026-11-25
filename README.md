# Draupnir

Fundamental stock analysis on SEC EDGAR data, with a localhost UI.

```sh
cp .env.example .env   # set DRAUPNIR_SEC_USER_AGENT
uv sync
uv run draupnir serve  # http://127.0.0.1:5000
uv run pytest
```

See CLAUDE.md for layout and conventions.
