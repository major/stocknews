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

## Containers

```bash
podman compose up --build
```

The container uses a pinned UBI 9 Python 3.14 image, installs the locked runtime
dependencies only, and runs as a non-root user.

## Gates

- `make check` and `make all` run formatting, lint, type checks, branch coverage,
  and the package build.
- `make coverage` runs tests with branch coverage and requires at least 95%.
- `make test` runs the test suite.
- `make audit` checks dependencies with `pip-audit`.
