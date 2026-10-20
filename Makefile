.PHONY: sync format lint test docs-audit check docs clean build publish help

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

# The documentation site, in sidebar order.  .github/workflows/docs.yml runs
# `make docs` for each published release and deploys site/ to GitHub Pages.
DOCS = README.md docs/INSTALL.md docs/CREDENTIALS.md docs/CREDENTIAL_TUI.md \
	docs/NETENV.md docs/RADIUS.md docs/PARSING.md docs/MARKDOWN.md \
	docs/TEXT_FILTERS.md docs/CREDENTIAL_FILE.md docs/SQLITE_CREDENTIALS.md \
	docs/DEVELOPMENT.md examples/parse/README.md examples/text/README.md \
	RELEASE_NOTES.md CHANGELOG.md
REPOSITORY = https://github.com/robert-patrick-texas/axlib
# The Git branch or tag each page's footer links to; the workflow passes the tag.
DOCS_REF ?= main

docs:
	rm -rf site
	uv run axlib md2html --strict --index README.md -o site \
		--site-name "axlib $$(uv version --short)" \
		--link "GitHub=$(REPOSITORY)" --link "PyPI=https://pypi.org/project/axlib/" \
		--source-url "$(REPOSITORY)/blob/$(DOCS_REF)/" $(DOCS)

clean:
	rm -rf .coverage .pytest_cache .ruff_cache .venv build dist site
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
	@echo "docs       - Build the HTML documentation site in site/"
	@echo "clean      - Remove generated files"
	@echo "build      - Build source and wheel distributions"
	@echo "publish    - Publish built artifacts with uv"
