.PHONY: sync format lint test docs-audit check clean build publish help

sync:
	uv sync --all-groups

format:
	uv run ruff format .
	uv run ruff check --fix .

lint:
	uv run ruff format --check .
	uv run ruff check .
	uv run ty check src/

test:
	uv run pytest

docs-audit:
	uv run python tools/audit_docstrings.py src

check: lint docs-audit test

clean:
	rm -rf .coverage .pytest_cache .ruff_cache .venv build dist
	find . -type d -name "__pycache__" -prune -exec rm -rf {} +
	find . -type f -name "*.pyc" -delete

build:
	uv build

publish:
	uv publish

help:
	@echo "sync       - Install runtime and development dependencies"
	@echo "format     - Format code and apply safe lint fixes"
	@echo "lint       - Check formatting, lint, and static types"
	@echo "docs-audit - Verify educational docstring requirements"
	@echo "test       - Run pytest with coverage"
	@echo "check      - Run lint, docstring audit, and tests"
	@echo "clean      - Remove generated files"
	@echo "build      - Build source and wheel distributions"
	@echo "publish    - Publish built artifacts with uv"
