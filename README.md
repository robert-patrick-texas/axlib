# axlib 1.0.0

Axlib is an educational Python package for Network Operations staff building automation scripts. It combines pipeline-friendly text filters with reusable credential helpers that can resolve operator credentials without embedding passwords in source code.

## Highlights

- `import axlib as ax; ax.getkeys()` remains the simple network-credential API.
- `$USER` selects the operator record when no service is supplied.
- `NETUSER`, `NETPASS`, and `NETENABLE` override stored fields.
- A configured shared service remains the fallback when an operator is not yet enlisted.
- Redis can optionally cache plaintext values with TLS, authentication, TTL, and timeouts.
- SQLite stores service payloads with AES-256-GCM.
- The native encrypted text store also uses AES-256-GCM and no longer depends on `keyring` or `keyrings.cryptfile`.
- Both durable stores use parallel Python CRUD methods and parallel administration CLIs.
- Both durable stores support in-place AES-256 key rotation that re-encrypts every record.

## Install

```bash
python -m pip install axlib
```

Runtime dependencies are limited to `cryptography` and `redis`. Redis is imported only when caching is enabled.

## Existing automation API

```python
import axlib as ax

netuser, netpass, netenable = ax.getkeys()
```

Lookup precedence for each missing field is environment/caller, Redis, SQLite when enabled, then the encrypted text file when enabled.

## Standard credential administration

```bash
axlib credential-file init --generate-key
axlib credential-file add --service "$USER" --set netuser="$USER" --prompt netpass --prompt netenable
axlib credential-file list

axlib credential-db init --generate-key
axlib credential-db add --service "$USER" --set netuser="$USER" --prompt netpass --prompt netenable
axlib credential-db list

axlib credential-file rotate-key --generate-key --yes
axlib credential-db rotate-key --generate-key --yes
```

Both commands provide `init`, `add`, `update`, `delete`, `list`, and `rotate-key`. Both support `--set FIELD=VALUE`, `--from-env FIELD=ENV_VAR`, secure `--prompt FIELD`, and JSON listing.

## Shared server example

```toml
[credentials]
shared_service = "network-shared"

[credential_file]
enabled = true
file = "/var/lib/axlib/credentials.axc"
key_file = "/etc/axlib/credential-file.key"
file_mode = "0660"
key_file_mode = "0640"
group = "netops"

[sqlite]
enabled = true
database = "/var/lib/axlib/credentials.db"
key_file = "/etc/axlib/sqlite-aes256.key"
database_mode = "0660"
key_file_mode = "0640"
group = "netops"
```

When both durable stores are enabled, SQLite is preferred before the encrypted text file. The key files may live outside the data directory, and relative paths in TOML are resolved relative to the configuration file.

## Storage-version policy

Axlib 1.0.0 intentionally performs no credential-store migrations. The text store accepts only its current format version and AES-256-GCM cipher. SQLite accepts only its current axlib schema version. Unsupported versions fail clearly so operators can deliberately recreate/reprovision a store rather than have startup mutate it.

## Text filters

The `axlib.tf` modules can be imported or placed in shell pipelines:

```bash
python -m axlib tf include site.conf \
  | python -m axlib tf comments \
  | python -m axlib tf variables -v hostname=edge-01 \
  | python -m axlib tf whitespace \
  > site.rendered.conf
```

See `docs/TEXT_FILTERS.md` for filter semantics.

## Documentation

- `docs/CREDENTIALS.md` — lookup precedence, configuration, CLI, and permissions.
- `docs/CREDENTIAL_FILE.md` — full encrypted text-store API with examples for every method.
- `docs/SQLITE_CREDENTIALS.md` — full SQLite API with examples for every method.
- `docs/TEXT_FILTERS.md` — pipeline and Python text-processing examples.
- `docs/DEVELOPMENT.md` — development and validation guidance.

The source intentionally includes detailed module/function docstrings and comments explaining both what Python constructs do and why they are useful in network automation.
