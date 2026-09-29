# axlib release notes

Newest release first. [CHANGELOG.md](CHANGELOG.md) lists every individual change.

## 1.0.3

### Overview

This release makes axlib straightforward to deploy on a shared Linux host. The release tarball now contains `install.sh`, which sets up a shared Python, a locked axlib environment, and a ready credential store for the `netops` group, and `docs/INSTALL.md` explains how administrators maintain that environment and how network operations staff use it in their own scripts. The axlib package itself is unchanged from the previous release.

### Shared-host installer

The release tarball now contains `install.sh`, which installs axlib for every operator on a Linux host:

```bash
tar xzf axlib-<version>.tar.gz
sudo ./axlib-<version>/install.sh            # add --with-tui and/or --with-netenv as needed
```

| Path | Contents |
| --- | --- |
| `/opt/shared/python` | uv-managed Python, readable by every user |
| `/opt/shared/axlib` | uv project with a locked `.venv`, release wheels, and docs (`current/`) |
| `/usr/local/bin/axlib` | the `axlib` command |
| `/etc/axlib/axlib.toml`, `/etc/axlib/sqlite-aes256.key` | configuration and AES-256 key, `root:netops` |
| `/var/lib/axlib/credentials.db` | encrypted store, `root:netops 0660` |

`AXLIB_CONFIG_FILE` is set for all logins, so operators need no configuration. An existing configuration, key, or store is never overwritten, and rerunning a newer release's installer upgrades in place; previous wheels stay in `releases/` for rollback. See `docs/INSTALL.md`.

### Administrator and operator guides

`docs/INSTALL.md` now has two guides:

- **For administrators**: an `axuv` helper that runs uv on `/opt/shared/axlib` with the shared Python and `umask 022`; adding libraries such as netmiko and scrapli so every script can use them; pinning, upgrading, and removing libraries; tracking `pyproject.toml` and `uv.lock` in git for one-command undo; upgrades, rollback, backups, mirrors, and uninstalling. Libraries added this way survive axlib upgrades.
- **For network operations staff**: four ways to get credentials in a script: the shared interpreter (`#!/opt/shared/axlib/.venv/bin/python`, with netmiko and scrapli examples), a personal uv project or single-file uv script using the release wheel from `/opt/shared/axlib/releases/`, or the `NET*` variables from `netenv-set`. It also covers cron and systemd (use `ax.getkeys(getpass.getuser())` where `USER` may be unset) and rules for handling credentials.

Administrators should use `axuv` (or an equivalent `umask 022`) for every change to the shared environment. Running plain `uv` under a restrictive umask leaves new libraries unreadable for everyone except root, and the damage persists through reinstalls until the uv cache is cleaned. The guide's troubleshooting table gives the symptoms and the recovery steps.

### Compatibility

- The axlib package is unchanged from the previous release: no API, command, storage format, or dependency changes.
- Hosts that already have axlib installed can adopt the installer by running it. An existing configuration, key, or store is kept and only verified.

### Validation

Validated on Python 3.14.3:

```text
Ruff format and lint (all rules, including docs code): passed
ty static type check:                                  passed
Educational docstring audit:                           passed
Main pytest suite:                                     156 passed
shellcheck install.sh:                                 passed
install.sh on Ubuntu 24.04 (fresh, rerun, upgrade,
  rollback, non-root operator, login autoload):        passed
Shared libraries (netmiko, scrapli) as another user,
  kept across an axlib upgrade:                        passed
Personal uv project on the shared Python:              passed
```

## 1.0.2

### Overview

This release lets Network Operations staff load their network login into the shell when they SSH in. `axlib netenv-set` exports `NETUSER`, `NETPASS`, and `NETENABLE` from the operator's axlib record, and `axlib netenv-clear` removes them. Storage formats, `ax.getkeys()`, and the lookup precedence are unchanged.

### Session credentials

A program cannot change its parent shell's environment, so both commands print shell statements for the shell to `eval`:

```bash
eval "$(axlib netenv-set)"      # export NETUSER, NETPASS, NETENABLE
eval "$(axlib netenv-clear)"    # unset them
```

