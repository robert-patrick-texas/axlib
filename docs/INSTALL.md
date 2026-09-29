# Installing axlib on a shared Linux host

`install.sh`, included in every release tarball, installs axlib for all
operators on a host with no manual configuration. It sets up a shared Python,
a locked axlib environment, the standard configuration and key locations, and
an encrypted credential store for the `netops` group.

## Quick install

```bash
# 1. uv is needed to install and upgrade (not to run axlib)
curl -LsSf https://astral.sh/uv/install.sh | sudo env UV_INSTALL_DIR=/usr/local/bin sh

# 2. Unpack the release anywhere and run its installer as root
tar xzf axlib-<version>.tar.gz
sudo ./axlib-<version>/install.sh

# 3. Give operators access and a record each
sudo usermod -aG netops alice
export AXLIB_CONFIG_FILE=/etc/axlib/axlib.toml     # new logins get this automatically
sudo -E axlib credential-db add --service alice --set netuser=alice --prompt netpass --prompt netenable
```

When alice next logs in, `axlib credentials network` reports `netuser=set` and
`netpass=set`, and her scripts' calls to `ax.getkeys()` return her login.

## What gets installed

| Path | Contents | Owner and mode |
| --- | --- | --- |
| `/opt/shared/python/` | uv-managed CPython (about 110 MB) | `root`, world-readable |
| `/opt/shared/axlib/` | uv project: `pyproject.toml`, `uv.lock`, `.venv/` | `root`, world-readable |
| `/opt/shared/axlib/releases/` | Each release's wheel plus its unpacked docs, examples, and `install.sh` | `root`, world-readable |
| `/opt/shared/axlib/current` | Symlink to the installed release's files | |
| `/usr/local/bin/axlib` | Symlink to `/opt/shared/axlib/.venv/bin/axlib` | |
| `/etc/axlib/` | Configuration and AES-256 key | `root:netops 2750` |
| `/etc/axlib/axlib.toml` | Configuration (no secrets) | `root:netops 0640` |
| `/etc/axlib/sqlite-aes256.key` | Store encryption key | `root:netops 0640` |
| `/var/lib/axlib/` | Credential database directory | `root:netops 2770` |
| `/var/lib/axlib/credentials.db` | Encrypted SQLite credential store | `root:netops 0660` |
| `/etc/environment` | Adds `AXLIB_CONFIG_FILE=/etc/axlib/axlib.toml` | |
| `/etc/profile.d/axlib-config.sh` | Exports `AXLIB_CONFIG_FILE` if it is still unset | `0644` |
| `/etc/profile.d/axlib-netenv.sh` | Only with `--with-netenv` | `0644` |

axlib never searches for a configuration file, so `AXLIB_CONFIG_FILE` is the
one setting every user needs. The installer sets it in two places.
`/etc/environment` is read through PAM for SSH logins and, on most
distributions, for cron jobs. `/etc/profile.d/axlib-config.sh` covers login
shells on hosts where sshd does not use PAM.

The group permissions are what let operators share the store. The key is
group-readable and the database is group-writable, so every member of
`netops` can read and update records. The directory is group-writable so
SQLite can create its journal file beside the database, and its setgid bit
keeps new files in the `netops` group.

## Requirements

- Linux, and root access (`sudo`) for installing. Operators need no privileges
  beyond membership in `netops`.
- `uv` on root's `PATH`, or passed with `sudo UV=/path/to/uv ./install.sh`.
  Installing it into `/usr/local/bin` (step 1 above) keeps a root-run binary
  out of anyone's home directory.
