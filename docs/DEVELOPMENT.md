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

## Quality workflow

```bash
make format
make check
make build
```

`make check` runs Ruff formatting/lint checks, the ty static type checker, the
docstring audit, and pytest with coverage.

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

## Adding a credential backend

Implement the small structural interfaces in `axlib.credentials.manager` and
inject the provider into `lookup_values()` in tests. A production backend must:

- Never log secret values.
- Have explicit timeouts for remote calls.
- Use context management or an equivalent unconditional cleanup path.
- Treat encryption/TLS verification failures as errors, not silent fallbacks.
- Preserve caller-over-backend precedence.
