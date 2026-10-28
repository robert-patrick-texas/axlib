# axlib

Axlib is an educational Python package for Network Operations staff building automation scripts. It combines pipeline-friendly text filters with reusable credential helpers that can resolve operator credentials without embedding passwords in source code.

- Documentation: <https://robert-patrick-texas.github.io/axlib/>
- Source code: <https://github.com/robert-patrick-texas/axlib>
- Package: <https://pypi.org/project/axlib/>

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
- `axlib parse` parses show-command output with TextFSM (the ntc-templates collection plus your own `.textfsm` files), and `axlib parse diff` compares collections taken before and after a change record by record, so neighbors listed in another order or hold times that ticked down are not reported as changes.
- `axlib md2html` converts Markdown runbooks and notes into HTML pages with a sidebar listing every page and section, and checks every link between them. This documentation site is built with it.

## Install

This project uses [uv](https://docs.astral.sh/uv/). Add axlib to your project:

```bash
uv add axlib
```

Add the optional full-screen credential manager as well:

```bash
uv add 'axlib[tui]'
```

Add show-command parsing (`axlib parse`):

```bash
uv add 'axlib[textfsm]'
```

Add Markdown-to-HTML conversion (`axlib md2html`):

```bash
uv add 'axlib[markdown]'
```

To install axlib for every operator on a shared Linux host (a shared Python in `/opt/shared/python`, axlib in `/opt/shared/axlib`, and a ready `netops` credential store), unpack a release tarball and run its installer:

```bash
tar xzf axlib-<version>.tar.gz
sudo ./axlib-<version>/install.sh
```

The installer records its options in `/etc/axlib/install.env`, so the `axlib` command finds `/etc/axlib/axlib.toml` without `--config`, and upgrades need no options. `--with-tui`, `--with-textfsm`, and `--with-markdown` add the optional parts below, and `--with-all` adds every one of them, including any that a later release adds. Administrators add libraries such as netmiko for every script with `sudo axuv add netmiko`; `axuv` runs uv on the shared environment with the installer's settings. Later, `sudo /opt/shared/axlib/current/install.sh update` upgrades to the latest GitHub release after checking its SHA-256 digest, and `update --dry-run` only reports whether there is one; `--proxy URL` sends the downloads through a proxy. See [docs/INSTALL.md](docs/INSTALL.md) for options, upgrades, rollback, and backups.

Runtime dependencies are limited to `cryptography` and `redis`. Redis is imported only when caching is enabled. The optional `tui` extra adds `textual` and `rich`, the optional `textfsm` extra adds `textfsm` and `ntc-templates`, and the optional `markdown` extra adds `markdown-it-py`, `linkify-it-py`, and `mdit-py-plugins`.

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

It lists each service's field names and note, with advice explaining how `ax.getkeys()` will use them, and it adds, edits, and deletes records, initializes stores, and rotates keys. Credential values are never displayed. See [docs/CREDENTIAL_TUI.md](docs/CREDENTIAL_TUI.md).

## Session credentials at login

Operators can have their network login exported into every SSH session:

```bash
eval "$(axlib netenv-set)"      # export NETUSER, NETPASS, NETENABLE from $USER's record
eval "$(axlib netenv-clear)"    # unset them
```

`examples/netenv/netenv.sh` wraps both in the `netenv-set` and `netenv-clear` shell functions and loads credentials when an interactive bash or zsh shell starts. The uv scripts `scripts/netenv-set` and `scripts/netenv-clear` run the same commands from a checkout, from any directory. Read the warnings in [docs/NETENV.md](docs/NETENV.md) before enabling this: exported passwords are visible to every program the shell starts.

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

It works over IPv4 and IPv6: a server name with both kinds of address is tried at each in turn until one answers, and `-4` or `-6` uses one only. The exit status is 0 for Access-Accept, 1 for Reject or Challenge, 2 for errors, and 3 when no reply arrives. `--json` prints the full result. The module uses only the standard library and is checked against the RFC 2865 test vectors. See [docs/RADIUS.md](docs/RADIUS.md).

## Show-command parsing

Collect show commands before and after a maintenance window, then see only what changed:

```bash
axlib parse -p cisco_ios -r captures/before/ -o before.json
axlib parse diff -p cisco_ios -r before.json captures/after/
```

```text
sw1: show cdp neighbors
  - local_interface=GigabitEthernet1/0/5 neighbor_name=ap-lobby-01
  ~ local_interface=GigabitEthernet1/0/48 neighbor_name=core-rtr-01.example.net
      neighbor_interface: GigabitEthernet0/0/1 -> GigabitEthernet0/0/2
```

Captures can be session logs with prompts (`sw1#show cdp neighbors`), script output with bare commands, whole directory trees (`-r`), or output piped from `ssh` (`--command`). Abbreviated commands such as `sh cdp nei` are recognized. Records are sorted by what identifies them, interface names are spelled one way (`Gig 1/0/48`, `Gi1/0/48` → `GigabitEthernet1/0/48`), and fields that change on every run are dropped. To add a template, put `cisco_ios_show_ip_pim_neighbor.textfsm` (platform, then the command) in a template directory: `--template-dir`, `AXLIB_TEXTFSM_TEMPLATES`, or `/etc/axlib/textfsm` on a shared server. Template directories are searched before ntc-templates.

```python
from axlib.parse import parse_output

records = parse_output(
    conn.send_command("show cdp neighbors"), "show cdp neighbors", "cisco_ios"
)
```

See [docs/PARSING.md](docs/PARSING.md). For output collected with karvi, see [docs/KARVI.md](docs/KARVI.md).

## Markdown to HTML

Turn a runbook into a page to attach to a ticket, or a folder of notes into a small site:

```bash
axlib md2html change-plan.md                  # writes change-plan.html beside it
axlib md2html -r runbooks -o site --index runbooks/README.md --site-name "NOC runbooks"
```

The Markdown is read as GitHub reads it (tables, task lists, footnotes, and bare URLs as links), and headings get GitHub's anchors. Links between the Markdown files are rewritten to point at the HTML pages, and broken links and missing `#section` anchors are reported as `file:line: message`. Every page has a sidebar listing the pages and the current page's sections, follows the reader's light or dark setting, and needs no web server. `markdown_to_html()` and `convert_paths()` do the same from Python. See [docs/MARKDOWN.md](docs/MARKDOWN.md).

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

See [docs/TEXT_FILTERS.md](docs/TEXT_FILTERS.md) for filter semantics.

## Commands

Every command is a subcommand of `axlib` (also `python -m axlib`), and most also have their own name and a `python -m` module. The modules in the last row run only with `python -m`:

| Command | Other names | Purpose | Guide |
| --- | --- | --- | --- |
| `axlib version` (or `-V`, `--version`) | | Print the installed version. | |
| `axlib credentials network`, `axlib credentials infoblox` | `python -m axlib.secrets` | Check which credential fields resolve, without printing them. | [CREDENTIALS.md](docs/CREDENTIALS.md) |
| `axlib credential-db` | `axlib-credential-db`, `python -m axlib.credentials.sqlite_cli` | Manage the encrypted SQLite store. | [SQLITE_CREDENTIALS.md](docs/SQLITE_CREDENTIALS.md) |
| `axlib credential-file` | `axlib-credential-file`, `python -m axlib.credentials.file_cli` | Manage the encrypted text store. | [CREDENTIAL_FILE.md](docs/CREDENTIAL_FILE.md) |
| `axlib credential-tui` | `axlib-credential-tui`, `python -m axlib.credentials.tui` | Full-screen credential manager (`tui` extra). | [CREDENTIAL_TUI.md](docs/CREDENTIAL_TUI.md) |
| `axlib netenv-set`, `axlib netenv-clear` | `axlib-netenv-set`, `axlib-netenv-clear`, `python -m axlib.credentials.netenv set` (or `clear`) | Load or clear `NETUSER`, `NETPASS`, and `NETENABLE` in a shell. | [NETENV.md](docs/NETENV.md) |
| `axlib radius` | `axlib-radius`, `python -m axlib.radius` | Check a login against a RADIUS server. | [RADIUS.md](docs/RADIUS.md) |
| `axlib parse`, `axlib parse diff`, `axlib parse templates`, `axlib parse split` | `axlib-parse`, `python -m axlib.parse` | Parse and compare show-command output (`textfsm` extra). | [PARSING.md](docs/PARSING.md) |
| `axlib md2html` | `axlib-md2html`, `python -m axlib.md2html` | Convert Markdown to HTML pages (`markdown` extra). | [MARKDOWN.md](docs/MARKDOWN.md) |
| `axlib tf FILTER` | `axlib-tf`, `axlib-tf-FILTER`, `python -m axlib.tf` | Text filters for pipelines. | [TEXT_FILTERS.md](docs/TEXT_FILTERS.md) |
| | `python -m axlib.credentials.admin`, `python -m axlib.credentials.profiles` | Check every configured store; list record profiles. | [CREDENTIALS.md](docs/CREDENTIALS.md) |

`install.sh` puts only `axlib` on the `PATH`; the other names are in the environment's `bin` directory.

## Documentation

The guides below are also published as a website, <https://robert-patrick-texas.github.io/axlib/>, for each release.

- [docs/INSTALL.md](docs/INSTALL.md) — shared-host installation with `install.sh`: layout, options, upgrades, rollback, backups.
- [docs/CREDENTIALS.md](docs/CREDENTIALS.md) — lookup precedence, configuration and every setting, the Redis cache, the CLIs, the administration API, and permissions.
- [docs/CREDENTIAL_TUI.md](docs/CREDENTIAL_TUI.md) — operator guide for the optional full-screen credential manager.
- [docs/NETENV.md](docs/NETENV.md) — loading credentials into SSH sessions with `netenv-set` / `netenv-clear`: setup, warnings, and troubleshooting.
- [docs/RADIUS.md](docs/RADIUS.md) — checking a login against a RADIUS server with `axlib radius`: options, exit status, Python API, how it works, and troubleshooting.
- [docs/PARSING.md](docs/PARSING.md) — parsing show commands with `axlib parse` and comparing collections with `axlib parse diff`: capture formats, output, adding your own TextFSM templates, Python API, and troubleshooting.
- [docs/KARVI.md](docs/KARVI.md) — collecting with karvi and parsing with axlib: platform names, output as tables, JSON lines, and snapshots, how Cisco output is normalized, and comparing interface status, routes, ARP, and MAC tables before and after a change.
- [docs/MARKDOWN.md](docs/MARKDOWN.md) — converting Markdown to HTML pages with `axlib md2html`: what is converted, the page layout, link checking, Python API, and publishing a site.
- [docs/CREDENTIAL_FILE.md](docs/CREDENTIAL_FILE.md) — the encrypted text store: its file format, every Python method with an example, and its command line.
- [docs/SQLITE_CREDENTIALS.md](docs/SQLITE_CREDENTIALS.md) — the encrypted SQLite store: its database layout, every Python method with an example, and its command line.
- [docs/TEXT_FILTERS.md](docs/TEXT_FILTERS.md) — pipeline and Python text-processing examples.
- [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md) — development, testing, documentation, and release guidance.
- [examples/parse/README.md](examples/parse/README.md) and [examples/text/README.md](examples/text/README.md) — sample files to try `axlib parse` and the text filters on.
- [examples/axlib.toml.example](examples/axlib.toml.example) — a configuration file with every section.
- [RELEASE_NOTES.md](RELEASE_NOTES.md) and [CHANGELOG.md](CHANGELOG.md) — what changed in each release.

The source intentionally includes detailed module/function docstrings and comments explaining both what Python constructs do and why they are useful in network automation. The package ships type hints (`py.typed`), so editors and type checkers see every signature.
