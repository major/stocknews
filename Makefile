.PHONY: test lint typecheck all

test:
	uv run pytest

lint:
	uv run ruff format --check

typecheck:
	uv run pyright src/*

all: lint test typecheck