- `$USER` selects the record, as in `ax.getkeys()`. `--service NAME` loads another record.
- Existing `NET*` variables are ignored during the lookup, so a stale value never looks like a successful load.
- The shared fallback service is used only with `--allow-shared`, and always with a warning.
- A record without an enable secret unsets `NETENABLE`.
- Values are quoted with `shlex.quote`; standard output carries only shell code, and every message goes to standard error.
- On any failure nothing is printed on standard output and the environment is unchanged. Exit statuses: `0` exported, `1` no complete record, `2` configuration or store error, `3` refused because standard output is a terminal (which keeps the password off the screen).
- `--check` reports which fields would be set without showing values.

The commands are also available as `axlib-netenv-set`, `axlib-netenv-clear`, and `python -m axlib.credentials.netenv set|clear`.

### uv scripts and shell integration

- `scripts/netenv-set` and `scripts/netenv-clear` are uv inline-metadata scripts (`#!/usr/bin/env -S uv run --quiet --script`) that install axlib from the checkout they live in and run from any directory. Call them by their real path; a symlink moves the directory uv resolves the checkout from.
- `examples/netenv/netenv.sh` defines `netenv-set` and `netenv-clear` shell functions for bash and zsh and loads credentials when an interactive shell starts. Non-interactive sessions (`ssh host command`, `scp`, `sftp`, `rsync`) are left alone, `set -x` tracing is paused during the `eval`, and the functions return the command's real exit status. Install it in `~/.bashrc`, `~/.zshrc`, or `/etc/profile.d/`.

Read `docs/NETENV.md` before enabling this on a shared host. Exported passwords are visible to every program the shell starts, remain in long-running `tmux` sessions after a password change, and take precedence over the store in `ax.getkeys()`.

### Compatibility

- No storage format or schema changes, and no migrations.
- No new runtime dependencies.
- `ax.getkeys()`, `ax.getinfoblox()`, and existing commands are unchanged.

### Validation

Validated on Python 3.14.3:

```text
Ruff format and lint (all rules, including scripts/):  passed
ty static type check:                                  passed
Educational docstring audit:                           passed
Main pytest suite:                                     156 passed
Measured source line coverage:                         82%
Source-distribution build:                             passed
Extracted source-distribution tests:                   156 passed
bash eval round trip, set -x, non-interactive guard:   passed
```

## 1.0.1

### Overview

This release adds a Python administration API for the encrypted credential stores and an optional full-screen credential manager (TUI) for operators who run `ax.getkeys()` scripts but do not write Python. Storage formats are unchanged: existing SQLite databases, encrypted text files, and key files work without any migration.

### Python administration API

`axlib.credentials.StoreAdmin` administers either durable store through one API: `status()`, `initialize()`, `add()`, `update()` (including removal of optional fields), `delete()`, `rotate_key()`, and `annotate()`. It normalizes service names exactly as `ax.getkeys()` looks them up, validates fields against a record profile, clears the service's Redis cache entry after every change, and never returns a secret value. Both CLIs and the TUI are thin layers over it.

`python -m axlib.credentials.admin` is a standalone health check (text or `--json`) that exits `0` only when every enabled store is ready.

### Record profiles

Profiles record which fields each lookup helper expects: `network` (`netuser`, `netpass`, optional `netenable`) for `ax.getkeys()` and `infoblox` (`ibgrid`, `ibuser`, `ibpass`) for `ax.getinfoblox()`. Infoblox records can now be provisioned from the command line or the TUI. `python -m axlib.credentials.profiles` lists them.

### Command-line changes

- `add` and `update` accept `--profile network|infoblox` (default `network`).
- `update --remove FIELD` deletes an optional field; required fields cannot be removed.
- `rotate-key` re-encrypts every record under a new AES-256 key (`--generate-key` or `--new-key-file`, with `--dry-run` and a required `--yes`).
- When a change is saved but the Redis cache cannot be cleared, the command now prints `status=...` and `cache=stale` before exiting `1`, so it no longer looks like a failed save.
- `axlib credential-db` and `axlib credential-file` now share one implementation; existing command lines and output are unchanged.

### Credential manager (TUI)

Install the optional extra and start the manager locally or over SSH:

```bash
uv add 'axlib[tui]'
axlib credential-tui --config /etc/axlib/axlib.toml
```

