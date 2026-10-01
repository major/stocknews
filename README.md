# stocknews

stocknews is a Python 3.14 service that reads Alpaca news and stock streams and
sends relevant alerts to Discord. It filters for Benzinga Newsdesk items and
routes qualifying headlines to earnings, analyst, or general news webhooks. It
also logs IEX trades for SPY and QQQ through a bounded worker.

The package is in `src/stocknews/`. The `stocknews` console command starts the
service.

## Configuration

| Variable | Required | Default | Description |
|---|---|---|---|
| `ALPACA_API_KEY` | Yes | None | Alpaca API key |
| `ALPACA_API_SECRET` | Yes | None | Alpaca API secret |
| `ALPACA_NEWS_STREAM_URL` | No | `wss://stream.data.alpaca.markets/v1beta1/news` | Alpaca news WebSocket URL |
| `ALPACA_STOCK_STREAM_URL` | No | `wss://stream.data.alpaca.markets/v2` | Alpaca stock WebSocket URL for IEX trades |
| `DISCORD_ANALYST_WEBHOOKS` | No | Empty | Comma-separated Discord webhook URLs for analyst ratings |
| `DISCORD_EARNINGS_WEBHOOKS` | No | Empty | Comma-separated Discord webhook URLs for earnings |
| `DISCORD_NEWS_WEBHOOKS` | No | Empty | Comma-separated Discord webhook URLs for general news |
| `STOCK_LOGO` | No | `https://static.stocktitan.net/company-logo/%s.webp` | Stock logo URL template (`%s` = ticker) |
| `TRANSPARENT_PNG` | No | `https://major.io/transparent.png` | Transparent PNG URL for Discord embed thumbnails |
| `BLOCKED_PHRASES` | No | `if you invested,you would have,would be worth` | Comma-separated phrases to suppress |

Set configuration in the environment or in `.env` when using Compose. The two
Alpaca credentials are required. Webhook lists and blocked phrases are
comma-separated; empty entries are ignored.

## Run locally

```bash
uv sync --locked
uv run stocknews
```

The startup banner shows the commit SHA from `GIT_SHA`, or `unknown` when no
SHA is provided. To show the current commit when running locally:

```bash
GIT_SHA=$(git rev-parse HEAD) uv run stocknews
```

## Containers

```bash
podman compose up --build
```

The container uses a pinned UBI 9 Python 3.14 image, installs the locked runtime
dependencies only, and runs as a non-root user.
For a manual build with the current commit in the startup banner, pass the SHA
as a build argument:

```bash
podman build --build-arg GIT_SHA="$(git rev-parse HEAD)" -t stocknews .
```

## Gates

- `make check` and `make all` run formatting, lint, type checks, branch coverage,
  and the package build.
- `make coverage` requires at least 95% combined statement-and-branch coverage and 95% branch-only coverage.
- `make test` runs the test suite.
- `make audit` checks dependencies with `pip-audit`.

## Mutation pilot

The non-gating GitHub Actions mutation pilot runs on Sundays at 05:17 UTC on the
default branch and can also be started manually. The schedule becomes active
after this workflow is merged to the default branch. It uses the existing
`mutmut` pilot scope for `src/stocknews/earnings.py` with
`tests/test_earnings.py`, `tests/test_news.py`, and
`tests/test_domain_properties.py`. It uses two workers and a 15-minute job
limit. Result logs and statistics are available as a 14-day artifact.

Surviving mutants are informational and have no score threshold. Setup,
baseline, or mutation-command failures still fail the workflow. It does not run
on pushes or pull requests and does not gate merges.
