# AGENTS.md

> Maintenance: update this file when modules are added/removed, data flow changes, or conventions shift.

## Project

Real-time stock news bot: Alpaca WebSocket API to Discord. Filters earnings reports and analyst ratings from Benzinga Newsdesk for approved sources/symbols.

## Module Graph

```text
src/main.rs
  -> config::Settings::from_env()
  -> alpaca::stream_news()
       -> tokio_tungstenite WebSocket connection to Alpaca
       -> authenticate, subscribe, read loop
       -> news::classify()
            -> single symbol, US exchanges, approved author
            -> earnings::is_earnings_news() / is_analyst_rating_change()
       -> discord::{earnings_payload, analyst_payload, news_payload, send_payload}()
            -> analyst::AnalystNews for analyst embeds
            -> earnings helpers for earnings embeds
            -> reqwest Discord webhook posts
```

## Module Responsibilities

| Module | Role |
|---|---|
| `config.rs` | Env-based settings, plural Discord webhook vars, defaults. |
| `alpaca.rs` | WebSocket lifecycle, auth, subscribe, message routing. |
| `news.rs` | Alpaca news item model, author/symbol filters, category classification. |
| `discord.rs` | Discord webhook payload builders and sender. |
| `analyst.rs` | Regex extraction of analyst ratings, price targets, firm names. |
| `earnings.rs` | Earnings headline parsing, result descriptions, blocked phrase helpers. |
| `main.rs` | Tokio entry point, tracing setup, Ctrl-C shutdown. |

## Key Patterns

- **Config**: env vars only; plural webhook variables (`*_WEBHOOKS`) are comma-separated.
- **Async**: Tokio runtime, `tokio-tungstenite` for Alpaca, `reqwest` for Discord.
- **Parsing**: regexes ported from the Python implementation with Rust unit tests.
- **Filtering**: single US symbol, approved author, then earnings/analyst/general classification.
- **Notifications**: Build Discord JSON with `serde`, then post to each configured webhook.
- **Errors**: `alpaca::AppResult<T>` keeps error handling small; add a custom error enum only if matching on errors becomes necessary.

## Test Conventions

- Unit tests live beside the module they cover.
- Keep table/fixture style simple; no snapshot dependency unless JSON payloads get hard to read.
- Minimum local gate: `cargo test` and `cargo clippy --all-targets -- -D warnings`.

## Tooling

- **Build**: Cargo, Rust 1.96 MSRV pinned in `rust-toolchain.toml`.
- **Lint**: `cargo fmt --check`, `cargo clippy --all-targets -- -D warnings`.
- **Coverage**: `cargo llvm-cov --lcov --output-path lcov.info`.
- **Dependency hygiene**: `cargo deny check`, `cargo machete`.
- **Pre-commit**: basic file checks, `cargo fmt`, `cargo clippy`.
- **Command**: `make check` runs lint, tests, and dependency hygiene.

## CI/CD Pipeline

```text
GitHub Actions:
  ci.yml:
    -> lint, test, coverage, msrv, security
  codeql.yml:
    -> Rust CodeQL buildless analysis
  container.yml:
    -> build ghcr.io/major/stocknews:latest from Containerfile
    -> update major/selfhosted deployment manifest with new image digest on main
```

All action versions are pinned to commit SHAs.

## Runtime

- Entry: `cargo run` or `podman compose up --build`.
- Config: `.env` file for podman compose or exported env vars locally.
- Deps: Alpaca API, Discord webhook URLs.
- Behavior: long-running single WebSocket connection, processes messages as they arrive.
