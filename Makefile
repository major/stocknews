.PHONY: all check fmt fmt-fix lint types test build coverage branch-coverage audit clean

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
	$(MAKE) branch-coverage

branch-coverage:
	uv run --locked python -c 'import xml.etree.ElementTree as ET; root=ET.parse("coverage.xml").getroot(); covered=int(root.attrib["branches-covered"]); valid=int(root.attrib["branches-valid"]); rate=covered / valid if valid else 0.0; print(f"Branch coverage: {covered}/{valid} ({rate:.2%})"); raise SystemExit(0 if valid and rate >= 0.95 else 1)'

audit:
	uv run --locked pip-audit

clean:
	rm -rf .coverage coverage.xml htmlcov dist
