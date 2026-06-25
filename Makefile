MSRV := 1.96
LCOV := lcov.info

.DEFAULT_GOAL := check

.PHONY: check lint test coverage security deny machete msrv clean

check: lint test security

lint:
	cargo fmt --check
	cargo clippy --all-targets -- -D warnings

test:
	cargo test

coverage:
	cargo llvm-cov --lcov --output-path $(LCOV) --ignore-filename-regex 'src/(alpaca|config|main)\.rs'
	cargo llvm-cov report --summary-only --ignore-filename-regex 'src/(alpaca|config|main)\.rs'

security: deny machete

deny:
	cargo deny check

machete:
	cargo machete

msrv:
	cargo +$(MSRV) check --all-targets

clean:
	cargo clean
	rm -f $(LCOV)
