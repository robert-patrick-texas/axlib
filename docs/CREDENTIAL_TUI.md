# Credential manager (TUI)

The axlib credential manager is a full-screen terminal application for people
who run network automation built on

```python
import axlib as ax

netuser, netpass, netenable = ax.getkeys()
```

but who do not write Python themselves. It adds, changes, and removes the
records those scripts read, and it works the same locally and over SSH.

The manager is an optional add-on. Everything it does is also available from
the Python API (`axlib.credentials.StoreAdmin`) and from the `axlib
credential-db` / `axlib credential-file` commands, and all three follow the
same rules because the TUI and the CLIs are thin layers over that one API.

## Install and start

Add the optional `tui` extra to your uv-managed project:

```bash
uv add 'axlib[tui]'
```

On a shared jump host where operators are not working inside a Python project,
`uv tool install 'axlib[tui]'` installs the `axlib` and `axlib-credential-tui`
commands on the `PATH` instead.

Start it with any of these equivalent commands:

```bash
axlib credential-tui --config /etc/axlib/axlib.toml
axlib-credential-tui --config /etc/axlib/axlib.toml
python -m axlib.credentials.tui --config /etc/axlib/axlib.toml
```

`--config` can be omitted when `AXLIB_CONFIG_FILE` is set. Other options:

| Option | Meaning |
| --- | --- |
| `--store db` / `--store file` | Store to open first. The default is SQLite when it is configured, matching the order `ax.getkeys()` uses. |
| `--idle-timeout MINUTES` | Close after this long without a key press or mouse action. Default `5`; `0` disables it. |

Over SSH, request a terminal so the full-screen display works:

```bash
ssh -t jumphost axlib credential-tui
```

## The screen

```text

 ╭─ axlib credential manager ─────────────────────────────────────────────────╮
 │ SQLite store  /var/lib/axlib/credentials.db                                │
 │ key /etc/axlib/sqlite-aes256.key  ·  4 records  ·  shared network-shared   │
 │                                                                            │
 │ / filter services                                                          │
 │                                                                            │
 │  SERVICE        FIELDS                     UPDATED     NOTES               │
 │  akumar         netuser                    2026-09-26  missing netpass     │
 │  infoblox       ibgrid,ibuser,ibpass       2026-09-26                      │
 │  jsmith         netuser,netpass,netenable  2026-09-26  you                 │
 │  networkshared  netuser,netpass,netenable  2026-09-26  shared fallback     │
 │                                                                            │
 │ a Add e Edit d Delete / Filter s Store k Rotate key r Refresh q Quit       │
 ╰───────────────────────────────────────────────────────────── SQLite store ─╯

```

The table shows field **names** only. Credential values are never displayed.

The **NOTES** column explains how `ax.getkeys()` will treat each record:

| Note | Meaning |
| --- | --- |
| `you` | This is your own record: `ax.getkeys()` looks up `$USER` first. |
| `shared fallback` | The configured `credentials.shared_service`, used when a personal record is missing or incomplete. |
| `missing netpass` | A required field is absent, so `ax.getkeys()` will fall back to the shared service for this person. |
| `SQLite overrides netpass` | Shown in the text-file store: SQLite is read first, so these fields are taken from SQLite, not from this record. |

## Keys

| Key | Action |
| --- | --- |
| `a` | Add a record. |
| `e` or `Enter` | Edit the highlighted record. |
| `d` | Delete the highlighted record. You must type its service name to confirm. |
| `/` | Filter the table by service name. `Esc` or `Enter` returns to the table. |
| `s` | Switch between the SQLite and text-file stores (when both are configured). |
| `k` | Rotate the store's encryption key. You must type `rotate` to confirm. |
| `i` | Initialize the store (shown only when the store is not ready). |
| `r` | Reload the table. |
| `q` or `Ctrl+Q` | Quit. |

Keys that do not apply right now are hidden from the bottom line.

Inside a form, `Tab` and `Shift+Tab` move between boxes, `Enter` moves to the
next box and saves from the last one, and `Esc` cancels. The mouse also works
in terminals that support it.

## Adding and editing records

The **Add** form asks for a service name and a profile:

