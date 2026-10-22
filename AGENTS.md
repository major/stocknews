# AGENTS.md

Real-time Python service that reads the Alpaca news stream, filters Benzinga
Newsdesk items, and sends Discord webhook embeds.

## Start here

- Run local gates before committing: `make check`.
- Use `make coverage` to require at least 95% combined coverage and 95% branch coverage.
- Runtime: `uv sync --locked && uv run stocknews` or `podman compose up --build`.
- Python 3.15.1 and uv 0.13.5 are used for local development.

## Progressive discovery

Read only what you need:

1. Entrypoint: the `stocknews` console script in `pyproject.toml`.
2. Configuration: `src/stocknews/config.py` and `src/stocknews/models.py`.
3. Filtering and routing: `src/stocknews/news.py`, `src/stocknews/earnings.py`, and `src/stocknews/analyst.py`.
4. CI, container, and gates: `.github/workflows/`, `Containerfile`, `compose.yml`, `Makefile`, `pyproject.toml`, and `uv.lock`.

## Conventions

- `ALPACA_API_KEY` and `ALPACA_API_SECRET` are required. Optional settings are
  `ALPACA_NEWS_STREAM_URL`, `DISCORD_ANALYST_ENABLED`,
  `DISCORD_ANALYST_WEBHOOKS`, `DISCORD_EARNINGS_ENABLED`,
  `DISCORD_EARNINGS_WEBHOOKS`, `DISCORD_NEWS_ENABLED`,
  `DISCORD_NEWS_WEBHOOKS`, `STOCK_LOGO`, and `TRANSPARENT_PNG`.
  The three category-enabled settings default to true and
  accept true/false, 1/0, yes/no, and on/off, case-insensitive with surrounding
  whitespace ignored. An explicitly empty or invalid value raises `ValueError`
  at startup. Discord webhook lists are comma-separated.
- Keep source in `src/stocknews/` and tests in `tests/`.
- Use sociable tests with real internal code. Replace only external or nondeterministic boundaries.
- Pytest blocks sockets by default. Allow loopback access only for tests that need local servers.
- Keep subprocess coverage enabled so CLI signal tests count toward coverage.
- Keep Python dependencies and the lockfile managed by uv. Use `uv sync --locked`.
- Keep both combined statement-and-branch coverage and branch-only coverage at 95% or higher.
- Preserve Benzinga Newsdesk filtering, earnings/analyst/general routing, and bounded worker behavior.
- Container builds use a digest-pinned official Python 3.15.1 slim image on Debian Trixie and install locked runtime dependencies only. The runtime must remain non-root UID 1001.
- Keep action versions pinned to commit SHAs.
- Do not reintroduce the legacy Go runtime or tooling.
