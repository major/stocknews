# stocknews

stocknews is a Python 3.14 service that reads the Alpaca news stream and sends
relevant alerts to Discord. It filters for Benzinga Newsdesk items and routes
qualifying headlines to earnings, analyst, or general news webhooks.

The package is in `src/stocknews/`. The `stocknews` console command starts the
service.

## Configuration

| Variable | Required | Default | Description |
|---|---|---|---|
| `ALPACA_API_KEY` | Yes | None | Alpaca API key |
| `ALPACA_API_SECRET` | Yes | None | Alpaca API secret |
| `ALPACA_NEWS_STREAM_URL` | No | `wss://stream.data.alpaca.markets/v1beta1/news` | Alpaca news WebSocket URL |
| `DISCORD_ANALYST_WEBHOOKS` | No | Empty | Comma-separated Discord webhook URLs for analyst ratings |
| `DISCORD_ANALYST_ENABLED` | No | `true` | Enable analyst rating and price-target alerts |
| `DISCORD_EARNINGS_WEBHOOKS` | No | Empty | Comma-separated Discord webhook URLs for earnings |
| `DISCORD_EARNINGS_ENABLED` | No | `true` | Enable earnings alerts for EPS and sales actuals versus estimates |
| `DISCORD_NEWS_WEBHOOKS` | No | Empty | Comma-separated Discord webhook URLs for general news |
| `DISCORD_NEWS_ENABLED` | No | `true` | Enable general news headline alerts with optional summary and link |
| `STOCK_LOGO` | No | `https://static.stocktitan.net/company-logo/%s.webp` | Stock logo URL template (`%s` = ticker) |
| `TRANSPARENT_PNG` | No | `https://major.io/transparent.png` | Transparent PNG URL for Discord embed thumbnails |
| `BLOCKED_PHRASES` | No | `if you invested,you would have,would be worth` | Comma-separated phrases to suppress |

Set configuration in the environment or in `.env` when using Compose. The two
Alpaca credentials are required. Webhook lists and blocked phrases are
comma-separated; empty entries are ignored.

The three `DISCORD_*_ENABLED` settings accept `true`/`false`, `1`/`0`,
`yes`/`no`, or `on`/`off`, case-insensitive and with surrounding whitespace
ignored. They default to `true`. An explicitly empty or invalid value raises a
`ValueError` at startup. For example, a `.env` file can enable or disable each
category:

```dotenv
DISCORD_ANALYST_ENABLED=false
DISCORD_EARNINGS_ENABLED=true
DISCORD_NEWS_ENABLED=on
```

Disabling a category skips it before payload creation and enqueueing. It does
not route those items to general news. Earnings alerts compare EPS and sales
actuals with estimates. Analyst alerts cover ratings and price targets, except
announced or maintained price targets are suppressed. General news includes a
headline and may include a summary and link. All categories filter for
Benzinga Newsdesk items with a single eligible symbol. Webhooks are still
required to send alerts, and empty webhook lists send nothing. Setting changes
take effect after restarting the service.

`ALPACA_STOCK_STREAM_URL` is no longer used and is ignored. SPY and QQQ remain
valid news tickers, and stock logo behavior is unchanged.

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
after this workflow is merged to the default branch. It mutates
`src/stocknews/earnings.py` and `src/stocknews/analyst.py` against the full test
suite. It uses two workers and a 15-minute job limit. Result logs and statistics
are available as a 14-day artifact.

Surviving mutants are informational and have no score threshold. Setup,
baseline, or mutation-command failures still fail the workflow. It does not run
on pushes or pull requests and does not gate merges.