- **Network device login** (`netuser`, `netpass`, optional `netenable`) is read
  by `ax.getkeys()`. Use your login name (for example `first.last`) as the
  service for a personal record, or the shared service name for the team
  account.
- **Infoblox grid API** (`ibgrid`, `ibuser`, `ibpass`) is read by
  `ax.getinfoblox()`, normally under the service name `infoblox`.

As you type the service name, the form shows the name it will be stored under.
Dots, dashes, and underscores are removed (`first.last` is stored as
`firstlast`), exactly as `ax.getkeys()` does when it looks the name up.

Passwords are typed twice. The **Edit** form pre-fills the username, leaves
passwords blank (a blank password keeps the current one), and offers a
**Remove** tick box for optional fields such as the enable secret.

Every change also clears that service from the Redis cache, when Redis caching
is enabled, so scripts pick up the new password immediately. If Redis cannot be
reached the change is still saved, and a yellow notice says how long the old
cached value may still be used.

## Setting up a new store

When the configured store does not exist yet, the manager explains what is
missing instead of showing an empty table. Press `i` to create it. When the
configured key file does not exist yet, the dialog offers to generate a new
random AES-256 key. Initialization never overwrites an existing key or store.

The store and key locations themselves come from the axlib TOML file (see
[CREDENTIALS.md](CREDENTIALS.md)); the manager does not edit configuration.

## Rotating the key

`k` re-encrypts every record under a new random key and then replaces the key
file. Anything else still holding a copy of the old key stops working, so
coordinate with any host that reads a copy of the store. If the rotation is
interrupted, the new key is kept beside the old one as `<key_file>.rotating`.
See [SQLITE_CREDENTIALS.md](SQLITE_CREDENTIALS.md#rotate_key) for recovery.
Rotation is refused when the key comes from `AXLIB_SQLITE_KEY` or
`AXLIB_CREDENTIAL_FILE_KEY`, because axlib cannot change the caller's
environment.

## Safety features

- **No values on screen.** Tables and messages show field names only. Password
  boxes show dots, and every password box is emptied as soon as a form closes.
- **No clipboard copies.** Copy and cut are disabled in password boxes. (Over
  SSH, Textual's clipboard support would otherwise send the text to your
  desktop clipboard.)
- **No keystroke logs.** The manager refuses to start when `TEXTUAL_LOG` is set
  or `TEXTUAL` enables `devtools`/`debug`, because those Textual debugging aids
  record every key press, including passwords.
- **No crash dumps of secrets.** Textual's crash report prints local variables,
  so the manager catches every error around code that handles secrets and shows
  a message instead.
- **Idle timeout.** The manager closes itself after five minutes without input
  (configurable), in case a session is left open on a shared host. Unsaved form
  contents are discarded.
- **Typed confirmation.** Deleting a record or rotating a key requires typing
  the service name or the word `rotate`.

## Colors

The display uses bright colors on a black background (white, cyan, blue,
magenta, orange, yellow, and green), chosen for the dark terminal themes most
operators use. The launcher matches the color depth to the terminal:

| Terminal advertises | Colors used |
| --- | --- |
| `COLORTERM=truecolor` or `24bit` | 16 million |
| `TERM=*256color*` (the usual case over SSH) | 256 |
| anything else | the 16 standard ANSI colors |

Set `TEXTUAL_COLOR_SYSTEM=truecolor`, `256`, or `standard` to override the
choice, or `NO_COLOR=1` for a monochrome display.

## Troubleshooting

| Message | What to do |
| --- | --- |
| `refusing to start: TEXTUAL_LOG is set` | `unset TEXTUAL_LOG` (or `unset TEXTUAL`) and start again. |
| `needs an interactive terminal` | Run it in a terminal; over SSH use `ssh -t`. For scripts use `axlib credential-db` / `axlib credential-file`. |
| `needs the optional 'tui' extra` | `uv add 'axlib[tui]'`. |
| `No SQLite store is configured` | Add a `[sqlite]` (or `[credential_file]`) section to the TOML file and pass `--config`. |
| Permission or ownership errors | Confirm you are in the configured group (default `netops`) and that the directories were created as described in [CREDENTIALS.md](CREDENTIALS.md#shared-linux-host-protection). |
| `Timed out waiting for ... lock` | Another operator is changing the store; press `r` to retry. |
