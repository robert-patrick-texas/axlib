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

On a shared host set up with `install.sh`, the administrator adds the manager
to the shared environment with `--with-tui` (and removes it with `--no-tui`;
see [INSTALL.md](INSTALL.md)):

```bash
sudo ./axlib-<version>/install.sh --with-tui
```

On another jump host where operators are not working inside a Python project,
`uv tool install 'axlib[tui]'` installs the `axlib` and `axlib-credential-tui`
commands on the `PATH` instead.

Start it with any of these equivalent commands:

```bash
axlib credential-tui --config /etc/axlib/axlib.toml
axlib-credential-tui --config /etc/axlib/axlib.toml
python -m axlib.credentials.tui --config /etc/axlib/axlib.toml
```

`install.sh` puts only the `axlib` command on the `PATH`, so on those hosts use
`axlib credential-tui`.

`--config` can be omitted when `AXLIB_CONFIG_FILE` is set, or on a host installed with `install.sh`, which records its configuration file in `/etc/axlib/install.env`. Other options:

| Option | Meaning |
| --- | --- |
| `--store db` / `--store file` | Store to open first. The default is SQLite when it is configured, matching the order `ax.getkeys()` uses. |
| `--idle-timeout MINUTES` | Close after this long without a key press or mouse action. Default `5`; `0` disables it. Fractions such as `0.5` are allowed; a negative value is refused. |

