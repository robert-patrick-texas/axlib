# Changelog

## Unreleased

- Added `axlib md2html` (also `axlib-md2html` and `python -m axlib.md2html`), which converts Markdown files to HTML pages. One file becomes a page beside it (or `-o FILE`, or standard output); several files or a folder (`-r` for subfolders) become a small site, beside the Markdown or under `-o DIR`, and standard input becomes one page. Markdown is read as GitHub reads it: CommonMark plus tables, strikethrough, bare URLs as links, task-list check boxes, and footnotes. Headings get GitHub's anchors, so `FILE.md#section` links keep working.
- Every page has a sidebar listing every page converted together with the current page's `##` and `###` sections, an optional top bar (`--site-name`, repeatable `--link LABEL=URL`), and, when converted from a file, a footer naming its Markdown source (linked with `--source-url`). Pages need no JavaScript or internet access, follow the light or dark system setting, put the text first on a phone, and leave navigation out when printed. `--index FILE` writes `FILE` as `index.html`; `--fragment` writes only the converted HTML; `--escape-html` shows HTML in untrusted Markdown as text.
- Links between the converted files are rewritten to the HTML pages. Broken links, missing files, and `#section` links to headings that do not exist are reported as `file:line: message`; `--strict` turns them into exit status 1. Linked local files such as images are copied next to the pages when they go to another folder, except hidden files, files outside the folder the Markdown files share, and folders, which are reported instead. `--json` prints a report. Exit status: 0 converted, 1 link problems with `--strict`, 2 error.
- Added the Python API `axlib.md2html`: `markdown_to_html()`, `convert_text()`, `convert_paths()`, `build_site()`, `write_site()`, `SiteOptions`, `parse_markdown()`, `slugify()`, and `render_page()`.
- Added the optional extra `markdown` (`uv add 'axlib[markdown]'`): `markdown-it-py` with `linkify-it-py`, and `mdit-py-plugins`. `install.sh --with-markdown` installs it and `--no-markdown` removes it; the choice is recorded as `WITH_MARKDOWN`.
- The documentation is published as a website, <https://robert-patrick-texas.github.io/axlib/>, built with `axlib md2html`. `make docs` builds it in `site/`; `.github/workflows/docs.yml` builds it from each published release's tag and deploys it to GitHub Pages. Each page has the GitHub repository and PyPI project links at the top. The package's Documentation URL now points at the site, and a test checks every link and `#section` in the guides.
- Reviewed every guide against the code. Corrections include the shared-service fallback in `ax.getkeys()` (only when `netuser` or `netpass` is missing), the safe recovery after an interrupted `rotate-key` (the old advice could delete the only working key), `generate_sqlite_key_file(overwrite=True)`, the RADIUS password-option rules and JSON layout, `axlib parse diff` exit status when commands fail, and text-filter details such as case-sensitive include paths. Added a settings reference with every TOML key and `AXLIB_*` variable, the Redis cache, the `axlib credentials` check, the legacy lookup helpers, both stores' command lines, the full Python APIs of `axlib.credentials`, `axlib.parse`, `axlib.tf`, `axlib.radius`, and `axlib.credentials.netenv`, installer setup for the TUI and the login snippet, a Commands table in the README, and Makefile, source layout, release, and installer-testing sections in `docs/DEVELOPMENT.md`.
- `examples/axlib.toml.example` shows the Redis ACL user and TLS settings.
- Fixed: `axlib parse` now parses a command run from a configuration mode with `do` (`sw1(config)#do show ip int br`); before, it was skipped as having no template.
- Fixed: `axlib credentials` and the `axlib tf` filters name themselves in usage lines (`usage: axlib tf comments ...` instead of `usage: axlib ...`) and in error messages (`axlib tf whitespace: ...` instead of `rmwhite: ...`).
- Fixed: `axlib tf variables` kept the second of three assignments to a name given in different cases (`-v host=a -v Host=b -v host=c` gave `b`); the last one now wins, as documented.
- Changed `axlib netenv-set`: for a record without an enable secret it no longer prints `unset NETENABLE`, so its output holds only `export` lines. The new `--unset` option prints it again (`render_exports(values, unset_missing=True)` in Python); a record with an enable secret still gives `export NETENABLE=...` either way. The `netenv-set` shell function in `examples/netenv/netenv.sh` (also `/etc/profile.d/axlib-netenv.sh`) passes `--unset`, so shells that use it still drop an enable secret left by an earlier record. A script that runs `eval "$(axlib netenv-set)"` itself now keeps an existing `NETENABLE` unless it adds `--unset`.
- Fixed credential handling:
  - A key file or database named by a relative path in `axlib.toml` that is a symlink is now refused like any other symlink; resolving the whole path used to follow the link to its target.
  - `axlib credentials` prints one line and exits 2 when the configuration file is missing or unreadable, instead of a Python traceback.
  - A configuration file that exists but cannot be read (such as `/etc/axlib/axlib.toml` for a user outside `netops`) is reported as `cannot be read: PATH (Permission denied)` by every tool, not as `does not exist`; a file that is not UTF-8 is an error instead of a traceback.
  - A symlinked, non-regular, or unfixable SQLite key file is reported as itself by `list`, `add`, and `read`, instead of as `key-check metadata is invalid`. Messages say `SQLite`, not `Sqlite`.
  - `rotate-key --dry-run`, the TUI's `k`, and the rotation itself make the same checks (a key from the environment, no key file, a leftover `.rotating` file), so a dry run no longer passes what the rotation then refuses. The checks are `check_key_rotation()` on both stores and `StoreAdmin`.
  - `--prompt FIELD` names the field when input ends, instead of printing an empty `error:`.
  - Key generation and store initialization check the owner and group before creating a missing parent directory, so a typo no longer leaves an empty directory behind.
  - The text store reports a record without a `service` line as a damaged store in `list` and `rotate-key`, instead of a `KeyError`.
  - The TUI empties password boxes whenever a form closes, including Cancel and Escape, not only on Save.
