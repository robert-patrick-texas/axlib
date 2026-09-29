#!/usr/bin/env bash
# Copyright 2026 Robert Patrick
# SPDX-License-Identifier: Apache-2.0
#
# Install axlib from an unpacked release tarball on a shared Linux host.
#
#   tar xzf axlib-<version>.tar.gz
#   sudo ./axlib-<version>/install.sh [--with-netenv] [--with-tui]
#
# Result (defaults):
#   /opt/shared/python           uv-managed CPython, readable by every user
#   /opt/shared/axlib            uv project with the locked axlib environment
#     .venv/                     the environment; .venv/bin/python runs scripts
#     releases/                  built wheels and each release's docs/examples
#     current -> releases/axlib-<version>
#   /usr/local/bin/axlib         symlink to the axlib command
#   /etc/axlib/axlib.toml        configuration (never overwritten)
#   /etc/axlib/sqlite-aes256.key AES-256 key, root:netops 0640
#   /var/lib/axlib/credentials.db encrypted SQLite store, root:netops 0660
#   /etc/environment, /etc/profile.d/axlib-config.sh   AXLIB_CONFIG_FILE
#
# Running it again from a newer release upgrades axlib in place; the
# configuration, key, and credential store are kept.  See docs/INSTALL.md.

set -euo pipefail
umask 022

PREFIX=/opt/shared/axlib
PYTHON_DIR=/opt/shared/python
PYTHON_VERSION=3.14
GROUP=netops
BIN_DIR=/usr/local/bin
CONFIG_DIR=/etc/axlib
DATA_DIR=/var/lib/axlib
WITH_TUI=0
WITH_NETENV=0
CREATE_STORE=1

usage() {
    cat <<EOF
Usage: sudo $0 [options]

Install axlib from this unpacked release into a shared uv environment.

Options:
  --prefix DIR        axlib uv project and environment  (default: $PREFIX)
  --python-dir DIR    shared uv-managed Python          (default: $PYTHON_DIR)
  --python VERSION    Python version to install          (default: $PYTHON_VERSION)
  --group NAME        group that may use the store       (default: $GROUP)
  --bin-dir DIR       where the axlib command is linked  (default: $BIN_DIR)
  --config-dir DIR    configuration and key directory    (default: $CONFIG_DIR)
  --data-dir DIR      credential database directory      (default: $DATA_DIR)
  --with-tui          also install the credential manager (axlib[tui])
  --with-netenv       load NETUSER/NETPASS/NETENABLE at every interactive login
                      (/etc/profile.d/axlib-netenv.sh; read docs/NETENV.md first)
  --no-store          do not create the SQLite store or its key
  -h, --help          show this help

Environment:
  UV                  path to the uv binary (default: uv on PATH)
EOF
}

say() { printf '==> %s\n' "$*"; }
die() { printf 'install.sh: error: %s\n' "$*" >&2; exit 1; }

while [ $# -gt 0 ]; do
    case $1 in
    --prefix) PREFIX=${2:?--prefix needs a directory}; shift ;;
    --python-dir) PYTHON_DIR=${2:?--python-dir needs a directory}; shift ;;
    --python) PYTHON_VERSION=${2:?--python needs a version}; shift ;;
    --group) GROUP=${2:?--group needs a name}; shift ;;
    --bin-dir) BIN_DIR=${2:?--bin-dir needs a directory}; shift ;;
    --config-dir) CONFIG_DIR=${2:?--config-dir needs a directory}; shift ;;
    --data-dir) DATA_DIR=${2:?--data-dir needs a directory}; shift ;;
    --with-tui) WITH_TUI=1 ;;
    --with-netenv) WITH_NETENV=1 ;;
    --no-store) CREATE_STORE=0 ;;
    -h | --help) usage; exit 0 ;;
    *) usage >&2; die "unknown option: $1" ;;
    esac
    shift
done

# ---------------------------------------------------------------- preflight
[ "$(id -u)" -eq 0 ] || die "run as root (sudo $0); it writes to $PREFIX, /etc, and /var/lib."

