# CLAUDE.md

Python bot streaming real-time stock news from Alpaca WebSocket API → Discord/Mastodon notifications. Filters for earnings & analyst ratings from approved sources (Benzinga Newsdesk).

## Commands
- `uv run pytest` - Tests with coverage
- `uv run ruff format --check` - Lint
- `uv run pyright src/*` - Type check
- `make all` - Lint + tests + typecheck
- `uv run run_bot.py` - Start bot
- `docker compose up` - Run in Docker

## Architecture
**news_realtime.py**: WebSocket client, auth, filtering (single symbol, US exchanges, approved authors), message routing
**notify.py**: Discord/Mastodon notifications with rich embeds
**utils.py**: Earnings regex parsing, company name extraction
**analyst.py**: Parse analyst ratings/price targets from headlines
**config.py**: Pydantic settings (API keys, webhooks, filters)

**Flow**: Alpaca WebSocket → filter → categorize (earnings/analyst/news) → Discord/Mastodon

**Stack**: `uv`, `httpx-ws`, pytest, Pydantic, Sentry