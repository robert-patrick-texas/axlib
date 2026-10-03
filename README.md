# axlib

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
- `StoreAdmin` is one Python administration API for either store; both CLIs and the optional TUI are thin layers over it.
- An optional full-screen credential manager (TUI) gives non-developers an "easy button" for their `ax.getkeys()` records, locally or over SSH.
- Every record may carry a short, non-secret note (owner, purpose, expiry) that the TUI and `list` show and lookups ignore.
- `axlib netenv-set` / `axlib netenv-clear` load `NETUSER`, `NETPASS`, and `NETENABLE` into an operator's shell at SSH login, and clear them again.
- `axlib radius` checks a username and password against a RADIUS server, as a network device would, and reports Accept, Reject, or Challenge.

## Install

This project uses [uv](https://docs.astral.sh/uv/). Add axlib to your project:

```bash
uv add axlib
```

Add the optional full-screen credential manager as well:

```bash
uv add 'axlib[tui]'
```

To install axlib for every operator on a shared Linux host (a shared Python in `/opt/shared/python`, axlib in `/opt/shared/axlib`, and a ready `netops` credential store), unpack a release tarball and run its installer:

```bash
tar xzf axlib-<version>.tar.gz
sudo ./axlib-<version>/install.sh
```

The installer records its options in `/etc/axlib/install.env`, so the `axlib` command finds `/etc/axlib/axlib.toml` without `--config`, and upgrades need no options. Administrators add libraries such as netmiko for every script with `sudo axuv add netmiko`; `axuv` runs uv on the shared environment with the installer's settings. See `docs/INSTALL.md` for options, upgrades, rollback, and backups.

Runtime dependencies are limited to `cryptography` and `redis`. Redis is imported only when caching is enabled. The optional `tui` extra adds `textual` and `rich`.

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

Both commands provide `init`, `add`, `update`, `delete`, `list`, and `rotate-key`. Both support `--set FIELD=VALUE`, `--from-env FIELD=ENV_VAR`, secure `--prompt FIELD`, `--profile network|infoblox`, `update --remove FIELD`, and JSON listing.

## Credential manager (TUI)

Operators who run scripts built on `ax.getkeys()` but do not write Python can manage their records in a full-screen terminal UI:

```bash
uv add 'axlib[tui]'
axlib credential-tui --config /etc/axlib/axlib.toml
```

It lists each service's field names with notes explaining how `ax.getkeys()` will use them, and it adds, edits, and deletes records, initializes stores, and rotates keys. Credential values are never displayed. See `docs/CREDENTIAL_TUI.md`.

## Session credentials at login

Operators can have their network login exported into every SSH session:

```bash
eval "$(axlib netenv-set)"      # export NETUSER, NETPASS, NETENABLE from $USER's record
eval "$(axlib netenv-clear)"    # unset them
```

`examples/netenv/netenv.sh` wraps both in the `netenv-set` and `netenv-clear` shell functions and loads credentials when an interactive bash or zsh shell starts. The uv scripts `scripts/netenv-set` and `scripts/netenv-clear` run the same commands from a checkout, from any directory. Read the warnings in `docs/NETENV.md` before enabling this: exported passwords are visible to every program the shell starts.

## Python administration API

The same operations are available to Python code, with the same validation, profile rules, and Redis cache invalidation as the CLIs and the TUI:

```python
from axlib.credentials import StoreAdmin, StoreKind, load_settings

admin = StoreAdmin(load_settings("/etc/axlib/axlib.toml"), StoreKind.SQLITE)
admin.initialize(generate_key=True)
admin.add("first.last", {"netuser": "first.last", "netpass": password})
admin.update("first.last", remove=["netenable"])
print(admin.status().state)  # "ready"
```

`python -m axlib.credentials.admin --json` prints a health check of every configured store, and `python -m axlib.credentials.profiles` lists the fields each record profile expects.

## RADIUS login check

When a device login fails, ask the RADIUS server directly whether it accepts the account:

```bash
axlib radius 192.0.2.10 -u "$NETUSER" --password-env NETPASS --secret-file /etc/axlib/radius.secret
```

```python
from axlib.radius import authenticate

result = authenticate("192.0.2.10", "alice", password, secret)  # port=1812 by default
print(result.reply.code_name, result.accepted)
```

The exit status is 0 for Access-Accept, 1 for Reject or Challenge, 2 for errors, and 3 when no reply arrives. `--json` prints the full result. The module uses only the standard library and is checked against the RFC 2865 test vectors. See `docs/RADIUS.md`.

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

Axlib intentionally performs no credential-store migrations. The text store accepts only its current format version and AES-256-GCM cipher. SQLite accepts only its current axlib schema version. Unsupported versions fail clearly so operators can deliberately recreate/reprovision a store rather than have startup mutate it.

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

- `docs/INSTALL.md` — shared-host installation with `install.sh`: layout, options, upgrades, rollback, backups.
- `docs/CREDENTIALS.md` — lookup precedence, configuration, CLI, administration API, and permissions.
- `docs/CREDENTIAL_TUI.md` — operator guide for the optional full-screen credential manager.
- `docs/NETENV.md` — loading credentials into SSH sessions with `netenv-set` / `netenv-clear`: setup, warnings, and troubleshooting.
- `docs/RADIUS.md` — checking a login against a RADIUS server with `axlib radius`: options, exit status, Python API, how it works, and troubleshooting.
- `docs/CREDENTIAL_FILE.md` — full encrypted text-store API with examples for every method.
- `docs/SQLITE_CREDENTIALS.md` — full SQLite API with examples for every method.
- `docs/TEXT_FILTERS.md` — pipeline and Python text-processing examples.
- `docs/DEVELOPMENT.md` — development and validation guidance.

The source intentionally includes detailed module/function docstrings and comments explaining both what Python constructs do and why they are useful in network automation.
