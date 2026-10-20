# Changelog

## 1.0.6 - 2026-10-20

- Added `axlib parse` (also `axlib-parse` and `python -m axlib.parse`), which parses show-command output with TextFSM templates into records. Captures can be session logs with prompts (IOS, NX-OS, EOS, IOS-XR, and Junos styles), script output with bare `show`/`display` commands, an operator's own header format (`--marker`), files and directory trees (`-r`, `--glob`), or piped output (`--command`). Pager prompts, carriage returns, IOS exec-prompt timestamps, and Junos `{master:0}`/`[edit]` lines are removed. Output is a JSON snapshot (default), JSON lines, or aligned tables. Commands with no template, or with `|`-filtered output, are skipped; a template that rejects its output is reported as a failure. Exit status: 0 parsed, 1 a command failed or nothing was parsed, 2 error.
- Records are made comparable: they are sorted by their key in natural order, interface names are spelled one way (`Gig 1/0/48`, `Gi1/0/48` → `GigabitEthernet1/0/48`), and volatile fields such as CDP/LLDP hold time are dropped. The key comes from the template's `Key` values, else from built-in profiles for CDP and LLDP neighbors (brief and detail), IP interface brief, and interface status. `--raw` returns records exactly as the template does, and `--ignore FIELD` drops a field.
- Added `axlib parse diff BEFORE AFTER`, which compares two collections (snapshots, capture files, or directories) record by record and reports added, removed, and changed records with the changed fields. Commands are paired by device and spelled-out command, so `sh cdp nei` pairs with `show cdp neighbors`. Exit status: 0 no differences, 1 differences, 2 trouble.
- Operators add TextFSM templates by putting `<platform>_<command>.textfsm` files in a template directory, or an ntc-templates style `index` for commands with arguments or per-host templates. Directories are searched in this order: `--template-dir`, `AXLIB_TEXTFSM_TEMPLATES`, `template_dirs` in a new `[textfsm]` table of `axlib.toml` (default: a `textfsm` directory beside it), then ntc-templates (or `NTC_TEMPLATES_DIR`). In each directory a command matches as written or as a word-by-word abbreviation, so an operator's template overrides ntc-templates however the command is abbreviated. `axlib parse templates` shows the search path and which template parses a command, and `axlib parse split` shows how a capture is cut into commands. `[textfsm]` can also set a default `platform` (or `AXLIB_TEXTFSM_PLATFORM`).
- Added the Python API `axlib.parse`: `parse_output()` (one command's output, for netmiko scripts), `parse_text()`, `parse_paths()`, `compare()`, `TemplateCatalog`, `split_capture()`, `canonical_interface()`, and `normalize_records()`.
- Added the optional extra `textfsm` (`uv add 'axlib[textfsm]'`), which installs `textfsm` and `ntc-templates`. Splitting captures and comparing saved snapshots need only the standard library.
- `install.sh --with-textfsm` installs the extra and creates `/etc/axlib/textfsm` (`root:netops 2775`) for shared templates; `--no-textfsm` removes the packages and keeps the templates. The choice is recorded as `WITH_TEXTFSM` in `/etc/axlib/install.env`. Removing one extra while keeping another now works for any combination of `tui` and `textfsm`. A new `axlib.toml` includes a commented `[textfsm]` table.
- Added `docs/PARSING.md` and the `examples/parse` before/after captures with an example operator template.

## 1.0.5 - 2026-10-03

- Added `axlib radius` (also `axlib-radius` and `python -m axlib.radius`), a RADIUS client that checks a username and password against a RADIUS server, as a network device does. It sends one PAP `Access-Request` to UDP port 1812 (or `--port`), resends on timeout, and prints `Access-Accept`, `Access-Reject`, or `Access-Challenge` with the reply's attributes (Reply-Message, Cisco and other vendor attributes, and more), or one JSON object with `--json`. Exit status: 0 accept, 1 reject or challenge, 2 error, 3 no reply. The password and shared secret come from an environment variable, standard input, a secret file, or an echo-off prompt.
- Added the Python API `axlib.radius.authenticate()`, which returns a `RadiusResult` and never holds the password or secret, plus the packet functions `hide_password()`, `build_access_request()`, and `verify_response()`. Every request carries a `Message-Authenticator` (BlastRADIUS, CVE-2024-3596), and every reply's Response Authenticator, and its Message-Authenticator when present, is verified. Standard library only.
- Added `docs/RADIUS.md`.

## 1.0.4 - 2026-09-29

- Records may carry an optional `note`: one line of up to 120 printable characters, for owners, purposes, or expiry dates. Both profiles allow it, it is stored encrypted inside the record, and no lookup helper reads it (`FieldSpec.lookup`, `RecordProfile.lookup_names`), so `ax.getkeys()` is unaffected, `netenv-set` never exports it, and the TUI's "overrides" advice ignores it. The storage formats are unchanged.
- The TUI shows notes in a new NOTE column (first 40 characters), shows the highlighted record's full note below the table, filters on note text, and edits the note in the add and edit forms; clearing it removes it. The advice column is renamed from NOTES to ADVICE so the two cannot be confused.
- `axlib credential-db list` and `axlib credential-file list` add a NOTE column (last, so the fixed columns keep their positions) and a `note` key (or `null`) to `--json`. `add`/`update --set note=...` and `update --remove note` work in both CLIs.
- `list_records(visible_fields=...)` on both stores returns the values of named non-secret fields in `record.visible`; `StoreAdmin.list_records()` asks for the note, and `AnnotatedRecord.note` returns it. `python -m axlib.credentials.profiles` adds a LOOKUP column (and `lookup` in JSON).
- `install.sh` records its options in `/etc/axlib/install.env`, and every later run starts from them, so `sudo ./install.sh` upgrades a host exactly as it was installed. New options `--no-tui`, `--no-netenv`, `--store`, and `--sbin-dir` undo `--with-tui`, `--with-netenv`, and `--no-store`, or choose where `axuv` goes. `--no-tui` now removes the TUI packages from `.venv`; before, `uv add` kept the extra. On a host installed by 1.0.3, a run without these options keeps the TUI and netenv choices it already has. A changed `--config-dir` now also updates `AXLIB_CONFIG_FILE` in `/etc/environment`.
- `install.sh` installs `/usr/local/sbin/axuv` (from `scripts/axuv`), which replaces the shell function in `docs/INSTALL.md`. It runs uv as root on the shared environment with `umask 022` and the shared Python, taking the paths from the install record.
- axlib uses the `CONFIG_FILE` recorded by `install.sh` when neither `--config` nor `AXLIB_CONFIG_FILE` names a configuration file. `axlib` commands, the TUI, and `ax.getkeys()` scripts therefore work under `sudo` without `-E`, in cron, and in systemd units with no extra setup. Added `axlib.credentials.default_config_file()`. Every `--config` help text describes the lookup order.
- `docs/INSTALL.md`: documents the recorded options and the `axuv` command, and recommends `/etc/uv/uv.toml` for mirrors on offline hosts.
- `docs/INSTALL.md`: axlib is now published on PyPI, so operators' own projects and uv scripts may use `axlib>=1.0` from PyPI as well as the server's release wheel. The 0.1.x releases, which lack the credential stores, have been removed from PyPI; the guide explains that a mirror or cache may still serve them, and how to recognize and fix that.

## 1.0.3 - 2026-09-29

- Added `install.sh` to the release tarball. Run as root from an unpacked release, it installs a uv-managed Python in `/opt/shared/python` and axlib as a locked uv project in `/opt/shared/axlib`, and links `/usr/local/bin/axlib`. It creates `/etc/axlib` and `/var/lib/axlib` for the `netops` group, writes a minimal `axlib.toml` only if none exists, sets `AXLIB_CONFIG_FILE` in `/etc/environment` and `/etc/profile.d`, and creates or verifies the SQLite store without ever replacing a key. Options include `--with-tui`, `--with-netenv`, and custom paths. Rerunning it upgrades in place.
- Added `docs/INSTALL.md`, with a section for administrators (an `axuv` helper for running uv safely on the shared environment, adding libraries such as netmiko and scrapli for every script, pinning, upgrading, and removing them, tracking changes in git, upgrades, rollback, backups, and uninstalling) and a section for network operations staff (using the shared interpreter, a personal uv project or single-file uv script with the release wheel, or `NET*` variables without importing axlib; cron and systemd setup; rules for handling credentials).

## 1.0.2 - 2026-09-29

- Added `axlib netenv-set` and `axlib netenv-clear` (also `axlib-netenv-set`, `axlib-netenv-clear`, and `python -m axlib.credentials.netenv set|clear`). They print `export`/`unset` statements to `eval`, so an operator's `NETUSER`, `NETPASS`, and `NETENABLE` can be loaded into the shell at SSH login. `$USER` selects the record as in `ax.getkeys()`. Existing `NET*` variables are ignored during lookup, and the shared fallback is used only with `--allow-shared`. Values are quoted with `shlex.quote`, and the commands refuse to print shell code to a terminal.
- Added uv inline-metadata scripts `scripts/netenv-set` and `scripts/netenv-clear` that run from any directory without an activated environment.
- Added `examples/netenv/netenv.sh`, bash/zsh integration that defines `netenv-set`/`netenv-clear` shell functions and loads credentials in interactive shells only.
- Added `docs/NETENV.md`.

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
