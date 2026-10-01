.PHONY: all check fmt fmt-fix lint types test build coverage audit clean

all: fmt lint types coverage build

check: all

fmt:
	uv run --locked ruff format --check .

fmt-fix:
	uv run --locked ruff format .

lint:
	uv run --locked ruff check .

types:
	uv run --locked mypy src/stocknews
	uv run --locked pyright

test:
	uv run --locked pytest

build:
	uv build

coverage:
	uv run --locked pytest --cov=stocknews --cov-branch --cov-report=term-missing:skip-covered --cov-report=xml:coverage.xml --cov-fail-under=95

audit:
	uv run --locked pip-audit

clean:
	rm -rf .coverage coverage.xml htmlcov dist