SOURCE_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
if ! grep -qs '^name = "axlib"$' "$SOURCE_DIR/pyproject.toml"; then
    die "run install.sh from inside the unpacked axlib release (no axlib pyproject.toml in $SOURCE_DIR)."
fi
VERSION=$(sed -n 's/^version = "\(.*\)"$/\1/p' "$SOURCE_DIR/pyproject.toml" | head -n 1)
[ -n "$VERSION" ] || die "cannot read the version from $SOURCE_DIR/pyproject.toml."

UV=${UV:-$(command -v uv || true)}
if [ -z "$UV" ] || [ ! -x "$UV" ]; then
    die "uv was not found. Install it system-wide first:
    curl -LsSf https://astral.sh/uv/install.sh | sudo env UV_INSTALL_DIR=/usr/local/bin sh
or pass its location: sudo UV=/path/to/uv $0"
fi

# uv must use only its own Python from the shared directory.  Without these,
# uv may pick an interpreter under someone's home directory, which other users
# cannot read, and every axlib command would fail for them.
export UV_PYTHON_INSTALL_DIR=$PYTHON_DIR UV_MANAGED_PYTHON=1
# A key or config in root's own environment would redirect the store setup.
unset AXLIB_SQLITE_KEY AXLIB_SQLITE_KEY_FILE AXLIB_SQLITE_DATABASE AXLIB_SQLITE_ENABLE
export AXLIB_CONFIG_FILE=$CONFIG_DIR/axlib.toml

say "Installing axlib $VERSION from $SOURCE_DIR"

# ------------------------------------------------------------ shared Python
if ! getent group "$GROUP" >/dev/null; then
    say "Creating group $GROUP"
    groupadd "$GROUP"
fi

say "Installing Python $PYTHON_VERSION into $PYTHON_DIR"
install -d -m 0755 "$PYTHON_DIR"
# --no-bin keeps uv from adding python3.x links to root's ~/.local/bin.
"$UV" python install --quiet --no-bin --install-dir "$PYTHON_DIR" "$PYTHON_VERSION"

# ------------------------------------------------------------ axlib project
RELEASES=$PREFIX/releases
RELEASE_DIR=$RELEASES/axlib-$VERSION
install -d -m 0755 "$PREFIX" "$RELEASES"

say "Copying release files to $RELEASE_DIR"
rm -rf "$RELEASE_DIR"
cp -R "$SOURCE_DIR" "$RELEASE_DIR"
rm -rf "$RELEASE_DIR/.venv"
ln -sfn "releases/axlib-$VERSION" "$PREFIX/current"

say "Building the axlib wheel"
rm -f "$RELEASES"/axlib-"$VERSION"-*.whl
"$UV" build --quiet --wheel --python "$PYTHON_VERSION" --out-dir "$RELEASES" "$SOURCE_DIR"
# uv build drops a "*" .gitignore into its output directory; not wanted here.
rm -f "$RELEASES/.gitignore"
WHEEL=$(find "$RELEASES" -maxdepth 1 -name "axlib-$VERSION-*.whl" -print -quit)
[ -n "$WHEEL" ] || die "the wheel build produced no axlib-$VERSION wheel in $RELEASES."

if [ ! -f "$PREFIX/pyproject.toml" ]; then
    say "Creating the uv project in $PREFIX"
    "$UV" init --quiet --bare --no-workspace --name axlib-site \
        --python "$PYTHON_VERSION" "$PREFIX"
fi

# A wheel (not the source directory) is added: a directory would become an
# editable workspace member tied to wherever the tarball was unpacked.
REQUIREMENT="./releases/$(basename "$WHEEL")"
EXTRAS=()
[ "$WITH_TUI" -eq 1 ] && EXTRAS=(--extra tui)
say "Installing $REQUIREMENT ${EXTRAS[*]-} into $PREFIX/.venv"
# --upgrade/--reinstall-package make uv re-read the wheel: a rerun rebuilds
# it with a new hash, and an upgrade replaces the previous release.
(cd "$PREFIX" && "$UV" add --quiet --upgrade-package axlib --reinstall-package axlib \
    "$REQUIREMENT" ${EXTRAS[@]+"${EXTRAS[@]}"})

