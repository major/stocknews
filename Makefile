GOFMT := go run mvdan.cc/gofumpt
GOLANGCI_LINT := go run github.com/golangci/golangci-lint/v2/cmd/golangci-lint
COVERAGE_PROFILE := coverage.out
COVERAGE_THRESHOLD := 95
APP_PACKAGES := ./cmd/stocknews ./internal/...
TEST_PACKAGES := $(APP_PACKAGES) ./tools/coveragecheck
COVERAGE_PACKAGES := $(TEST_PACKAGES)

.DEFAULT_GOAL := all

.PHONY: all check fmt fmt-fix lint test doc build coverage audit clean

all: fmt lint test doc build

check: all coverage

fmt:
	@tmp="$$(mktemp)"; trap 'rm -f "$$tmp"' EXIT; \
	$(GOFMT) -l . >"$$tmp"; \
	if [ -s "$$tmp" ]; then \
		printf 'gofumpt found unformatted files:\n'; \
		cat "$$tmp"; \
		exit 1; \
	fi

fmt-fix:
	$(GOFMT) -w .

lint:
	$(GOLANGCI_LINT) run ./...

test:
	go test $(TEST_PACKAGES)

doc:
	go vet ./...

build:
	go build ./cmd/stocknews

coverage:
	@coverpkg="$$(go list $(COVERAGE_PACKAGES) | paste -sd, -)"; \
	go test -covermode=count -coverpkg="$$coverpkg" -coverprofile=$(COVERAGE_PROFILE) $(TEST_PACKAGES)
	go run ./tools/coveragecheck -profile $(COVERAGE_PROFILE) -min $(COVERAGE_THRESHOLD)

audit:
	go run golang.org/x/vuln/cmd/govulncheck@latest ./...

clean:
	rm -f $(COVERAGE_PROFILE)
