GOFMT := go run mvdan.cc/gofumpt
GOLANGCI_LINT := go run github.com/golangci/golangci-lint/v2/cmd/golangci-lint
COVERAGE_PROFILE := coverage.out
COVERAGE_THRESHOLD := 95
APP_PACKAGES := ./cmd/stocknews ./internal/...
TEST_PACKAGES := $(APP_PACKAGES) ./tools/coveragecheck
COVERAGE_PACKAGES := $(TEST_PACKAGES)

.DEFAULT_GOAL := check

.PHONY: check fmt lint test coverage clean

check: fmt lint test coverage

fmt:
	@tmp="$$(mktemp)"; trap 'rm -f "$$tmp"' EXIT; \
	$(GOFMT) -l . >"$$tmp"; \
	if [ -s "$$tmp" ]; then \
		printf 'gofumpt found unformatted files:\n'; \
		cat "$$tmp"; \
		exit 1; \
	fi

lint:
	$(GOLANGCI_LINT) run ./...

test:
	go test $(TEST_PACKAGES)

coverage:
	@coverpkg="$$(go list $(COVERAGE_PACKAGES) | paste -sd, -)"; \
	go test -covermode=count -coverpkg="$$coverpkg" -coverprofile=$(COVERAGE_PROFILE) $(TEST_PACKAGES)
	go run ./tools/coveragecheck -profile $(COVERAGE_PROFILE) -min $(COVERAGE_THRESHOLD)

clean:
	rm -f $(COVERAGE_PROFILE)