It lists services with notes explaining how `ax.getkeys()` will use them, and it adds, edits, and deletes records, initializes stores, switches between the SQLite and text-file stores, and rotates keys. Credential values are never displayed. The manager refuses to start when Textual keystroke logging (`TEXTUAL_LOG`, devtools, or debug mode) is enabled, blocks clipboard copies from password boxes, matches its colors to the terminal, and closes after five idle minutes. See `docs/CREDENTIAL_TUI.md`.

### Version numbering

The release number now lives only in `pyproject.toml` and is changed with `uv version --bump patch`. `axlib.__version__` reads it from the installed package metadata, and a test fails if the number is hard-coded anywhere else.

### Compatibility

- No storage format or schema changes, and no migrations.
- `ax.getkeys()`, `ax.getinfoblox()`, and the lookup precedence are unchanged.
- Runtime dependencies are unchanged (`cryptography`, `redis`). The optional `tui` extra adds `textual>=8.2.8` and `rich>=15.0.0`.
- `normalize_service_for_write` moved to `axlib.credentials.manager` and remains importable from `axlib.credentials.cli_common`.
- `RELEASE_NOTES.md` is now included in the source distribution.

### Validation

Validated on Python 3.14.3:

```text
Ruff format and lint (all rules):                    passed
ty static type check:                                passed
Educational docstring audit:                         passed
Main pytest suite:                                   137 passed
Measured source line coverage:                       82%
Source-distribution build:                           passed
Extracted source-distribution tests:                 137 passed
TUI in a pseudo-terminal (truecolor/256/16/NO_COLOR): passed
```

## 1.0.0

### Overview

This release establishes the first axlib-owned credential-storage formats and a common administration model for durable credential stores. The former `keyring` / `keyrings.cryptfile` backend has been removed. Axlib now provides its own AES-256-GCM encrypted text credential store alongside the existing AES-256-GCM SQLite store.

This is intentionally a breaking release. Credential-store migrations are not implemented: unsupported encrypted-text formats and unsupported SQLite schema versions fail clearly and must be deliberately recreated or reprovisioned by an operator.

### Backwards-compatible application lookup

Existing network automation can continue to use:

```python
import axlib as ax

netuser, netpass, netenable = ax.getkeys()
```

When no service is explicitly supplied, `ax.getkeys()` uses `$USER` for the operator lookup. `NETUSER`, `NETPASS`, and `NETENABLE` continue to override stored fields. If an individual operator is not enlisted or lacks the required login fields, the configured shared service remains the fallback.

The lookup order for missing fields is:

1. caller/environment values;
2. Redis, when enabled;
3. SQLite, when enabled;
4. the axlib AES-256-GCM encrypted text file, when enabled.

SQLite is therefore the preferred durable store when both durable stores are configured.

### Native AES-256-GCM credential file

The new `CredentialFileStore` is implemented entirely within axlib. It does not import or depend on `keyring` or `keyrings.cryptfile`, does not read legacy cryptfile files, and does not support AES-128.

The format uses:

- AES-256-GCM from `cryptography`;
- a 32-byte URL-safe Base64 encryption key;
- a fresh 12-byte nonce for each encrypted service write;
- authenticated associated data that binds ciphertext to the normalized service identity;
- an encrypted key-check value so an incorrect key is rejected before normal credential use;
- an INI-like outer container with version/cipher metadata and encrypted service payloads;
- JSON serialization inside each encrypted service payload so both field names and field values are encrypted.

Text-file writes are protected by a sibling lock file and POSIX advisory locking. Mutations write a complete temporary sibling file, flush it, atomically replace the live credential file, and apply configured ownership/mode policy. This avoids lost updates when multiple Network Operations users administer the same shared store.

### Parallel durable-store APIs

`CredentialFileStore` and `SQLiteCredentialStore` use the same operational model:

- `initialize()` — create a new current-format store, or verify an existing current-format store;
- `create()` — create a service and fail if it already exists;
- `read()` — return requested decrypted fields;
- `update()` — modify fields and fail if the service does not exist;
- `write()` — atomically create a service when absent or merge fields when present;
- `delete()` — remove selected fields;
- `delete_service()` — remove the complete service record;
- `list_records()` — return service names, field names, and safe metadata without exposing secret values.

The text store additionally provides dependency-free convenience methods `get_password()`, `set_password()`, and `delete_password()` for greenfield scripts that prefer the familiar service/field calling style.

