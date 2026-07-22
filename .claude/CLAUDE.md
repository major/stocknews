# CLAUDE.md

Go bot streaming real-time stock news from Alpaca to Discord notifications. Filters for earnings and analyst ratings from approved sources.

## Commands
- `go test ./cmd/stocknews ./internal/...` - Tests
- `go run mvdan.cc/gofumpt -l .` - Format check
- `go run github.com/golangci/golangci-lint/v2/cmd/golangci-lint run ./...` - Lint and exported comment checks
- `make check` - Full local gate
- `go run ./cmd/stocknews` - Start bot
- `podman compose up --build` - Run in containers

## Architecture
**cmd/stocknews/main.go**: Entrypoint and process lifecycle
**internal/runtime**: Stream-to-dispatch orchestration
**internal/alpaca**: Alpaca news stream integration
**internal/discord**: Discord notifications with embeds
**internal/earnings**: Earnings parsing helpers
**internal/analyst**: Analyst rating parsing helpers
**internal/config**: Environment configuration

**Flow**: Alpaca stream → filter → categorize (earnings/analyst/news) → Discord

**Stack**: Go stdlib + Alpaca SDK
