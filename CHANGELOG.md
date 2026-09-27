# Changelog

## 1.0.1 - 2026-09-26

- Added `axlib.credentials.admin.StoreAdmin`, a store-agnostic Python administration API (status, initialize, add, update with field removal, delete, rotate key, advice notes). It normalizes service names, validates fields against record profiles, and clears the Redis cache after every change without ever returning secret values.
- Added record profiles (`axlib.credentials.profiles`): `network` for `ax.getkeys()` and `infoblox` for `ax.getinfoblox()`. Both CLIs accept `--profile`, so Infoblox records can now be provisioned without Python.
- Added `update --remove FIELD` to both CLIs.
- Refactored `axlib credential-db` and `axlib credential-file` onto one shared implementation (`store_cli.py`); command lines and output are unchanged.
- A change that is saved but cannot clear the Redis cache now prints `status=...` and `cache=stale` before exiting 1, instead of looking like a failed save.
- Added the optional full-screen credential manager: `axlib credential-tui` / `axlib-credential-tui` / `python -m axlib.credentials.tui`, installed with `uv add 'axlib[tui]'`. It refuses to start under Textual keystroke logging, blocks clipboard copies of secrets, matches colors to the terminal, and closes after an idle timeout.
- Added standalone commands `python -m axlib.credentials.admin` (store health check) and `python -m axlib.credentials.profiles`.
- Moved `normalize_service_for_write` to `axlib.credentials.manager`; it is still importable from `axlib.credentials.cli_common`.
- The release number now lives only in `pyproject.toml` (bump it with `uv version --bump patch`). `axlib.__version__` and `axlib.secrets.__version__` read it from the installed package metadata, and a test fails if the number is hard-coded anywhere else.
- `RELEASE_NOTES.md` is now included in the source distribution.
- Added `rotate_key()` to `CredentialFileStore` and `SQLiteCredentialStore`, re-encrypting every record and the store's key-check marker under a new AES-256 key.
- Added matching `axlib credential-file rotate-key` and `axlib credential-db rotate-key` CLI subcommands with `--generate-key`, `--new-key-file`, `--dry-run`, and a required `--yes` confirmation.
- Rotation requires a configured key file (not an environment-only key) and replaces that file only after the durable store has already been updated; an interrupted rotation leaves the new key recoverable at `<key_file>.rotating` instead of losing access to the store.

## 1.0.0 - 2026-09-12

- Replaced the `keyring` / `keyrings.cryptfile` backend with an axlib-native, versioned AES-256-GCM encrypted text credential store.
- Added `CredentialFileStore` with `initialize`, `create`, `read`, `update`, `write`, field-level `delete`, `delete_service`, and `list_records`.
- Added cross-process shared/exclusive file locking, atomic fsync-and-replace updates, key verification, authenticated service identity, configurable owner/group/modes, and default shared-host group `netops`.
- Added `axlib credential-file` and `axlib-credential-file` with the same `init/add/update/delete/list` workflow as `credential-db`. Removed the legacy `axlib-set-keys` entry point.
- Removed runtime dependencies on `keyring` and `keyrings.cryptfile`.
- Standardized direct 32-byte URL-safe Base64 AES key handling for text-file and SQLite stores, using environment injection or protected key files.
- Preserved `axlib.getkeys()`, `$USER` lookup, environment precedence, Redis caching, and configured shared-service fallback.
- Tightened SQLite initialization: an existing database must already match the exact current schema; no migration is performed.
- Declared the same no-migration policy for encrypted text files. Unsupported versions fail clearly.
- Expanded credential-store documentation with API examples for every public CRUD method.

## 0.3.1 - 2026-09-12

- Changed the shared-host SQLite database default from `0600` to `0660` and
  added default group ownership by `netops`.
- Added separate configurable database and key-file modes, optional owner/group
  enforcement, and an escape hatch for externally managed ACL deployments.
- Set the shared key-file default to `0640`, allowing `netops` read access while
  retaining owner-only write access.
- Added deterministic resolution of relative SQLite database and key-file paths
  from the directory containing the selected TOML configuration.
- Added set-group-ID protection to package-created shared directories so SQLite
  rollback journals inherit the configured group.
- Added mode/owner/group verification before SQLite and key-file use, while
  avoiding unnecessary metadata changes for non-owner group members.
- Added a complete SQLite administration guide with examples for key generation,
  `initialize()`, `create()`, `read()`, `update()`, atomic `write()`, field-level
  `delete()`, `delete_service()`, and `list_records()`.
- Preserved the package-root `ax.getkeys()` interface, `$USER` lookup,
  shared-service fallback, Redis precedence, and cryptfile fallback.

## 0.3.0 - 2026-09-12

- Added an AES-256-GCM encrypted SQLite credential store using a fresh 12-byte
  nonce for every write and service-bound authenticated data.
- Added database initialization and key generation with owner-only file modes.
- Added separate create, update, delete-service, field-delete, read, and safe
  record-list APIs.
- Added `axlib credential-db` and `axlib-credential-db` commands with `init`,
  `add`, `update`, `delete`, and `list` subcommands.
- Added protected prompt and environment-variable input paths; management
  commands never display credential values.
- Added configurable SQLite database, key source, and lock timeout settings.
- Changed durable lookup precedence to Redis cache, configured SQLite, then the
  original encrypted cryptfile, preserving gradual migration and fallback.
- Preserved `import axlib as ax; ax.getkeys()`, `$USER` lookup, and configured
  shared-service fallback without calling-code changes.
- Invalidated complete Redis service hashes after SQLite record changes.
- Added wrong-key, ciphertext-tamper, CRUD, CLI, precedence, and backwards-
  compatibility regression tests.

## 0.2.0 - 2026-09-12

- Consolidated text filters and credential helpers under one `axlib` source tree.
- Preserved `import axlib as ax; ax.getkeys()` and `$USER`-based lookup.
- Preserved shared-service fallback for incomplete individual network records,
  with the service now configured rather than hard-coded.
- Added an explicit credential settings/provider/manager architecture.
- Added Redis TLS, ACL authentication, timeouts, optional key prefix, and TTL.
- Removed hard-coded paths, encryption keys, accounts, and provisioning data.
- Replaced `axsetkeys.py` data literals with safe prompt/environment-driven CLI.
- Invalidated changed Redis fields after encrypted credential updates.
- Fixed missing-`NETENABLE` lookup and blank environment override behavior.
- Guaranteed Redis client cleanup through context management.
- Added consistent file/stdin, output/stdout, and encoding syntax to every text
  filter plus unified `axlib tf` commands.
- Added conventional `-V`/`--version` flags and lazy text-filter exports so
  direct `python -m axlib.tf.<module>` execution is warning-free.
- Added comprehensive educational docstrings, rationale comments, docs, and
  expanded regression tests.