- Network access during installation. uv downloads the Python build from
  GitHub, and `cryptography`, `redis`, and `setuptools` from the package index.
  See [Offline hosts](#offline-hosts).
- About 150 MB in `/opt/shared`.

## Options

```text
sudo ./install.sh [options]
```

| Option | Default | Purpose |
| --- | --- | --- |
| `--prefix DIR` | `/opt/shared/axlib` | axlib uv project and environment |
| `--python-dir DIR` | `/opt/shared/python` | Shared uv-managed Python |
| `--python VERSION` | `3.14` | Python version (3.11 or newer) |
| `--group NAME` | `netops` | Group allowed to use the store; created if missing |
| `--bin-dir DIR` | `/usr/local/bin` | Where the `axlib` command is linked |
| `--config-dir DIR` | `/etc/axlib` | Configuration and key directory |
| `--data-dir DIR` | `/var/lib/axlib` | Database directory |
| `--with-tui` | off | Also install the full-screen credential manager (`axlib credential-tui`) |
| `--with-netenv` | off | Export `NETUSER`/`NETPASS`/`NETENABLE` in every interactive login. Read `docs/NETENV.md` first. |
| `--no-store` | off | Skip creating the SQLite store and key, for example when using Redis or the text store instead |

`--with-netenv` is off by default because it puts every operator's password
in the environment of every program they start. That is a policy decision
for the host owner, not an installer default.

## What the installer does

1. Checks that it runs as root from an unpacked axlib release, and finds `uv`.
2. Creates the `netops` group if it does not exist.
3. Installs Python into `/opt/shared/python`. It sets
   `UV_PYTHON_INSTALL_DIR` and `UV_MANAGED_PYTHON=1` so uv cannot pick an
   interpreter under someone's home directory, which other users could not
   read.
4. Copies the release to `/opt/shared/axlib/releases/axlib-<version>/`, builds
   its wheel, and adds the wheel to the `/opt/shared/axlib` uv project with
   `uv add`. `uv.lock` records the exact versions installed.
5. Links `/usr/local/bin/axlib`.
6. Creates `/etc/axlib` and `/var/lib/axlib`, and writes `axlib.toml` **only
   if it does not already exist**.
7. Sets `AXLIB_CONFIG_FILE` for all logins.
8. Runs `axlib credential-db init --generate-key` for a new store. For an
   existing store it runs `axlib credential-db init`, which only verifies it.
   **An existing key is never replaced.**
9. Optionally installs the netenv login snippet, then prints a store health
   check.

The generated configuration is minimal. Everything not listed uses axlib's
defaults (`0660`/`0640` modes, 5-second lock timeout, Redis off):

```toml
# [credentials]
# shared_service = "network-shared"   # fallback account for ax.getkeys()

[sqlite]
enabled = true
database = "/var/lib/axlib/credentials.db"
key_file = "/etc/axlib/sqlite-aes256.key"
group = "netops"
```

Edit it to add a shared fallback account, Redis caching, or the encrypted text
store. `docs/CREDENTIALS.md` describes every option.

## For administrators: maintaining the shared environment

This section is for the people with root access who install axlib and keep
the shared environment up to date. Everything under `/opt/shared` is owned by
root and read-only for everyone else. Operators use what you install but
cannot change it. `/opt/shared/axlib/pyproject.toml` and `uv.lock` are the
complete, exact record of what is installed.

### The `axuv` helper

Every uv command in `/opt/shared/axlib` must run as root with the same
settings as `install.sh`. Add this function to root's `~/.bashrc`, or paste it
into a `sudo -i` shell:

```bash
# uv, pointed at the shared axlib environment
axuv() {
    ( umask 022
      export UV_PYTHON_INSTALL_DIR=/opt/shared/python UV_MANAGED_PYTHON=1
      uv --directory /opt/shared/axlib "$@" )
}
```

Each part prevents a specific failure:

- `UV_PYTHON_INSTALL_DIR` and `UV_MANAGED_PYTHON=1` keep uv on the shared
  Python. Without them uv may rebuild `.venv` on an interpreter under root's
  home directory, and every operator's scripts stop working.
- `umask 022` makes new files world-readable even if your own umask is
  stricter (for example `077`). Without it, a newly added library is
  unreadable by everyone except root. That damage persists: uv hardlinks
  installed files from root's uv cache, so the cached copies keep the
  restrictive mode, and simply reinstalling does not fix it (see
  Troubleshooting).
- `--directory` lets you run `axuv` from any directory.

The examples below assume a root shell with `axuv` defined.

### Onboarding operators

```bash
usermod -aG netops alice                  # applies from alice's next login
export AXLIB_CONFIG_FILE=/etc/axlib/axlib.toml
axlib credential-db add --service alice --set netuser=alice --prompt netpass --prompt netenable
/opt/shared/axlib/.venv/bin/python -m axlib.credentials.admin    # store health check
```

Operators can also add and update their own records with
`axlib credential-db add --service "$USER" ...`, or with `axlib credential-tui`
if you installed with `--with-tui`.

### Adding libraries for everyone (netmiko, scrapli, ...)

Libraries added to the shared environment are available to every script that
uses `#!/opt/shared/axlib/.venv/bin/python`, with no setup by the operators:

```bash
axuv add netmiko scrapli
axuv tree --depth 1
```

```text
axlib-site v0.1.0
├── axlib v<version>
├── netmiko v4.8.0
└── scrapli v2026.2.20
```

Check the result as an ordinary user:

```bash
su - alice -c '/opt/shared/axlib/.venv/bin/python -c "import netmiko, scrapli, axlib"'
```

Other libraries commonly added for network automation:

| Need | Command |
| --- | --- |
| TextFSM/NTC templates for parsing `show` output | included with netmiko |
| Jinja2 configuration templates | `axuv add jinja2` |
| NETCONF | `axuv add ncclient` |
| REST APIs (NetBox, controllers) | `axuv add requests` or `axuv add pynetbox` |
| Multi-device task runner | `axuv add nornir nornir-netmiko nornir-scrapli` |
| Spreadsheets of devices | `axuv add openpyxl` |

Adding a library needs internet access, or an internal mirror (see
[Offline hosts](#offline-hosts)).

### Upgrading, pinning, and removing libraries

```bash
axuv add 'netmiko>=4.8,<5'                           # pin a range (the team relies on v4 behavior)
axuv lock --upgrade-package netmiko && axuv sync     # newest netmiko allowed by the pin
axuv lock --upgrade --dry-run                        # preview upgrades for everything
axuv lock --upgrade && axuv sync                     # upgrade everything
axuv remove scrapli                                  # remove a library
```

Every change applies to every operator the next time their script starts. A
library upgrade can break scripts that depended on old behavior, so:

- announce upgrades to the team, and upgrade one library at a time when you can
- pin major versions of the libraries that scripts rely on most
- keep a record so you can undo a change (next section)

Upgrading axlib with `install.sh` does not remove or upgrade these libraries.
The installer only replaces the `axlib` entry, and everything you added stays
as it was.

### Keeping a record of changes

`pyproject.toml` and `uv.lock` are small text files that completely describe
the environment. Tracking them in git gives you history and a one-command
undo:

```bash
cd /opt/shared/axlib
git init -q && printf '.venv/\nreleases/\ncurrent\n' > .gitignore
git add -A && git commit -qm "Baseline"

axuv add nornir && git add -A && git commit -qm "Add nornir for the backup jobs"

# Undo the last change:
git revert --no-edit HEAD && axuv sync
```

`axuv sync` makes `.venv` match `uv.lock` exactly, removing anything that is no
longer listed.

### Upgrading axlib

Unpack the new release and run its installer **with the same options as
before**. For example, leaving out `--with-tui` removes the TUI extra.

```bash
tar xzf axlib-<new-version>.tar.gz
sudo ./axlib-<new-version>/install.sh --with-tui
```

The configuration, key, database, records, and the libraries you added are
untouched. Only axlib itself, the `current` symlink, and (if requested) the
netenv snippet change. Running the same release's installer again is also
safe: it rebuilds and reinstalls that release.

Operators with their own projects keep using the wheel they pinned until they
update it (see [When axlib is upgraded](#when-axlib-is-upgraded)). Tell them
when a new release arrives.

### Rolling back axlib

Older wheels stay in `/opt/shared/axlib/releases/`. To switch back:

```bash
axuv add --upgrade-package axlib --reinstall-package axlib \
    ./releases/axlib-<old-version>-py3-none-any.whl
ln -sfn releases/axlib-<old-version> /opt/shared/axlib/current
axlib version
```

Storage formats are versioned and never migrated. A release that changes a
format says so in `RELEASE_NOTES.md`, and older releases then refuse the newer
store instead of misreading it.

### Backups

Back up **both** `/etc/axlib/sqlite-aes256.key` and
`/var/lib/axlib/credentials.db`, and keep the key backup separate from the
database backup. Without the key, the database cannot be decrypted, and
there is no recovery. Anyone with both files can read every credential.

To replace the key periodically, run
`axlib credential-db rotate-key --generate-key --yes` (with
`AXLIB_CONFIG_FILE` set). It re-encrypts every record and replaces the key
file only after the database is updated.

The environment itself doesn't need a backup: `pyproject.toml`, `uv.lock`,
and the wheels in `releases/` are enough to rebuild it.

### Offline hosts

Installing and adding libraries download packages. On a host without internet
access, point uv at internal mirrors, both for `install.sh` and in `axuv`:

```bash
sudo env UV_DEFAULT_INDEX=https://pypi.example.internal/simple \
         UV_PYTHON_INSTALL_MIRROR=https://mirror.example.internal/python-build-standalone \
         ./axlib-<version>/install.sh

# in axuv, next to the other exports:
export UV_DEFAULT_INDEX=https://pypi.example.internal/simple
```

The installer has no fully offline mode.

Both `/opt/shared/python` and `/opt/shared/axlib` must stay where they were
installed. The environment refers to its Python and its own files by absolute
path, so neither directory can be moved, renamed, or copied to a different
path.

### Uninstalling

```bash
sudo rm -f /usr/local/bin/axlib /etc/profile.d/axlib-config.sh /etc/profile.d/axlib-netenv.sh
sudo sed -i '/^AXLIB_CONFIG_FILE=/d' /etc/environment
sudo rm -rf /opt/shared/axlib
sudo rm -rf /opt/shared/python          # only if nothing else uses it
# Credentials: remove only after you have exported or no longer need them.
# sudo rm -rf /etc/axlib /var/lib/axlib
```

## For network operations staff: using axlib in your scripts

This section is for anyone writing scripts on the server that log in to
network devices. axlib gives every script your credentials from the
encrypted store, so passwords never appear in a script, a config file, or
your shell history.

### Before you start

1. Be a member of `netops`: `id` should list it. Ask an administrator if
   not, then log in again.
2. Have a credential record. Check it (values are never shown):

   ```console
   $ axlib credentials network
   netuser=set
   netpass=set
   netenable=missing
   ```

   If it says `missing` for `netuser` or `netpass`, add or update your record:
   `axlib credential-db add --service "$USER" --set netuser="$USER" --prompt netpass --prompt netenable`,
   or use `axlib credential-tui`.
3. Nothing else. `AXLIB_CONFIG_FILE` is set for you at login.

`ax.getkeys()` looks up the record named by `$USER` and returns
`(netuser, netpass, netenable)`. `netenable` is `None` when you have no enable
secret.

### Option 1: the shared environment (recommended)

Start the script with the shared interpreter. It works from any directory,
needs no setup and no internet access, and includes every library the
administrators have added:

```python
#!/opt/shared/axlib/.venv/bin/python
"""Print "show version" from one Cisco IOS device: ./show_version.py edge-01"""

import sys

import axlib as ax
from netmiko import ConnectHandler

netuser, netpass, netenable = ax.getkeys()
if not (netuser and netpass):
    sys.exit("No network credentials; check with: axlib credentials network")

device = {
    "device_type": "cisco_ios",
    "host": sys.argv[1],
    "username": netuser,
    "password": netpass,
    "secret": netenable or "",
}
with ConnectHandler(**device) as connection:
    if netenable:
        connection.enable()
    print(connection.send_command("show version"))
```

```bash
chmod +x show_version.py
./show_version.py edge-01
```

The same pattern with scrapli:

```python
#!/opt/shared/axlib/.venv/bin/python
import sys

import axlib as ax
from scrapli import Scrapli

netuser, netpass, netenable = ax.getkeys()
device = {
    "host": sys.argv[1],
    "platform": "cisco_iosxe",
    "auth_username": netuser,
    "auth_password": netpass,
    "auth_secondary": netenable or "",
}
with Scrapli(**device) as connection:
    print(connection.send_command("show version").result)
```

scrapli checks SSH host keys by default. Connect once with `ssh` so the device
is in `~/.ssh/known_hosts`, rather than turning the check off.

You can also run a script without the `#!` line:
`/opt/shared/axlib/.venv/bin/python script.py`.

**What's installed**: `cat /opt/shared/axlib/pyproject.toml` lists the
libraries added to the shared environment. If you need one that isn't there,
ask an administrator to add it so everyone benefits. You cannot install into
the shared environment yourself.

### Option 2: your own uv project

Use your own environment when a script needs libraries or versions that
aren't in the shared one. You get axlib from the release wheel on the server,
and the shared Python so you don't download one of your own:

```bash
export UV_PYTHON_INSTALL_DIR=/opt/shared/python UV_MANAGED_PYTHON=1
mkdir -p ~/projects/backups && cd ~/projects/backups
uv init --bare
uv add /opt/shared/axlib/releases/axlib-<version>-py3-none-any.whl
uv add netmiko jinja2
uv run python backup.py
```

Things to know:

- Put the two `export` lines in your `~/.bashrc` so every project uses the
  shared Python.
- The first `uv add` downloads `cryptography`, `redis`, and your libraries
  into your own cache (`~/.cache/uv`), so it needs internet access or the
  team's mirror.
- **Use the wheel from `/opt/shared/axlib/releases/`, not `uv add axlib`.** The
  `axlib` on PyPI is an old release without the credential stores, and it
  cannot read the server's store.
- To run the script from anywhere or from cron, use the project's interpreter
  by full path: `~/projects/backups/.venv/bin/python ~/projects/backups/backup.py`,
  or `uv run --project ~/projects/backups python ~/projects/backups/backup.py`.

### Option 3: a single-file uv script

For a standalone script with its own libraries and no project folder, declare
everything in the file. uv builds and caches the environment on first run:

```python
#!/usr/bin/env -S uv run --quiet --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["axlib", "scrapli"]
#
# [tool.uv.sources]
# axlib = { path = "/opt/shared/axlib/releases/axlib-<version>-py3-none-any.whl" }
# ///
"""Collect interface status from every device named on the command line."""

import axlib as ax
from scrapli import Scrapli

...
```

Run it with `./script.py`. It works from any directory and needs internet
access the first time you run it.

### Option 4: credentials without importing axlib

If your administrator enabled `--with-netenv`, or you load it yourself with
`netenv-set`, your shell already has `NETUSER`, `NETPASS`, and `NETENABLE`
(see `docs/NETENV.md`). Any program can read them, in any language or
environment, without axlib installed:

```python
import os

netuser = os.environ["NETUSER"]
netpass = os.environ["NETPASS"]
netenable = os.environ.get("NETENABLE")
```

`ax.getkeys()` also checks these variables first, so Options 1 to 3 give the
same result in such a shell. Remember that after a password change, the
variables keep the old value until you run `netenv-set` again.

### Scheduled jobs (cron and systemd)

Scheduled jobs don't start a login shell, so set what they need explicitly:

```cron
AXLIB_CONFIG_FILE=/etc/axlib/axlib.toml
# m  h  dom mon dow  command
15   2  *   *   *    /home/alice/jobs/backup_configs.py >> /home/alice/jobs/backup.log 2>&1
```

- cron may not set `USER`, and without it `ax.getkeys()` cannot tell whose
  record to load. It then falls back to the shared account, if one is
  configured. In scripts meant for cron, name the account explicitly:

  ```python
  import getpass

  netuser, netpass, netenable = ax.getkeys(getpass.getuser())
  ```

  `getpass.getuser()` also checks `LOGNAME`, which cron always sets, and
  finally the account database.
- Use full paths for the interpreter and every file, because cron's working
  directory and `PATH` are minimal.
- For a systemd service, set `User=` and `Environment=AXLIB_CONFIG_FILE=/etc/axlib/axlib.toml`
  in the unit.

### Rules for credentials in scripts

- Get credentials from `ax.getkeys()` (or `NET*` variables) every time. Never
  paste a password into a script, a config file, an inventory, or a command
  line. Command lines are visible to every user in `ps`.
- Never print, log, or write credentials to a file, including in debug output
  and exception messages. Log `netuser` if you must, never `netpass` or
  `netenable`.
- Don't copy the key file or the database. Access comes from the `netops`
  group, and a copy is an unmanaged backup of everyone's passwords.
- Check for missing credentials before connecting, as in the examples above.
  Otherwise a device login attempt with an empty password counts against your
  lockout limit.

### When axlib is upgraded

- **Option 1** scripts get the new release immediately. Nothing to do.
- **Option 2 and 3** keep the axlib wheel they point to, so the server upgrade
  cannot break them. To move to the new release, `uv add` the new wheel
  (Option 2) or edit the path in the script header (Option 3):

  ```bash
  ls /opt/shared/axlib/releases/*.whl      # available releases
  uv add /opt/shared/axlib/releases/axlib-<new-version>-py3-none-any.whl
  ```

`axlib version` shows the release installed in the shared environment, and
`uv run python -c "import axlib; print(axlib.__version__)"` shows the one in
your project.

## Troubleshooting

| Symptom | Cause and fix |
| --- | --- |
| `install.sh: error: run as root` | Run with `sudo`. |
| `install.sh: error: uv was not found` | `sudo` resets `PATH`. Install uv into `/usr/local/bin` (step 1), or run `sudo UV=$(command -v uv) ./install.sh`. |
| `No SQLite encryption key is configured` when adding a record | `AXLIB_CONFIG_FILE` is not set in the current shell. Run `export AXLIB_CONFIG_FILE=/etc/axlib/axlib.toml`, and use `sudo -E` to pass it through sudo, or log in again. |
| `Permission denied` on the key or database for an operator | The user is not in `netops` yet, or has not logged in again since being added. Check with `id`. |
| `axlib: command not found` | `/usr/local/bin` is not on `PATH`, or the symlink was removed. Run `/opt/shared/axlib/.venv/bin/axlib` directly. |
| `bad interpreter` / `No such file or directory` running `axlib` | `/opt/shared/python` or `/opt/shared/axlib` was moved, renamed, or deleted. Reinstall to the original paths. |
| Cron jobs cannot find credentials | This distribution's cron does not read `/etc/environment`. Add `AXLIB_CONFIG_FILE=/etc/axlib/axlib.toml` at the top of the crontab. |
| Health check shows `file ... not-configured` | Expected. Only the SQLite store is enabled by default. |
| `ModuleNotFoundError: No module named 'netmiko'` (or another library) | The library is not in the environment the script runs in. For the shared environment, ask an administrator to `axuv add` it. For your own project, run `uv add` in the project. |
| A library works for root, but other users get `cannot import name ... (unknown location)`, `PermissionError`, or `ModuleNotFoundError` | It was installed by plain `uv` under a restrictive umask. Python treats the unreadable directory as an empty namespace package, which produces these errors. The same restrictive modes are also in root's uv cache, so clear the cache before reinstalling: `axuv cache clean <name> <its-dependencies>` then `axuv sync --reinstall-package <name>`. To reset everything, run `axuv cache clean && axuv sync --reinstall` (this downloads all packages again). |
| A cron job gets the shared account or no credentials | cron did not set `USER`. Use `ax.getkeys(getpass.getuser())` (see [Scheduled jobs](#scheduled-jobs-cron-and-systemd)). |
| `uv add axlib` installs an old release without credential stores | That is the old PyPI package. Add the wheel from `/opt/shared/axlib/releases/` instead. |