The command exits with status 0 when you quit or the idle timeout closes it,
1 when it cannot start (see [Troubleshooting](#troubleshooting)), and 2 for an
invalid option.

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
 │ / filter services and notes                                                │
 │                                                                            │
 │  SERVICE        FIELDS                     UPDATED     ADVICE           NO │
 │  akumar         netuser                    2026-09-26  missing netpass     │
 │  infoblox       ibgrid,ibuser,ibpass       2026-09-26                   gr │
 │  jsmith         netuser,netpass,netenable  2026-09-26  you              co │
 │  networkshared  netuser,netpass,netenable  2026-09-26  shared fallback  la │
 │                                                                            │
 │                                                                            │
 │ Note  core routers, DC1 and DC2                                            │
 │ a Add e Edit d Delete / Filter s Store k Rotate key r Refresh q Quit       │
 ╰───────────────────────────────────────────────────────────── SQLite store ─╯

```

This is an 80-column terminal with the `jsmith` row highlighted. The two lines
at the top name the store and its file, then where the AES key comes from (a
key file, or `AXLIB_SQLITE_KEY` / `AXLIB_CREDENTIAL_FILE_KEY`), the number of
records, and the configured shared service. A store that is configured but has
`enabled = false` is marked `disabled: ax.getkeys() skips this store`. The
bottom border names the store on screen.

The table shows field **names** only. Credential values are never displayed.
The one value it shows is each record's **note**: free text that you write,
such as "core routers, DC1 and DC2". The **NOTE** column holds up to 40
characters of it (a longer note is cut and ends in `…`), and on a narrow
terminal it continues off the right edge, with a scroll bar under the table;
the `Left` and `Right` arrow keys scroll the table sideways. The line below the
table always shows the whole note of the highlighted record.

The **ADVICE** column is written by axlib, not by you. It explains how
`ax.getkeys()` will treat each record:

| Advice | Meaning |
| --- | --- |
| `you` | This is your own record: `ax.getkeys()` looks up `$USER` first. |
| `shared fallback` | The configured `credentials.shared_service`, used when a personal record is missing or incomplete. |
| `missing netpass` | A required field is absent. For a network device login record, `ax.getkeys()` will fall back to the shared service for this person. |
| `SQLite overrides netpass` | Shown in the text-file store: SQLite is read first, so these fields are taken from SQLite, not from this record. |

## Keys

| Key | Action |
| --- | --- |
| `Up` / `Down` | Move the highlight. `Left` / `Right` scroll the table sideways. |
| `a` | Add a record. |
| `e` or `Enter` | Edit the highlighted record. |
| `d` | Delete the highlighted record. You must type its service name to confirm. |
| `/` | Filter the table by service name or note text. `Esc` or `Enter` returns to the table. |
| `s` | Switch between the SQLite and text-file stores (when both are configured). |
| `k` | Rotate the store's encryption key. You must type `rotate` to confirm. |
| `i` | Initialize the store (shown only when the store is not ready). |
| `r` | Reload the table. |
| `q` or `Ctrl+Q` | Quit. `Ctrl+C` does not quit; it shows a reminder to press `Ctrl+Q`. |

Keys that do not apply right now are hidden from the bottom line and do
nothing.

Inside a form, `Tab` and `Shift+Tab` move between boxes, `Enter` moves to the
next box and saves from the last one, and `Esc` cancels. On the **Profile**
line, `Enter` opens the list of profiles instead; choose one with the arrow
keys and `Enter`, then press `Tab` to move on. The mouse also works in
terminals that support it.

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
`firstlast`), exactly as `ax.getkeys()` does when it looks the name up. A name
with spaces is refused, and a name that already exists is reported so you can
cancel and use **Edit** instead. Fields marked `*` are required.

Every profile also has an optional **Note**: one line of up to 120
characters, such as who owns a shared account or when a lab account expires.
`ax.getkeys()` and `axlib netenv-set` never read it. It is encrypted with the
rest of the record but is not secret, so the table, the edit form, and
`axlib credential-db list` show it.

Passwords are typed twice, and the form will not save while the two entries
differ. The **Edit** form pre-fills the fields that are not secret (the
username, the Infoblox grid host and API username, and the note) and leaves
passwords blank (a blank password keeps the current one). Clearing the note
removes it. When the record has an enable secret, a **Remove enable secret**
tick box removes it. The service name and profile cannot be changed in the
**Edit** form; add a new record and delete the old one instead.

Every change also clears that service from the Redis cache, when Redis caching
is enabled, so scripts pick up the new password immediately. If Redis cannot be
reached the change is still saved, and a yellow notice says how long the old
cached value may still be used.

## Setting up a new store

When the configured store does not exist yet, the manager explains what is
missing instead of showing an empty table. Press `i` to create it. When the
configured key file does not exist yet, the dialog offers to generate a new
random AES-256 key (the box is ticked). Initialization never overwrites an
existing key or store.

When the store exists but cannot be opened, for example because the key does
not match it, the manager shows the error instead. Fix the cause and press `r`
to check again; `i` re-verifies the existing store with the configured key.

The store and key locations themselves come from the axlib TOML file (see
[CREDENTIALS.md](CREDENTIALS.md)); the manager does not edit configuration.

## Rotating the key

`k` re-encrypts every record under a new random key and then replaces the key
file. Anything else still holding a copy of the old key stops working, so
coordinate with any host that reads a copy of the store. If the rotation is
interrupted, the new key is kept beside the old one as `<key_file>.rotating`.
See [SQLITE_CREDENTIALS.md](SQLITE_CREDENTIALS.md#rotate_key) for recovery.
Before asking for confirmation, `k` makes the same checks as the rotation
itself and shows the reason in red if one fails: the key comes from
`AXLIB_SQLITE_KEY` or `AXLIB_CREDENTIAL_FILE_KEY` (axlib cannot change the
caller's environment), no key file is configured, or a `<key_file>.rotating`
file is left from an interrupted rotation (recover first, as linked above).

## Safety features

- **No values on screen.** Tables and messages show field names only. Password
  boxes show dots. They are emptied whenever a form closes: when you save it,
  press **Cancel**, or press `Esc`.
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
  (configurable), in case a session is left open on a shared host. It checks
  every 15 seconds, discards unsaved form contents, and prints
  `axlib credential manager closed after 5 minute(s) without input.`
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

Messages that stop the manager from starting are printed after
`axlib credential-tui:`. Problems found while it runs appear in a red
**Problem** notice for a few seconds.

| Message | What to do |
| --- | --- |
| `TEXTUAL_LOG is set, so Textual would write every keystroke` | `unset TEXTUAL_LOG` and start again. |
| `TEXTUAL enables devtools/debug mode` | `unset TEXTUAL` and start again. |
| `needs an interactive terminal` | Run it in a terminal; over SSH use `ssh -t`. For scripts use `axlib credential-db` / `axlib credential-file`. |
| `needs the optional 'tui' extra` | `uv add 'axlib[tui]'`, or on a host set up with `install.sh`, ask the administrator to run it with `--with-tui`. |
| `Credential configuration file does not exist` | The path given with `--config` or in `AXLIB_CONFIG_FILE` is wrong. |
| `Credential configuration file cannot be read` | The file exists, but you may not read it or enter its directory; the reason is in parentheses. On a host set up with `install.sh`, `id` must list the group that owns `/etc/axlib`. |
| `No SQLite store is configured` | Add a `[sqlite]` (or `[credential_file]`) section to the TOML file, then pass `--config` or set `AXLIB_CONFIG_FILE`. |
| Permission or ownership errors | Confirm you are in the configured group (default `netops`) and that the directories were created as described in [CREDENTIALS.md](CREDENTIALS.md#shared-linux-host-protection). |
| `Timed out waiting for encrypted credential-file lock` or `database is locked` | Another operator is changing the store; press `r` to retry. |
