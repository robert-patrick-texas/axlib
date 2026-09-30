# Session credentials: `netenv-set` and `netenv-clear`

`axlib netenv-set` loads your network login from the axlib credential store
into your shell as three environment variables:

| Variable | Store field | Meaning |
| --- | --- | --- |
| `NETUSER` | `netuser` | Device login username (required) |
| `NETPASS` | `netpass` | Device login password (required) |
| `NETENABLE` | `netenable` | Enable secret (optional; unset when the record has none) |

`axlib netenv-clear` removes all three. With the shell integration installed,
this happens automatically each time you SSH in, and every tool you start
afterwards (`ax.getkeys()` scripts, Netmiko, Ansible, your own one-liners)
sees the same login without prompting.

Read [Warnings](#warnings) before rolling this out. Holding a password in
environment variables is convenient, but it is less protected than leaving it
in the encrypted store.

## Quick start

```bash
# Once, in ~/.bashrc (or ~/.zshrc):
export AXLIB_CONFIG_FILE=/etc/axlib/axlib.toml
source /opt/axlib/examples/netenv/netenv.sh

# After that, in any interactive shell:
netenv-set            # load (runs automatically when the shell starts)
netenv-set --check    # show which fields would load, never their values
netenv-clear          # remove NETUSER, NETPASS, NETENABLE
```

Your record must already exist. An administrator creates it with
`axlib credential-db add --service "$USER" ...`, or you can use
`axlib credential-tui` (see `docs/CREDENTIALS.md` and `docs/CREDENTIAL_TUI.md`).

## Why `eval`?

A program cannot change the environment of the shell that started it. Each
program gets a *copy* of the environment, and that copy disappears when the
program exits. So `axlib netenv-set` does not set anything itself. It
**prints** shell statements, and your shell runs them:

```console
$ axlib netenv-set | cat          # only to show the format; do not do this
export NETUSER=first.last
export NETPASS='S3cret pa$$'
unset NETENABLE
```

```bash
eval "$(axlib netenv-set)"        # what actually loads the variables
eval "$(axlib netenv-clear)"      # unset NETUSER NETPASS NETENABLE
```

The shell functions `netenv-set` and `netenv-clear` from
`examples/netenv/netenv.sh` do the `eval` for you. Use them rather than
typing `eval` by hand.

**`eval "$(...)"` hides the exit status.** `eval` with an empty string returns
0, so `eval "$(axlib netenv-set)" && run-job` runs the job even when no
credentials were loaded. In scripts, capture the output first and `eval` it
only on success. The shell functions already do this and return the real
status:

```bash
if code=$(axlib netenv-set --quiet); then
    eval "$code"
else
    echo "no network credentials; stopping" >&2
    exit 1
fi
```

Guarantees that follow from this design:

- **Standard output carries only shell code.** Every message, warning, and
  `--check` report goes to standard error, so nothing unexpected is ever
  `eval`'d.
- **Every value is quoted with Python's `shlex.quote`.** A password containing
  `$`, quotes, backticks, `;`, spaces, or a newline is assigned literally and is
  never run as a command. Leading and trailing spaces are kept.
- **Output is all or nothing.** If the lookup fails, nothing is printed on
  standard output, and your existing variables are left exactly as they were.
- **It will not print your password to the screen.** If standard output is a
  terminal (you typed `axlib netenv-set` without `eval`), the command refuses,
  shows the correct usage, and exits with status 3.

## Installation

There are two ways to run the commands. Both use the same code.

### Development checkout: the uv scripts

`scripts/netenv-set` and `scripts/netenv-clear` are standalone uv scripts. Their
first line is `#!/usr/bin/env -S uv run --quiet --script`, and a PEP 723
`# /// script` block tells uv to install axlib from the checkout the script
lives in (`path = ".."`). You don't need to activate a virtual environment,
and they work from any working directory:

```bash
cd /tmp
eval "$(/opt/axlib/scripts/netenv-set)"
```

To use them from the shell integration, point `AXLIB_NETENV_SCRIPTS` at the
directory:

```bash
export AXLIB_NETENV_SCRIPTS=/opt/axlib/scripts
```

Things to know about the uv scripts:

- **Call them by their real path. Do not symlink them** into `~/bin` or
  `/usr/local/bin`. uv resolves `path = ".."` relative to the directory it was
  called from, so a symlink points uv at the wrong place and fails with
  `path could not be normalized`. Use `AXLIB_NETENV_SCRIPTS`, an alias, or the
  full path instead.
- **The first run builds a cached environment** under `~/.cache/uv` (or
  `$UV_CACHE_DIR`). It can take a few seconds and needs access to the package
  index for `cryptography` and `redis`. After that, each run takes a fraction
  of a second. If the build fails at login, uv prints the error, nothing is
  exported, and the login continues.
- Because the install is editable, changes in the checkout apply on the next
  run.
- If you copy a script out of the checkout, edit its `[tool.uv.sources]` line
  to point at the checkout or a built wheel.

### Installed package: `axlib netenv-set`

When axlib is installed in an environment on your `PATH` (`uv add axlib` in a
project, then activate its `.venv`), use the subcommands:

```bash
eval "$(axlib netenv-set)"
eval "$(axlib netenv-clear)"
```

The same commands are also available as `axlib-netenv-set`,
`axlib-netenv-clear`, and `python -m axlib.credentials.netenv set|clear`.
The shell integration uses `axlib` whenever `AXLIB_NETENV_SCRIPTS` is unset.

From a development checkout without activating anything:

```bash
eval "$(uv run --quiet --project /opt/axlib axlib netenv-set)"
```

### Shell integration: loading at login

`examples/netenv/netenv.sh` works in bash and zsh. It defines the
`netenv-set` and `netenv-clear` functions and, in interactive shells only,
runs `netenv-set --quiet` once when the shell starts.

For one operator, add to `~/.bashrc` or `~/.zshrc`:

```bash
export AXLIB_CONFIG_FILE=/etc/axlib/axlib.toml
export AXLIB_NETENV_SCRIPTS=/opt/axlib/scripts   # omit if `axlib` is on PATH
source /opt/axlib/examples/netenv/netenv.sh
```

For every operator on a host, install it system-wide:

```bash
sudo install -m 0644 /opt/axlib/examples/netenv/netenv.sh /etc/profile.d/axlib-netenv.sh
# and set AXLIB_CONFIG_FILE (and AXLIB_NETENV_SCRIPTS) in /etc/profile.d before it,
# e.g. /etc/profile.d/axlib-00-config.sh
```

Settings read by the snippet:

| Variable | Default | Purpose |
| --- | --- | --- |
| `AXLIB_CONFIG_FILE` | the file recorded by `install.sh` | The `axlib.toml` that enables your stores. **Required** unless axlib was installed with `install.sh` (which records the file in `/etc/axlib/install.env`) or each store is enabled with `AXLIB_*` variables. |
| `AXLIB_NETENV_SCRIPTS` | unset | Directory containing the uv scripts. When unset, the installed `axlib` command is used. |
| `AXLIB_NETENV_AUTOLOAD` | `1` | Set to `0` to define the functions without loading credentials at login. |

Notes on the snippet:

- It runs only when the shell is **interactive** (`$-` contains `i`). bash
  also reads `~/.bashrc` for `ssh host command`, `scp`, `sftp`, and `rsync`.
  Those sessions stay silent and don't receive credentials. Anything printed
  in those sessions can corrupt a file transfer.
- It pauses `set -x` tracing while it runs `eval`, because tracing would echo
  the `export NETPASS=...` line to your terminal.
- In POSIX `sh`, which also reads `/etc/profile.d`, it returns without doing
  anything, because `netenv-set` is not a legal function name there.
- SSH logins start a **login** shell, which reads `~/.bash_profile` rather than
  `~/.bashrc`. Most distributions' default `~/.bash_profile` sources
  `~/.bashrc`. If yours does not, add `[ -f ~/.bashrc ] && . ~/.bashrc` to it.

## Command reference

### `axlib netenv-set [options]`

| Option | Effect |
| --- | --- |
| `--config PATH` | Use this `axlib.toml` instead of `$AXLIB_CONFIG_FILE` or the one recorded by `install.sh`. |
| `--service NAME` | Load this record instead of the one named by `$USER`, e.g. a lab account. |
| `--allow-shared` | If your record is missing or incomplete, use the configured shared service (`credentials.shared_service` / `AXLIB_SHARED_SERVICE`) and print a warning. Off by default. |
| `--check` | Report on standard error which variables would be set or missing. Values are never shown, and nothing is exported. |
| `-q`, `--quiet` | Hide the success and "not found" notices. Warnings and errors still print. |

| Exit status | Meaning | Environment |
| --- | --- | --- |
| `0` | Credentials exported (or `--check` found a complete record) | Replaced |
| `1` | No complete record (`netuser` and `netpass`) for the service | Unchanged |
| `2` | Configuration or store error: no store enabled, missing config file, wrong key, unreadable database | Unchanged |
| `3` | Refused because standard output is a terminal | Unchanged |

### `axlib netenv-clear`

Prints `unset NETUSER NETPASS NETENABLE`. It has no options, never reads the
store, and refuses to print to a terminal (exit status 3), for the same
reason as `netenv-set`: if you ran it without `eval`, it would look like it
worked while nothing was cleared.

## Which record is loaded

- **`$USER` chooses the record**, exactly as `ax.getkeys()` does, so the
  variables match what your scripts would read from the store. If `USER` is not
  set, your login account name is used. If `USER` differs from your login
  account, a warning is printed and `USER` still wins.
- **Service names are normalized**: dots, dashes, and underscores are removed,
  so `first.last`, `first-last`, and `firstlast` are the same record.
- **The lookup order is Redis, then SQLite, then the encrypted text file**,
  whichever are enabled in your configuration.
- **Existing `NET*` variables are ignored during the lookup.** Every run reads
  the store fresh, so a stale value from an earlier session can't look like a
  successful load. `ax.getkeys()` works differently: it returns
  variables that are already set.
- **There is no silent shared-account fallback.** `ax.getkeys()` falls back to
  the shared service when your record is incomplete. `netenv-set` does so only
  with `--allow-shared`, and it always says so on standard error. Check
  `echo "$NETUSER"` to see which account is loaded.
- **A record without `netenable`** exports `NETUSER` and `NETPASS` and
  **unsets** `NETENABLE`, so an enable secret from another record does not
  linger.

## Examples

Check your record without loading anything:

```console
$ netenv-set --check
***AX: service=first.last
***AX: NETUSER=set
***AX: NETPASS=set
***AX: NETENABLE=missing
```

Reload after your password was changed in the store:

```bash
netenv-set
```

Work against a lab account for a while, then return to your own:

```bash
netenv-set --service lab-readonly
# ... lab work ...
netenv-set            # back to $USER's record
```

Load credentials for one command only, in a subshell, without changing your
session:

```bash
( netenv-set --quiet && ansible-playbook site.yml )
```

Clear credentials before handing a terminal to someone or starting a
screen share:

```bash
netenv-clear
```

Use the variables from Python. `ax.getkeys()` gives environment variables
priority over the store, so it returns what `netenv-set` loaded:

```python
import axlib as ax

netuser, netpass, netenable = ax.getkeys()
```

Pass them to a tool that reads its own variable names, for this command
only:

```bash
ANSIBLE_NET_USERNAME="$NETUSER" ANSIBLE_NET_PASSWORD="$NETPASS" ansible-playbook site.yml
```

Run `ssh host command` with credentials. That session is non-interactive, so
nothing is loaded automatically, and the job should stop if the load fails:

```bash
ssh jump01 'export AXLIB_CONFIG_FILE=/etc/axlib/axlib.toml
            code=$(axlib netenv-set --quiet) && eval "$code" && ./nightly-backup.py'
```

## Warnings

**Environment variables are much less protected than the encrypted store.**
In the store, the password is encrypted with AES-256-GCM. Once it's
exported, it is plaintext in memory and goes wherever the environment goes.

- **Every program you start inherits it.** That includes editors, pagers,
  language servers, third-party tools, and plugins. Any of them can read and
  log it. Load credentials only in shells where you run network tooling, or
  set `AXLIB_NETENV_AUTOLOAD=0` and run `netenv-set` when you need it.
- **Other readers on the host.** The environment of each program you start
  can be read by you and by `root` through `/proc/<pid>/environ`
  and `ps eww`. On a shared host, anyone with root or `sudo` can see it.
- **Commands that print the environment** show it: `env`, `printenv`, `set`,
  `export -p`, `declare -p`. Don't run them during a screen share, and never
  paste their output into a ticket, chat, or pull request.
- **Crash dumps and debug logs** can include the environment. So can CI or
  cron logs that print variables, and `set -x` traces. The shell integration
  pauses `set -x` for its own `eval`, but your scripts may still echo
  `$NETPASS` if they trace.
- **Never pipe or redirect `netenv-set` output** anywhere except `eval`, for
  example `| tee`, `> file`, `| logger`, or a pastebin. The refusal to write to
  a terminal cannot protect a pipe or a file.
- **Values stay loaded until you clear them.** A long-running `tmux` or
  `screen` session keeps the password for days, and every new pane or window
  inherits it. After a password change, run `netenv-set` again in every open
  shell. Otherwise tools keep sending the old password, which can **lock the
  account out** after repeated failures.
- **The environment beats the store.** `ax.getkeys()` checks environment
  variables before the store. While `NETPASS` is set, your scripts ignore a
  newer password in the store. Reload or clear after every change.
- **Don't forward the variables to other hosts.** Leave `NET*` out of
  `SendEnv` in `~/.ssh/config` and out of `AcceptEnv` in `sshd_config`.
- **`sudo` usually drops them.** Its default `env_reset` removes `NET*`
  variables, which is the safe default. Don't add `NETPASS` to `env_keep`. If a
  root-run tool needs credentials, give it its own axlib record.
- **`USER` is not a security boundary.** Stores on a shared host are readable
  by the `netops` group (`0660`/`0640`), so every group member can already
  read every record, and anyone can change `USER` in their own shell. Record
  selection by `USER` is a convenience. Access control is the file mode and
  group membership.

## Caveats

- **Supported shells**: bash and zsh through the snippet. The printed
  `export`/`unset` statements also work in ksh and dash if you run `eval`
  yourself. fish and csh/tcsh are not supported.
- **Login time**: a lookup opens each enabled store. Redis has 2-second
  connect and socket timeouts by default, and SQLite and the text store wait up
  to 5 seconds for a lock. An unreachable Redis server therefore delays login
  by a few seconds. It prints a warning and then falls back to the durable
  stores. Lower `connect_timeout` and `socket_timeout` in the `[redis]` table if
  logins feel slow.
- **Warnings print at every login** while a store has a problem, such as Redis
  being down or an unreadable key. That is deliberate. `--quiet` hides only the
  success and "not found" notices.
- **No record, no change.** When your record does not exist yet, the command
  exits 1 and leaves the environment alone. Values you set manually stay in
  place.
- **Redis caching**: values read from SQLite or the text file are cached in
  Redis if it is enabled, exactly as with `ax.getkeys()`. Store updates made
  through the axlib CLIs, the TUI, or `StoreAdmin` clear that cache.
- **Only the network profile** is covered. Infoblox fields (`IBGRID`, `IBUSER`,
  `IBPASS`) are not exported.
- **No searching for a config file**: axlib never searches directories for
  `axlib.toml`. It uses `--config`, else `AXLIB_CONFIG_FILE`, else the file
  that `install.sh` recorded in `/etc/axlib/install.env`. With none of these,
  the command reports "no credential store is enabled" and exits 2.

## Troubleshooting

| Symptom | Cause and fix |
| --- | --- |
| `netenv-set prints shell code for eval and will not write it to a terminal` | You ran `axlib netenv-set` directly. Use `eval "$(axlib netenv-set)"` or the `netenv-set` shell function. |
| `no credential store is enabled` | No configuration file was found, or it doesn't enable a store. On a host without `install.sh`, export `AXLIB_CONFIG_FILE` before sourcing the snippet. |
| `Credential configuration file does not exist` | The path in `AXLIB_CONFIG_FILE` or `--config` is wrong. |
| `a complete network record for 'x' was not found` | No record for that service, or it lacks `netuser`/`netpass`. Check `axlib credential-db list`, or ask an administrator. |
| `... could not be read` (exit 2), preceded by a `lookup failed` line | The store can't be opened. Most often you are not in the store's group (`id` should list `netops`), the key file is wrong, or the file mode prevents reading. The line above says which. |
| `USER='a' differs from login account 'b'` | Something changed `USER`. Unset it or use `--service`. |
| `path could not be normalized` (from uv) | The uv script was run through a symlink. Call it by its real path or set `AXLIB_NETENV_SCRIPTS`. |
| Nothing loads at SSH login | The shell is not interactive, `~/.bash_profile` doesn't source `~/.bashrc`, `AXLIB_NETENV_AUTOLOAD=0`, or `netenv-set --quiet` found no record. Run `netenv-set --check` to see which. |
| Tools still use an old password | Your shell holds the old value. Run `netenv-set` again in every open shell and `tmux` pane. |
| `scp`/`sftp` hang or fail after editing `.bashrc` | Something in `.bashrc` prints output in non-interactive shells. Keep the snippet's interactive guard, and guard your own output the same way. |
