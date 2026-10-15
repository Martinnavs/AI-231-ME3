.PHONY: check test
check:
	uv run ruff check .
	uv run ruff format --check .
	uv run mypy

test:
	uv run pytest -q
