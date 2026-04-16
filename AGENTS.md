# AGENTS.md

> Maintenance: update this file when modules are added/removed, data flow changes, or conventions shift.

## Project

Real-time stock news bot: Alpaca WebSocket API to Discord. Filters earnings reports and analyst ratings from Benzinga Newsdesk for approved sources/symbols.

## Module Graph

```text
run_bot.py
  -> news_realtime.main()
       -> config.Settings (env-based config via Pydantic)
       -> logging_config.setup_logging() (loguru)
       -> stream_news()
            -> httpx_ws WebSocket connection to Alpaca
            -> filter: single symbol, US exchanges, approved authors
            -> categorize: earnings / analyst / general news
            -> notify.send_discord_message()
                 -> analyst.parse_analyst_action() (for analyst category)
                 -> utils.parse_earnings_headline() (for earnings category)
                 -> discord_webhook.DiscordWebhook (rich embeds)
```

## Module Responsibilities

| Module | Lines | Role |
|---|---|---|
| `config.py` | 109 | `Settings(BaseSettings)`: API keys, webhook URLs, source/author filters. All from env vars. |
| `news_realtime.py` | 116 | WebSocket lifecycle: connect, authenticate, subscribe, filter, route messages. Entry point: `main()`. |
| `notify.py` | 158 | Build Discord embeds per category (earnings/analyst/news). Largest module. |
| `analyst.py` | 80 | Regex extraction of analyst ratings, price targets, firm names from headlines. |
| `utils.py` | 89 | Earnings headline parsing (EPS, revenue, estimates). Company name extraction via regex. |
| `logging_config.py` | 15 | Single-function loguru setup. |

## Key Patterns

- **Config**: Pydantic `BaseSettings` with `model_config` for env prefix. All secrets via env vars, no defaults for sensitive values.
- **Async**: `async def main()` entry, `httpx_ws` for WebSocket. Bot runs a single persistent connection.
- **Parsing**: Heavy regex use in `analyst.py` and `utils.py`. Patterns extract structured data (EPS, revenue, price targets) from unstructured headline text.
- **Filtering**: Multi-layer in `news_realtime.py`: source check, author allowlist, exchange filter, symbol filter. Order matters for short-circuit efficiency.
- **Notifications**: Category-specific Discord embeds with color coding, field layouts, and conditional content.
- **Error handling**: Sentry integration for production. `try/except` in WebSocket loop for resilience.

## Test Conventions

- Location: `tests/` (flat, no subdirectories)
- Framework: pytest + pytest-asyncio (auto mode) + pytest-cov + pytest-randomly
- Style: function-level async fixtures, `patch()`/`Mock()`/`side_effect` for mocking, docstrings on all test functions
- Coverage: branch mode, 90% target (codecov.yaml), HTML + XML + terminal output
- Tested: analyst, news_realtime, utils
- Untested: config, logging_config, notify

## Tooling

- **Build**: `uv` with `uv_build` backend, src-layout
- **Lint**: ruff v0.12.5 (preview mode, auto-fix on)
- **Types**: pyright v1.1.403 (standard mode)
- **Line length**: 120 (`.editorconfig`)
- **Pre-commit**: ruff lint+format, pyright, prettier (YAML/JSON/MD), uv-lock check
- **Commands**: `make all` runs lint + test + typecheck

## CI/CD Pipeline

```text
GitHub Actions (.github/workflows/main.yml):
  test job:
    -> make all (lint + test + typecheck)
  container job (on main push, needs test):
    -> build ghcr.io/major/stocknews:latest
  gitops job (needs container):
    -> update major/selfhosted deployment manifest with new image digest
```

All action versions pinned to commit SHAs. Container runs Python 3.14 + uv 0.11.6.

## Runtime

- Entry: `uv run run_bot.py` or `docker compose up`
- Config: `.env` file (docker-compose mounts it)
- Deps: Alpaca API (news WebSocket), Discord webhook URL, optional Sentry DSN
- Behavior: long-running single WebSocket connection, processes messages as they arrive
