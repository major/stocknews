# stocknews

stocknews is a Python 3.15 service that reads the Alpaca news stream and sends
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
| `ALPACA_WS_AUTH_MODE` | No | `message` | Alpaca WebSocket authentication mode: `message` or `headers` |
| `DISCORD_ANALYST_WEBHOOKS` | No | Empty | Comma-separated Discord webhook URLs for analyst ratings |
| `DISCORD_ANALYST_ENABLED` | No | `true` | Enable analyst rating and price-target alerts |
| `DISCORD_EARNINGS_WEBHOOKS` | No | Empty | Comma-separated Discord webhook URLs for earnings |
| `DISCORD_EARNINGS_ENABLED` | No | `true` | Enable earnings alerts for EPS and sales actuals versus estimates |
| `DISCORD_NEWS_WEBHOOKS` | No | Empty | Comma-separated Discord webhook URLs for general news |
| `DISCORD_NEWS_ENABLED` | No | `true` | Enable general news headline alerts with optional summary and link |
| `STOCK_LOGO` | No | `https://static.stocktitan.net/company-logo/%s.webp` | Stock logo URL template (`%s` = ticker) |
| `TRANSPARENT_PNG` | No | `https://major.io/transparent.png` | Transparent PNG URL for Discord embed thumbnails |

Set configuration in the environment or in `.env` when using Compose. The two
Alpaca credentials are required. Webhook lists are comma-separated; empty entries
are ignored.

`ALPACA_WS_AUTH_MODE=message` is the default and preserves the current behavior
of authenticating with an Alpaca stream message. Set it to `headers` to send
credentials in the WebSocket connection headers instead. Configuration changes
take effect after restarting the service.

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
Benzinga Newsdesk items. Symbols are trimmed, and blank or colon-qualified
symbols are removed. Items with no symbols are allowed; items with only
colon-qualified symbols are skipped. Mixed lists keep their US-style tickers.
This colon-based check does not verify exchange listings. Embeds use the first
retained ticker, or no ticker prefix and a transparent thumbnail for tickerless
news. Headlines are not blocked by phrase. Webhooks are still required to send
alerts, and empty webhook lists send nothing. Setting changes take effect after
restarting the service.

`ALPACA_STOCK_STREAM_URL` is no longer used and is ignored. SPY and QQQ remain
valid news tickers, and stock logo behavior is unchanged.

## Run locally

Install uv 0.13.5 first. The project selects Python 3.15.1.

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

The container uses a digest-pinned official Python 3.15.1 slim image on Debian
Trixie, installs the locked runtime dependencies only, and runs as non-root UID
1001.
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

## Python 3.15

See [What's New in Python 3.15](https://docs.python.org/3.15/whatsnew/3.15.html).

- The empty EOF marker dataclass is replaced by the built-in sentinel.
- UTF-8 is the default, and explicit encodings are retained.
- No compelling `frozendict` or unpacking sites were found. Lazy imports are
  unnecessary for imports used on every run.
- `TaskGroup.cancel()` was not adopted because preserving tested cancellation
  and draining behavior needs a separate refactor.

The new sampling profiler can inspect asyncio tasks without application changes.
For a local service process, replace `PID` with its process ID:

```bash
uv run --locked python -m profiling.sampling attach --async-aware --async-mode all -d 10 PID
```

Attaching requires OS permission to inspect the target process. Containers may
need additional permissions; do not weaken production isolation just to profile.

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
