# AGENTS.md

Real-time Alpaca stock news bot that filters Benzinga Newsdesk items and sends Discord webhook embeds.

## Start here

- Run local gates before committing: `make check`.
- Use `make coverage` to enforce aggregate Go test coverage at `>=95%`.
- Runtime: `go run ./cmd/stocknews` or `podman compose up --build`.
- Go toolchain: `1.24.0` from `go.mod`.

## Progressive discovery

Read only what you need:

1. Startup/runtime path: `cmd/stocknews/main.go` -> `internal/runtime/runtime.go` -> `internal/alpaca/stream.go`.
2. Env config: `internal/config/config.go`.
3. Filtering/classification: `internal/news/news.go`.
4. Discord payloads/sending: `internal/discord/payload.go` and `internal/discord/send.go`.
5. Headline parsing: `internal/analyst/analyst.go` and `internal/earnings/earnings.go`.
6. CI/container/gates: `.github/workflows/`, `Containerfile`, `compose.yml`, `Makefile`, `.golangci.yml`, `tools/coveragecheck`.

## Conventions

- Env vars only; Discord webhooks use plural comma-separated vars: `*_WEBHOOKS`.
- Keep tests next to the package they cover.
- Prefer simple structs/functions; add custom error types only when callers need to match errors.
- Container builds use Hummingbird Go images: `registry.access.redhat.com/ubi9/go-toolset:1.24` for build, `registry.access.redhat.com/ubi9/ubi-micro` for runtime.
- Keep action versions pinned to commit SHAs.
- Use `mvdan.cc/gofumpt` for formatting and `github.com/golangci/golangci-lint/v2` for lint, including exported comment enforcement via `revive`.
- Do not reintroduce legacy non-Go tooling.
