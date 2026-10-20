# Development guide

## Teaching-oriented source rules

Every Python module has a module-level docstring with purpose, network context,
dependencies, and a usage example. Every function documents `Args`, `Returns`,
and `Raises`. Inline comments should explain why a Python technique or network
automation guardrail is used rather than narrating obvious syntax.

Run the audit directly:

```bash
uv run python tools/audit_docstrings.py src
```

The audit reads the source with `ast` and never imports it. It reports a
module without a docstring, a function whose docstring lacks an `Args`,
`Returns`, or `Raises` section, and a function defined twice in the same
module or class (the second silently replaces the first). It prints one line
per problem on standard error and exits with status 1 when it finds any.

`axlib.calc` (`add`, `subtract`, `multiply`, `divide`) is the smallest example
of these rules: typed functions, full docstrings, and a doctest, with no
network code. Start there when learning how the package is written.

## Quality workflow

```bash
make sync
make format
make check
make build
```

| Target | What it runs |
| --- | --- |
| `make sync` | `uv sync --all-groups`: the project, every optional extra, and the tools. |
| `make format` | `ruff format .`, then `ruff check --fix .`. Ruff also formats Python code blocks inside Markdown files. |
| `make lint` | `ruff format --check .`, `ruff check .`, and `ty check src/`. |
| `make docs-audit` | The docstring audit above. |
| `make test` | pytest with coverage. |
| `make check` | `lint`, `docs-audit`, and `test`: run it before every commit. |
| `make docs` | Build the documentation site in `site/` (see [Documentation site](#documentation-site)). |
| `make build` | `uv build`: the source tarball and the wheel in `dist/`. |
| `make publish` | `uv publish`, which uploads `dist/` to PyPI with a token from `UV_PUBLISH_TOKEN`. |
| `make clean` | Remove `.venv`, caches, `build/`, `dist/`, and `site/`. |
| `make help` | List the targets. |

The `dev` dependency group includes every optional extra of the project
(`axlib[markdown,textfsm,tui]`), so a plain `uv sync` installs Textual,
TextFSM, and markdown-it-py, `ty` can check `axlib.credentials.tui`,
`axlib.parse`, and `axlib.md2html`, and pytest exercises them. Add
dependencies with `uv add`: `uv add --optional EXTRA ...` for a package only
one optional part needs (then add the extra to the `dev` group if it is new),
`uv add --dev ...` for tooling, never `pip install`.

## Source layout

| Module | Responsibility |
| --- | --- |
| `__init__.py`, `secrets.py`, `config.py` | The original `import axlib as ax` API (`ax.getkeys()` and friends), kept for existing scripts, and its settings snapshot. |
| `credentials/` | Settings, the lookup chain (`manager.py`, `providers.py`), the two encrypted stores, `StoreAdmin`, the CLIs, `netenv`, and the optional TUI. See [Credential administration layers](#credential-administration-layers). |
| `radius.py` | `axlib radius`, a standard-library RADIUS client. |
| `parse/` | `axlib parse`, in the order data flows: `capture.py` cuts captures into commands, `templates.py` finds each command's template, `snapshot.py` runs TextFSM, `normalize.py` makes records comparable, and `diff.py` compares snapshots. `settings.py` reads `[textfsm]`; `errors.py` holds the exceptions. |
| `md2html/` | `axlib md2html`: `render.py` parses Markdown and gives headings anchors, `site.py` finds files and rewrites and checks links, `page.py` holds the HTML layout. |
| `tf/` | The text filters, one module per filter, sharing `_cli.py`. |
| `calc.py` | Teaching examples. |

A credential lookup flows from `secrets.getkeys()` through `config.py`
(settings loaded once) to `credentials.manager.lookup_values()`, which asks
the environment, then the Redis cache, then the SQLite store, then the text
store. The `tf`, `parse`, and `md2html` packages load their exported names
lazily, from a table that `__getattr__` reads (`_FILTER_EXPORTS` in `tf`,
`_LAZY_EXPORTS` in the others); in `tf` and `parse`, whose modules also run
on their own, this keeps `python -m` on one module from importing it twice.

## Test strategy

- Pure text transformations use focused input/output tests.
- CLI tests use temporary files so standard pipeline behavior is exercised
  without touching operator data.
- Credential precedence tests use fake cache and encrypted-store providers. Unit
  tests must not require a live Redis server or real credentials.
- SQLite tests use temporary databases and generated test keys to verify CRUD,
  wrong-key rejection, ciphertext tamper detection, file modes, and absence of
  plaintext credential values in the database bytes.
- Backwards-compatibility tests call `import axlib as ax; ax.getkeys()` and verify
  `$USER` selection plus configured shared-service fallback.
- Optional integration tests may use disposable Redis and temporary encrypted credential files in
  a protected CI environment.

## Adding a text filter

1. Implement a pure, typed transformation function.
2. Expose `transform_text` as an alias while keeping a descriptive public name.
3. Use helpers from `axlib.tf._cli` for `INPUT`, `--output`, and `--encoding`.
4. Add the command to `axlib.tf.__main__.COMMAND_MODULES` and `pyproject.toml`.
5. Document exactly which text can be changed or destroyed.
6. Add unit and CLI tests, including line endings and empty input.

## Adding a comparison profile to `axlib parse`

A profile in `axlib.parse.normalize.PROFILES` tells `axlib parse diff` which
fields identify a record, which hold interface names, and which change on
every run, for one spelled-out command. It is used only when the command's
template marks no `Key` fields, so prefer `Key` in a template you control.

1. Check the field names the ntc templates produce on each platform
   (`axlib parse templates -p PLATFORM COMMAND` shows the template file).
2. Add the command to `PROFILES`, reusing a `Profile` when the fields match.
3. Add a capture to `examples/parse` or a test in `tests/test_parse_*.py` with
   reordered rows and a changed volatile field, and check that
   `axlib parse diff` reports only the real change.
4. Update the profile table in `docs/PARSING.md`.

## Documentation site

Every Markdown guide is also published as HTML at
<https://robert-patrick-texas.github.io/axlib/>, built by the project's own
`axlib md2html` ([MARKDOWN.md](MARKDOWN.md)).

- `make docs` converts the files listed in `DOCS` in the `Makefile`, in
  sidebar order, into `site/`, with `README.md` as `index.html`, the GitHub
  and PyPI links in every page's top bar, and footers linking to each source
  file at `DOCS_REF` (default `main`). It uses `--strict`, so a broken link or
  `#section` fails the build. Open `site/index.html` to check it. `site/` is
  ignored by Git.
- `.github/workflows/docs.yml` runs `make docs DOCS_REF=<tag>` from the tag of
  each **published** GitHub release, then deploys `site/` to GitHub Pages. Run
  it from the Actions tab ("Run workflow") to publish again without a
  release. Its actions are pinned to commit hashes; update a pin by replacing
  the hash and the version in its comment together.
- The repository's Pages source is "GitHub Actions". The `github-pages`
  environment allows deployments from the `main` branch and from `v*` tags,
  which is what lets a release's workflow, running on its tag, deploy.
- `test_project_documentation_has_no_broken_links` in `tests/test_md2html.py`
  checks the same links in every `make check`, so a broken link is found
  before release.

When adding a guide, add it to `DOCS` in the `Makefile`, link it from the
README's Documentation list, and check that `MANIFEST.in` ships it.

## Releasing

The release number is written in exactly one place, `version` in
`pyproject.toml`. `axlib.__version__` reads it from the installed package
metadata, and `test_release_number_is_not_hard_coded_elsewhere` fails if the
number (or text such as "axlib" followed by a version) appears in any file
other than `pyproject.toml`, `uv.lock`, `CHANGELOG.md`, and `RELEASE_NOTES.md`
(the generated `site/` is skipped). Describe current behavior without a
version number so documents never go stale.

```bash
uv version --bump patch      # or minor / major; updates pyproject.toml and uv.lock
# Date the new CHANGELOG.md heading and add a RELEASE_NOTES.md section.
make check
make docs                    # the site builds with no broken links
uv build                     # dist/axlib-<version>.tar.gz and the wheel
```

`MANIFEST.in` decides what goes into the source tarball, which is also what
`install.sh` installs from on a shared host. Its patterns name file types per
folder, so a new top-level file, example folder, or kind of example file is
left out until it is added there (a new guide in `docs/` is included); check
with `tar tzf dist/axlib-<version>.tar.gz`.

Then commit, tag the release commit `v<version>` (an annotated tag), and push
the branch and the tag. Publishing a GitHub release for the tag, with the
tarball and the wheel attached, also publishes the documentation site. Upload
the same two files to PyPI with `uvx twine upload dist/axlib-<version>*` (or
`make publish`, which uploads everything in `dist/`).

## Testing the installer

`tests/test_install_scripts.py` checks `install.sh` and `axuv` with `bash -n`
and `shellcheck` (skipped when shellcheck is not installed), and checks that
`axuv` refuses to run as an ordinary user. The installer itself needs root
and changes `/opt`, `/etc`, and `/var/lib`, so test a change end to end in a
disposable container instead:

```bash
make build
V=$(uv version --short)
docker run --rm -e V="$V" -v "$PWD/dist":/dist:ro \
    -v "$(readlink -f "$(command -v uv)")":/usr/local/bin/uv:ro \
    ubuntu:26.04 bash -c '
        apt-get -qq update && apt-get -qq install -y ca-certificates >/dev/null
        cd /tmp && tar xzf "/dist/axlib-$V.tar.gz"
        "./axlib-$V/install.sh" --with-tui --with-textfsm --with-markdown
        axlib credentials network; axlib md2html --help >/dev/null && echo ok'
```

For a release, also install the previous release's tarball first and then
upgrade it with the new one, so the upgrade path and the recorded options
are tested, and try the rollback in [INSTALL.md](INSTALL.md#rolling-back-axlib).

## Credential administration layers

Administration code is layered so that every rule lives in exactly one place:

| Module | Responsibility |
| --- | --- |
| `credentials/profiles.py` | Which fields each record type allows and requires. |
| `credentials/admin.py` | `StoreAdmin`: the Python API. Name normalization, profile validation, Redis invalidation, status, and advice notes. |
| `credentials/store_cli.py` | The one CLI implementation, parameterized by `StoreKind`. |
| `credentials/sqlite_cli.py`, `file_cli.py` | Thin wrappers that pick a store. |
| `credentials/tui/` | The optional Textual app: `app.py` (main screen), `screens.py` (dialogs), `widgets.py`, `theme.py`, and a launcher in `__init__.py`. |

Front ends collect input and display results; they call `StoreAdmin` rather
than the raw store classes. Every command module runs on its own with
`python -m`, as listed in the README's Commands table, and so do the modules
that are useful by themselves (`credentials.profiles` and
`credentials.admin`).

## Testing the TUI

TUI tests use Textual's headless `App.run_test()`, which returns a `Pilot`
that presses keys and clicks buttons. Each test drives the app the way an
operator would and then checks the store through `StoreAdmin`. Tests call
`asyncio.run()` themselves, so no pytest plugin is needed, and they skip
automatically when the `tui` extra is not installed.

Keep these TUI-specific rules when changing the app:

- Never pass a secret to `notify()`, `log()`, or an exception message.
- Give background workers an explicit `description`; Textual otherwise builds
  one from the worker's arguments, which could include secrets.
- Do not let exceptions escape code that holds secrets: Textual's crash report
  prints local variables.
- Keep `RecordChange.values` declared with `repr=False`.

## Adding a credential backend

Implement the small structural interfaces in `axlib.credentials.manager` and
inject the provider into `lookup_values()` in tests. A production backend must:

- Never log secret values.
- Have explicit timeouts for remote calls.
- Use context management or an equivalent unconditional cleanup path.
- Treat encryption/TLS verification failures as errors, not silent fallbacks.
- Preserve caller-over-backend precedence.
