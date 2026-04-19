.PHONY: sync format lint test check clean build publish help

sync:
	uv sync --all-groups

format:
	ruff format . && ruff check --fix .

lint:
	ruff format --check && ruff check && ty check src/
test:
	uv run pytest
check: lint test

clean:
	ruff clean
	rm -f .coverage
	rm -fr dist/
	find . -type d -name "__pycache__" -exec rm -rf {} +
	find . -type d -name ".pytest_cache" -exec rm -rf {} +
	find . -type d -name ".mypy_cache" -exec rm -rf {} +
	find . -type d -name "*.pyc" -exec rm -rf {} +

build:
	uv build

publish:
	uv publish

help:
	@echo "sync     - Collect dependencies"
	@echo "format   - Fix format using ruff"
	@echo "lint     - Check format with ruff and ty"
	@echo "test     - Perform tests with pytest"
	@echo "check    - Run lint and test functions"
	@echo "clean    - Remove temporary files"
	@echo "build    - Build package"
	@echo "publish  - Push built package to Pypi"
