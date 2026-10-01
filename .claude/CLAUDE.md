# CLAUDE.md

Python 3.14 service that reads Alpaca news and stock streams, filters Benzinga
Newsdesk items, and sends Discord webhook embeds.

## Commands
- `uv sync --locked` - Install the locked development environment
- `uv run --locked pytest` - Run tests
- `uv run --locked ruff format --check .` - Check formatting
- `uv run --locked ruff check .` - Run lint checks
- `uv run --locked mypy src/stocknews` - Run mypy
- `uv run --locked pyright` - Run Pyright
- `make check` - Run all local gates, including coverage and package build
- `uv run stocknews` - Start the service
- `podman compose up --build` - Run in a container

## Architecture
**src/stocknews/__main__.py**: CLI startup, signal handling, and application lifecycle
**src/stocknews/alpaca.py**: Alpaca news and IEX trade WebSocket adapters
**src/stocknews/runtime.py**: Stream orchestration and bounded Discord delivery
**src/stocknews/news.py**: Benzinga Newsdesk filtering and classification
**src/stocknews/earnings.py** and **analyst.py**: Headline parsing and routing rules
**src/stocknews/discord.py**: Discord embed payloads and webhook requests
**src/stocknews/config.py** and **models.py**: Environment configuration and domain types
**src/stocknews/logging.py**: Structured JSON logs and webhook URL redaction

**Flow**: Alpaca streams → filter → classify as earnings, analyst, or general news → Discord

**Stack**: Python 3.14, uv, httpx2 WebSockets, and Discord webhooks
