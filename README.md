# stocknews

stocknews is a Python 3.15 service that polls Alpaca's REST news API and sends
relevant alerts to Discord. It filters for Benzinga Newsdesk items and routes
qualifying headlines to earnings, analyst, or general news webhooks.

The package is in `src/stocknews/`. The `stocknews` console command starts the
service.

## Configuration

| Variable | Required | Default | Description |
|---|---|---|---|
| `ALPACA_API_KEY` | Yes | None | Alpaca API key |
| `ALPACA_API_SECRET` | Yes | None | Alpaca API secret |
| `ALPACA_NEWS_REST_URL` | No | `https://data.alpaca.markets/v1beta1/news` | Alpaca REST news URL |
| `NEWS_POLL_INTERVAL_SECONDS` | No | `300` | Seconds to wait after each REST poll attempt |
| `NEWS_POLL_OVERLAP_SECONDS` | No | `600` | Seconds to overlap REST poll windows for catchup |
| `NEWS_STATE_PATH` | No | `.stocknews/news.sqlite3` | SQLite file for REST article IDs and poll checkpoint |
| `DISCORD_ANALYST_WEBHOOKS` | No | Empty | Comma-separated Discord webhook URLs for analyst ratings |
| `DISCORD_ANALYST_ENABLED` | No | `true` | Enable analyst rating and price-target alerts |
| `DISCORD_EARNINGS_WEBHOOKS` | No | Empty | Comma-separated Discord webhook URLs for earnings |
| `DISCORD_EARNINGS_ENABLED` | No | `true` | Enable earnings alerts for EPS and sales actuals versus estimates |
| `DISCORD_NEWS_WEBHOOKS` | No | Empty | Comma-separated Discord webhook URLs for general news |
| `DISCORD_NEWS_ENABLED` | No | `true` | Enable general news headline alerts with optional summary and link |
| `STOCK_LOGO` | No | `https://static.stocktitan.net/company-logo/%s.webp` | Stock logo URL template (`%s` = ticker) |
| `TRANSPARENT_PNG` | No | `https://major.io/transparent.png` | Transparent PNG URL for Discord embed thumbnails |

Set configuration in the environment or in `.env` when using Compose. The two
Alpaca credentials are required. Webhook lists are comma-separated; empty
entries are ignored.

The service uses REST polling for Alpaca news. Poll interval and overlap settings
must be positive integers, and the overlap must be at least the interval.

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

## REST news polling

The service makes its first poll immediately, requesting the most recent 10
minutes by default. It reads every page, then waits 5 minutes after the attempt
completes before polling again. Later windows use the persisted checkpoint and
the configured overlap for catchup. The initial baseline is persisted even if
a poll attempt fails. HTTP 401 and 403 responses are fatal. Other HTTP errors
and invalid responses are retried on a later poll without advancing the
completed window.

REST results use the Benzinga Newsdesk filters and category routing described
above. SQLite records article IDs before processing, so article updates and
repeats across pages, polls, or restarts are suppressed. IDs are retained
indefinitely, so the database grows over time. Filtered articles and articles
in disabled categories are also marked seen; enabling a category later does not
replay them. A crash after an ID is claimed but before its alert is sent can
lose that alert. Exact Discord delivery and retry behavior are not guaranteed.

Use one service instance for a REST state database. Do not delete the state file
unless you want previously seen articles to be eligible for replay. Compose
mounts the named `stocknews-state` volume at
`/opt/app-root/src/.stocknews`, writable by runtime UID 1001. Running
`podman compose down -v` removes that volume and its deduplication state. Any
custom `NEWS_STATE_PATH` must also be persistent and writable by UID 1001.

REST news access requires an Alpaca account entitlement. Account entitlement
and news latency have not been verified. The 10-minute default overlap does not
guarantee capture of articles that arrive later than the lookback window.

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
