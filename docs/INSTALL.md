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

## After installation

**Add operators**: `sudo usermod -aG netops <user>`. Group membership applies
from the user's next login.

**Add credentials**: an administrator runs `axlib credential-db add`, or each
operator adds their own record. Operators who don't write Python can use
`axlib credential-tui` if it was installed with `--with-tui`:

```bash
axlib credential-db add --service "$USER" --set netuser="$USER" --prompt netpass --prompt netenable
```

**Run Python scripts** with the shared environment's interpreter:

```python
#!/opt/shared/axlib/.venv/bin/python
import axlib as ax

netuser, netpass, netenable = ax.getkeys()
```

Or run `/opt/shared/axlib/.venv/bin/python script.py`. A project with its own
uv environment can use the same release with
`uv add /opt/shared/axlib/releases/axlib-<version>-py3-none-any.whl`.

**Check health** at any time:

```bash
/opt/shared/axlib/.venv/bin/python -m axlib.credentials.admin
```

## Upgrading

Unpack the new release and run its installer **with the same options as
before**. For example, leaving out `--with-tui` removes the TUI extra.

```bash
tar xzf axlib-<new-version>.tar.gz
sudo ./axlib-<new-version>/install.sh --with-tui
```

The configuration, key, database, and records are untouched. Only the
environment, the `current` symlink, and (if requested) the netenv snippet
change. Running the same release's installer again is also safe: it rebuilds
and reinstalls that release.

## Rolling back

Older wheels stay in `/opt/shared/axlib/releases/`. To switch back:

```bash
cd /opt/shared/axlib
sudo env UV_PYTHON_INSTALL_DIR=/opt/shared/python UV_MANAGED_PYTHON=1 \
    uv add --upgrade-package axlib --reinstall-package axlib \
    ./releases/axlib-<old-version>-py3-none-any.whl
sudo ln -sfn releases/axlib-<old-version> current
axlib version
```

Always set the two `UV_*` variables when running uv in `/opt/shared/axlib`.
Without them, uv might rebuild the environment on a Python that other users
cannot read.

Storage formats are versioned and never migrated. A release that changes a
format says so in `RELEASE_NOTES.md`, and older releases then refuse the newer
store instead of misreading it.

## Backups

Back up **both** `/etc/axlib/sqlite-aes256.key` and
`/var/lib/axlib/credentials.db`, and keep the key backup separate from the
database backup. Without the key, the database cannot be decrypted, and
there is no recovery. Anyone with both files can read every credential.

To replace the key periodically, run
`sudo -E axlib credential-db rotate-key --generate-key --yes`. It re-encrypts
every record and replaces the key file only after the database is updated.

## Uninstalling

```bash
sudo rm -f /usr/local/bin/axlib /etc/profile.d/axlib-config.sh /etc/profile.d/axlib-netenv.sh
sudo sed -i '/^AXLIB_CONFIG_FILE=/d' /etc/environment
sudo rm -rf /opt/shared/axlib
sudo rm -rf /opt/shared/python          # only if nothing else uses it
# Credentials: remove only after you have exported or no longer need them.
# sudo rm -rf /etc/axlib /var/lib/axlib
```

## Offline hosts

The installer downloads the Python build and the `cryptography`, `redis`, and
`setuptools` packages. On a host without internet access, point uv at internal
mirrors:

```bash
sudo env UV_DEFAULT_INDEX=https://pypi.example.internal/simple \
         UV_PYTHON_INSTALL_MIRROR=https://mirror.example.internal/python-build-standalone \
         ./axlib-<version>/install.sh
```

The installer has no fully offline mode.

Both `/opt/shared/python` and `/opt/shared/axlib` must stay where they were
installed. The environment refers to its Python and its own files by absolute
path, so neither directory can be moved, renamed, or copied to a different
path.

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
