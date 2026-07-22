# stocknews

Pick apart stock news from Alpaca to make relevant Discord alerts. 🤓

## Configuration

| Variable | Required | Default | Description |
|---|---|---|---|
| `ALPACA_API_KEY` | Yes | — | Alpaca API key |
| `ALPACA_API_SECRET` | Yes | — | Alpaca API secret |
| `ALPACA_NEWS_STREAM_URL` | No | `wss://stream.data.alpaca.markets/v1beta1/news` | Alpaca news WebSocket URL |
| `DISCORD_ANALYST_WEBHOOKS` | No | — | Comma-separated Discord webhook URLs for analyst ratings |
| `DISCORD_EARNINGS_WEBHOOKS` | No | — | Comma-separated Discord webhook URLs for earnings |
| `DISCORD_NEWS_WEBHOOKS` | No | — | Comma-separated Discord webhook URLs for general news |
| `STOCK_LOGO` | No | `https://static.stocktitan.net/company-logo/%s.webp` | Stock logo URL template (`%s` = ticker) |
| `TRANSPARENT_PNG` | No | `https://major.io/transparent.png` | Transparent PNG URL for Discord embed thumbnails |
| `BLOCKED_PHRASES` | No | `if you invested,you would have,would be worth` | Comma-separated phrases to suppress |

## Run locally

```bash
go run ./cmd/stocknews
```

## Containers

```bash
podman compose up --build
```

## Gates

- `make fmt` — gofumpt check
- `make lint` — golangci-lint with exported comment coverage via revive
- `make test` — Go test suite
- `make coverage` — aggregate coverage gate at `>=95%`
- `make check` — all required local gates