Detailed examples for every public method are in `docs/CREDENTIAL_FILE.md` and `docs/SQLITE_CREDENTIALS.md`.

### Standardized administration commands

The two durable stores now have parallel command-line interfaces:

```text
axlib credential-file init
axlib credential-file add
axlib credential-file update
axlib credential-file delete
axlib credential-file list

axlib credential-db init
axlib credential-db add
axlib credential-db update
axlib credential-db delete
axlib credential-db list
```

Standalone entry points are also installed:

```text
axlib-credential-file
axlib-credential-db
```

Both administration CLIs support the same input patterns where applicable:

```text
--set FIELD=VALUE
--from-env FIELD=ENV_VAR
--prompt FIELD
--dry-run
```

Listings expose only service/field names and safe timestamps. Credential values are not printed. Successful durable-store modifications invalidate the affected Redis cache entry when Redis caching is enabled.

### Configuration changes

The encrypted text store uses the new `[credential_file]` configuration section. Historical `keyring_file`, `keyring_key`, `keyring_key_file`, `AXLIB_KEYRING_*`, and `axlib-set-keys` interfaces have been removed.

Shared-host example:

```toml
[credentials]
shared_service = "network-shared"

[credential_file]
enabled = true
file = "/var/lib/axlib/credentials.axc"
key_file = "/etc/axlib/credential-file.key"
lock_timeout = 5.0
file_mode = "0660"
key_file_mode = "0640"
group = "netops"
enforce_permissions = true

[sqlite]
enabled = true
database = "/var/lib/axlib/credentials.db"
key_file = "/etc/axlib/sqlite-aes256.key"
timeout = 5.0
database_mode = "0660"
key_file_mode = "0640"
group = "netops"
enforce_permissions = true
```

For both stores, relative data/key paths in TOML are resolved relative to the selected configuration file. Key files may live independently of the database/credential-file path.

Direct key injection is also supported through the environment; direct encryption keys are not read from TOML:

```text
AXLIB_CREDENTIAL_FILE_KEY
AXLIB_SQLITE_KEY
```

The corresponding key-file variables are:

```text
AXLIB_CREDENTIAL_FILE_KEY_FILE
AXLIB_SQLITE_KEY_FILE
```

### Shared and private permission models

The shared-server defaults are intended for operators in the `netops` group:

- encrypted credential file: `0660`;
- credential-file lock file: `0660`;
- SQLite database: `0660`;
- encryption key files: `0640`;
- configured group: `netops`.

Private deployments can select `0600` modes and disable group enforcement by configuring an empty group. Existing symlinks and non-regular sensitive paths are rejected.

### Storage-version policy: no migrations

This release deliberately does not contain credential-store migration code.

For the encrypted text store, `initialize()` creates the current format when the file is absent and verifies the exact supported format/version/cipher when it exists. Legacy cryptfile data, AES-128 data, unknown versions, and alternate formats are rejected.

For SQLite, `initialize()` creates the current schema for a new database and verifies the exact current schema version for an existing database. Older or newer schema versions are rejected rather than upgraded or rewritten.

This policy keeps the initial production formats and educational code straightforward. Migration logic can be added later only if an operator explicitly requires it.

### Dependency changes

Runtime dependencies are now:

```text
cryptography>=42
redis>=5
```

Removed dependencies:

```text
keyring
keyrings.cryptfile
```

There is no optional keyring adapter and no keyring entry-point registration.

### Validation

The final release was validated on Python 3.13.5:

```text
Source compilation:                         passed
Educational docstring audit:                passed
Main pytest suite:                          78 passed
Measured source line coverage:              76%
Wheel build:                                passed
Source-distribution build:                  passed
Wheel metadata/dependency audit:            passed
Wheel entry-point audit:                    passed
Clean installed-wheel credential-file use: passed
Installed ax.getkeys() lookup:              passed
Installed shared-service fallback:          passed
Text-store convenience password methods:    passed
Extracted source-distribution tests:         78 passed
Unsupported text format/version rejection:  passed by test suite
Unsupported SQLite schema rejection:        passed by test suite
```

A live Redis service was not required for release validation. Redis behavior, precedence, and invalidation boundaries are exercised through the test suite without placing production secrets in test output.