install -d -m 0755 "$BIN_DIR"
ln -sfn "$PREFIX/.venv/bin/axlib" "$BIN_DIR/axlib"
"$BIN_DIR/axlib" version >/dev/null || die "$BIN_DIR/axlib does not run."

# ------------------------------------------------------------ configuration
install -d -o root -g "$GROUP" -m 2750 "$CONFIG_DIR"
# setgid (2xxx) makes new files inherit the group; 2770 lets group members
# write, which SQLite needs to create its journal beside the database.
install -d -o root -g "$GROUP" -m 2770 "$DATA_DIR"

if [ -e "$AXLIB_CONFIG_FILE" ]; then
    say "Keeping existing $AXLIB_CONFIG_FILE"
else
    say "Writing $AXLIB_CONFIG_FILE"
    cat >"$AXLIB_CONFIG_FILE" <<EOF
# Written by axlib install.sh.  Every option is described in docs/CREDENTIALS.md
# ($PREFIX/current/docs/CREDENTIALS.md).  This file holds no secrets.

# [credentials]
# shared_service = "network-shared"   # fallback account for ax.getkeys()

[sqlite]
enabled = true
database = "$DATA_DIR/credentials.db"
key_file = "$CONFIG_DIR/sqlite-aes256.key"
group = "$GROUP"
EOF
    chgrp "$GROUP" "$AXLIB_CONFIG_FILE"
    chmod 0640 "$AXLIB_CONFIG_FILE"
fi

# /etc/environment is read by PAM for SSH logins and (on most distributions)
# cron; the profile.d file covers hosts where sshd does not use PAM.
if ! grep -q '^AXLIB_CONFIG_FILE=' /etc/environment 2>/dev/null; then
    say "Adding AXLIB_CONFIG_FILE to /etc/environment"
    printf 'AXLIB_CONFIG_FILE=%s\n' "$AXLIB_CONFIG_FILE" >>/etc/environment
fi
cat >/etc/profile.d/axlib-config.sh <<EOF
# Written by axlib install.sh: where axlib finds its configuration.
export AXLIB_CONFIG_FILE="\${AXLIB_CONFIG_FILE:-$AXLIB_CONFIG_FILE}"
EOF
chmod 0644 /etc/profile.d/axlib-config.sh

# ---------------------------------------------------------- credential store
if [ "$CREATE_STORE" -eq 1 ]; then
    if [ -e "$CONFIG_DIR/sqlite-aes256.key" ]; then
        say "Verifying the existing credential store"
        "$BIN_DIR/axlib" credential-db init
    else
        say "Creating the credential store and its AES-256 key"
        "$BIN_DIR/axlib" credential-db init --generate-key
    fi
fi

# ------------------------------------------------------- login integration
# The profile.d files are sourced in name order: axlib-config.sh runs before
# axlib-netenv.sh, so AXLIB_CONFIG_FILE is set when the snippet needs it.
if [ "$WITH_NETENV" -eq 1 ]; then
    say "Installing /etc/profile.d/axlib-netenv.sh"
    install -m 0644 "$RELEASE_DIR/examples/netenv/netenv.sh" /etc/profile.d/axlib-netenv.sh
fi

# ------------------------------------------------------------------- report
if [ "$CREATE_STORE" -eq 1 ]; then
    say "Store health check"
    "$PREFIX/.venv/bin/python" -m axlib.credentials.admin
fi

cat <<EOF

axlib $VERSION is installed.

  Command:       $BIN_DIR/axlib
  Python:        $PREFIX/.venv/bin/python   (use as the #! line of ax.getkeys() scripts)
  Config:        $AXLIB_CONFIG_FILE
  Docs:          $PREFIX/current/docs/

Next steps:
  1. In this shell (new logins get it automatically):
                                   export AXLIB_CONFIG_FILE=$AXLIB_CONFIG_FILE
  2. Add operators to the group:   usermod -aG $GROUP <user>   (effective at next login)
  3. Add a record per operator:    axlib credential-db add --service <user> \\
                                       --set netuser=<user> --prompt netpass --prompt netenable
  4. As the operator, check:       axlib credentials network
EOF