- Fixed `install.sh`:
  - `--no-store` writes `enabled = false` under `[sqlite]` in a new `axlib.toml`; it wrote `enabled = true`, so every lookup warned that the database did not exist.
  - `current` moves to the new release only after the environment runs it. A failed build or `uv add` left `current` naming a release that was not installed.
  - A failure before `uv add` finishes restores the project's `pyproject.toml` and `uv.lock` (which `uv remove` had changed when a part was turned off) and removes the copy and wheel of the release that was never installed.
  - A failing store health check at the end prints a warning and the command to rerun it, then the summary; the installer used to stop with status 1 after installing everything.
  - Every `AXLIB_*` variable in root's environment is ignored while it runs. Only four were, so `sudo -E` or `sudo -i` could give the new store the wrong group, modes, or a Redis cache.
- Fixed `axlib parse diff`: a command that failed to parse on either side now makes the exit status 2 and is counted in the summary ("N failed to parse and could not be compared") and in `--json` (`failures`, `summary.failed`); the comparison used to exit 0, printing "No differences in 0 commands." when nothing was compared. `SnapshotDiff` has `before_failures`, `after_failures`, and `failures`.
- Fixed `axlib parse diff` and `load_snapshot()`: a truncated snapshot (`not valid JSON`) or another JSON file (`JSON but not an axlib parse snapshot`) is an error, exit 2. Both used to be read as a capture with no commands.
- `axlib parse` reports skipped commands as "skipped N command(s) (-v lists them and why)"; "with no template" was wrong for filtered output and binary files.
- Fixed `axlib radius`: end of input at the password or secret prompt prints what was being read and how to give it in a script, exit 2, instead of an empty `radius:` message. `--password-stdin` with no input at all is an error instead of sending an empty password; an empty line is still an empty password.
- Fixed text filters:
  - `triple-quotes` removes spans that cross lines in CRLF and CR-only text; it used to leave them and turn each `\n` inside into a space.
  - `include` and `comments` report undecodable input as an error with status 1, like the other filters, instead of a usage message with status 2.
  - `include` ends an included file that lacks a final line ending with the directive line's ending, so it no longer runs into the next line.
  - `include` blocks a cycle back to the input file the first time; the file's content used to repeat once. `expand_includes()` takes `source_path=` for this, and a cycle is blocked even with `--no-recursive` or at the depth limit.
  - `whitespace-safe` ends quoted text at CR as well as LF line endings.

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
