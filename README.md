# stocknews

Pick apart stock news from Alpaca to make some relevant Discord alerts. 🤓

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
| `SENTRY_DSN` | No | — | Sentry DSN; omit or leave empty to disable error reporting |
| `SENTRY_DEBUG` | No | `false` | Set to `1` or `true` to enable Sentry debug logging |

## Run locally

```bash
podman compose up --build
```
