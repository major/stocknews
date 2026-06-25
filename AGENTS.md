# AGENTS.md

Real-time Alpaca stock news bot that filters Benzinga Newsdesk items and sends Discord webhook embeds.

## Start here

- Run local gates before committing: `cargo test && cargo clippy --all-targets -- -D warnings`.
- Use `make check` for the full gate: fmt, clippy, tests, `cargo deny`, `cargo machete`.
- Runtime: `cargo run` or `podman compose up --build`.
- Rust MSRV: 1.96 (`rust-toolchain.toml`).

## Progressive discovery

Read only what you need:

1. Startup/runtime path: `src/main.rs` -> `src/alpaca.rs`.
2. Env config: `src/config.rs`.
3. Filtering/classification: `src/news.rs`.
4. Discord payloads/sending: `src/discord.rs`.
5. Headline parsing: `src/analyst.rs` and `src/earnings.rs`.
6. CI/container/dependency gates: `.github/workflows/`, `Containerfile`, `podman-compose.yml`, `deny.toml`, `Makefile`.

## Conventions

- Env vars only; Discord webhooks use plural comma-separated vars: `*_WEBHOOKS`.
- Keep tests next to the module they cover.
- Prefer simple structs/functions; add custom error types only when callers need to match errors.
- Container builds use Hummingbird Rust images: `registry.access.redhat.com/hi/rust:1.96-builder` -> `registry.access.redhat.com/hi/rust:1.96`.
- Keep action versions pinned to commit SHAs.
- Do not reintroduce Python/uv tooling.
