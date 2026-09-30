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
#   /usr/local/sbin/axuv         uv, pointed at the shared environment (root only)
#   /etc/axlib/axlib.toml        configuration (never overwritten)
#   /etc/axlib/install.env       the options of the last run (see below)
#   /etc/axlib/sqlite-aes256.key AES-256 key, root:netops 0640
#   /var/lib/axlib/credentials.db encrypted SQLite store, root:netops 0660
#   /etc/environment, /etc/profile.d/axlib-config.sh   AXLIB_CONFIG_FILE
#
# Every run records its options in /etc/axlib/install.env, and the next run
# starts from them, so an upgrade needs no options at all:
#   sudo ./axlib-<new-version>/install.sh
# The same record tells axuv where the environment is, and tells the axlib
# command which axlib.toml to use when neither --config nor AXLIB_CONFIG_FILE
# names one.  The configuration, key, and credential store are always kept.
# See docs/INSTALL.md.

set -euo pipefail
umask 022

# Fixed, not an option: it is how later runs find the options of this one.
INSTALL_RECORD=/etc/axlib/install.env

# Defaults for the first install.
PREFIX=/opt/shared/axlib
PYTHON_DIR=/opt/shared/python
PYTHON_VERSION=3.14
GROUP=netops
BIN_DIR=/usr/local/bin
SBIN_DIR=/usr/local/sbin
CONFIG_DIR=/etc/axlib
DATA_DIR=/var/lib/axlib
# Empty means "keep what this host already has"; resolved after the options.
WITH_TUI=
WITH_NETENV=
CREATE_STORE=1
UV_BIN=

# Later runs start from the options recorded by the previous one.  The record
# is root-owned in a directory only root can write, so sourcing it is safe.
RECORDED=0
if [ -f "$INSTALL_RECORD" ]; then
    # shellcheck source=/dev/null
    . "$INSTALL_RECORD"
    RECORDED=1
fi

usage() {
    cat <<EOF
Usage: sudo $0 [options]

Install axlib from this unpacked release into a shared uv environment.
Defaults below are the options of the previous run when $INSTALL_RECORD
exists, so an upgrade needs no options.

Options:
  --prefix DIR        axlib uv project and environment  (default: $PREFIX)
  --python-dir DIR    shared uv-managed Python          (default: $PYTHON_DIR)
  --python VERSION    Python version to install          (default: $PYTHON_VERSION)
  --group NAME        group that may use the store       (default: $GROUP)
  --bin-dir DIR       where the axlib command is linked  (default: $BIN_DIR)
  --sbin-dir DIR      where the axuv command is copied   (default: $SBIN_DIR)
  --config-dir DIR    configuration and key directory    (default: $CONFIG_DIR)
  --data-dir DIR      credential database directory      (default: $DATA_DIR)
  --with-tui          also install the credential manager (axlib[tui])
  --no-tui            remove the credential manager
  --with-netenv       load NETUSER/NETPASS/NETENABLE at every interactive login
                      (/etc/profile.d/axlib-netenv.sh; read docs/NETENV.md first)
  --no-netenv         remove that login snippet
  --no-store          do not create the SQLite store or its key
  --store             create or verify the SQLite store (the default)
  -h, --help          show this help

Without --with-tui/--no-tui or --with-netenv/--no-netenv, the host keeps its
current choice.

Environment:
  UV                  path to the uv binary (default: the uv of the previous
                      run, else uv on PATH)
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
    --sbin-dir) SBIN_DIR=${2:?--sbin-dir needs a directory}; shift ;;
    --config-dir) CONFIG_DIR=${2:?--config-dir needs a directory}; shift ;;
    --data-dir) DATA_DIR=${2:?--data-dir needs a directory}; shift ;;
    --with-tui) WITH_TUI=1 ;;
    --no-tui) WITH_TUI=0 ;;
    --with-netenv) WITH_NETENV=1 ;;
    --no-netenv) WITH_NETENV=0 ;;
    --no-store) CREATE_STORE=0 ;;
    --store) CREATE_STORE=1 ;;
    -h | --help) usage; exit 0 ;;
    *) usage >&2; die "unknown option: $1" ;;
    esac
    shift
done

# ---------------------------------------------------------------- preflight
[ "$(id -u)" -eq 0 ] || die "run as root (sudo $0); it writes to $PREFIX, /etc, and /var/lib."

# The record stores values in single quotes, and axuv and the axlib command
# read the paths from any directory, so paths must be absolute and unquoted.
for path in "$PREFIX" "$PYTHON_DIR" "$BIN_DIR" "$SBIN_DIR" "$CONFIG_DIR" "$DATA_DIR"; do
    case $path in
    *"'"*) die "paths cannot contain a single quote: $path" ;;
    /*) ;;
    *) die "use an absolute path: $path" ;;
    esac
done
case $PYTHON_VERSION$GROUP in *"'"*) die "--python and --group cannot contain a single quote." ;; esac

SOURCE_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
if ! grep -qs '^name = "axlib"$' "$SOURCE_DIR/pyproject.toml"; then
    die "run install.sh from inside the unpacked axlib release (no axlib pyproject.toml in $SOURCE_DIR)."
fi
VERSION=$(sed -n 's/^version = "\(.*\)"$/\1/p' "$SOURCE_DIR/pyproject.toml" | head -n 1)
[ -n "$VERSION" ] || die "cannot read the version from $SOURCE_DIR/pyproject.toml."

# UV in the environment wins, then the uv of the previous run, then PATH.
if [ -z "${UV:-}" ]; then
    if [ -x "$UV_BIN" ]; then
        UV=$UV_BIN
    else
        UV=$(command -v uv || true)
    fi
fi
if [ -z "$UV" ] || [ ! -x "$UV" ]; then
    die "uv was not found. Install it system-wide first:
    curl -LsSf https://astral.sh/uv/install.sh | sudo env UV_INSTALL_DIR=/usr/local/bin sh
or pass its location: sudo UV=/path/to/uv $0"
fi
# Recorded for axuv, which runs from any directory, so it must be absolute.
UV_BIN=$(cd "$(dirname "$UV")" && pwd)/$(basename "$UV")
case $UV_BIN in *"'"*) die "the uv path cannot contain a single quote: $UV_BIN" ;; esac

# Options not given and not recorded keep what the host has, so the first run
# of this installer on a host set up by an older one changes nothing unasked.
if [ -z "$WITH_TUI" ]; then
    if grep -qs '"axlib\[tui\]' "$PREFIX/pyproject.toml"; then WITH_TUI=1; else WITH_TUI=0; fi
fi
if [ -z "$WITH_NETENV" ]; then
    if [ -e /etc/profile.d/axlib-netenv.sh ]; then WITH_NETENV=1; else WITH_NETENV=0; fi
fi

# uv must use only its own Python from the shared directory.  Without these,
# uv may pick an interpreter under someone's home directory, which other users
# cannot read, and every axlib command would fail for them.
export UV_PYTHON_INSTALL_DIR=$PYTHON_DIR UV_MANAGED_PYTHON=1
# A key or config in root's own environment would redirect the store setup.
unset AXLIB_SQLITE_KEY AXLIB_SQLITE_KEY_FILE AXLIB_SQLITE_DATABASE AXLIB_SQLITE_ENABLE
export AXLIB_CONFIG_FILE=$CONFIG_DIR/axlib.toml

say "Installing axlib $VERSION from $SOURCE_DIR"
[ "$RECORDED" -eq 1 ] && say "Starting from the options recorded in $INSTALL_RECORD"

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
REMOVE_TUI=0
if [ "$WITH_TUI" -eq 0 ] && grep -qs '"axlib\[tui\]' "$PREFIX/pyproject.toml"; then
    REMOVE_TUI=1
    # uv add keeps the extras of an existing requirement, so drop it first.
    # --no-sync leaves .venv alone until the commands below update it.
    say "Removing the credential manager (axlib[tui])"
    (cd "$PREFIX" && "$UV" remove --quiet --no-sync axlib)
fi
say "Installing $REQUIREMENT ${EXTRAS[*]-} into $PREFIX/.venv"
# --upgrade/--reinstall-package make uv re-read the wheel: a rerun rebuilds
# it with a new hash, and an upgrade replaces the previous release.
(cd "$PREFIX" && "$UV" add --quiet --upgrade-package axlib --reinstall-package axlib \
    "$REQUIREMENT" ${EXTRAS[@]+"${EXTRAS[@]}"})
# uv add only installs; uv sync also uninstalls what uv.lock no longer lists
# (textual, rich, and their dependencies).
if [ "$REMOVE_TUI" -eq 1 ]; then
    (cd "$PREFIX" && "$UV" sync --quiet)
fi

install -d -m 0755 "$BIN_DIR"
ln -sfn "$PREFIX/.venv/bin/axlib" "$BIN_DIR/axlib"
"$BIN_DIR/axlib" version >/dev/null || die "$BIN_DIR/axlib does not run."

# A copy, not a symlink into releases/: axuv must keep working while an
# administrator rolls axlib back or removes old releases.
say "Installing $SBIN_DIR/axuv"
install -d -m 0755 "$SBIN_DIR"
install -m 0755 "$RELEASE_DIR/scripts/axuv" "$SBIN_DIR/axuv"

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

# The record is written with mktemp + mv so a reader never sees half a file.
# CONFIG_FILE is what the axlib command falls back to; the other values are
# the defaults of the next run of this installer and the paths axuv uses.
say "Recording these options in $INSTALL_RECORD"
[ -d "${INSTALL_RECORD%/*}" ] || install -d -m 0755 "${INSTALL_RECORD%/*}"
RECORD_TMP=$(mktemp "$INSTALL_RECORD.XXXXXX")
cat >"$RECORD_TMP" <<EOF
# Written by axlib $VERSION install.sh.  Read by the next install.sh run (as
# its defaults), by axuv, and by the axlib command (default configuration).
# Change an option by running install.sh again with it, not by editing here.
PREFIX='$PREFIX'
PYTHON_DIR='$PYTHON_DIR'
PYTHON_VERSION='$PYTHON_VERSION'
GROUP='$GROUP'
BIN_DIR='$BIN_DIR'
SBIN_DIR='$SBIN_DIR'
CONFIG_DIR='$CONFIG_DIR'
DATA_DIR='$DATA_DIR'
CONFIG_FILE='$AXLIB_CONFIG_FILE'
WITH_TUI='$WITH_TUI'
WITH_NETENV='$WITH_NETENV'
CREATE_STORE='$CREATE_STORE'
UV_BIN='$UV_BIN'
EOF
chown root:root "$RECORD_TMP"
chmod 0644 "$RECORD_TMP"
mv -f "$RECORD_TMP" "$INSTALL_RECORD"

# /etc/environment is read by PAM for SSH logins and (on most distributions)
# cron; the profile.d file covers hosts where sshd does not use PAM.  The
# value is replaced if an earlier run used a different --config-dir.
if ! grep -qxF "AXLIB_CONFIG_FILE=$AXLIB_CONFIG_FILE" /etc/environment 2>/dev/null; then
    say "Setting AXLIB_CONFIG_FILE in /etc/environment"
    ENVIRONMENT=$(grep -v '^AXLIB_CONFIG_FILE=' /etc/environment 2>/dev/null || true)
    # Rewriting in place (rather than mv) keeps the file's owner, mode, and
    # SELinux label.
    {
        [ -z "$ENVIRONMENT" ] || printf '%s\n' "$ENVIRONMENT"
        printf 'AXLIB_CONFIG_FILE=%s\n' "$AXLIB_CONFIG_FILE"
    } >/etc/environment
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
elif [ -e /etc/profile.d/axlib-netenv.sh ]; then
    say "Removing /etc/profile.d/axlib-netenv.sh"
    rm -f /etc/profile.d/axlib-netenv.sh
fi

# ------------------------------------------------------------------- report
if [ "$CREATE_STORE" -eq 1 ]; then
    say "Store health check"
    "$PREFIX/.venv/bin/python" -m axlib.credentials.admin
fi

cat <<EOF

axlib $VERSION is installed.

  Command:       $BIN_DIR/axlib   (uses $AXLIB_CONFIG_FILE unless given --config)
  Python:        $PREFIX/.venv/bin/python   (use as the #! line of ax.getkeys() scripts)
  Libraries:     $SBIN_DIR/axuv add netmiko scrapli   (root; uv on the shared environment)
  Options:       $INSTALL_RECORD   (the next install.sh run starts from these)
  Docs:          $PREFIX/current/docs/

Next steps:
  1. Add operators to the group:   usermod -aG $GROUP <user>   (effective at next login)
  2. Add a record per operator:    axlib credential-db add --service <user> \\
                                       --set netuser=<user> --prompt netpass --prompt netenable
  3. As the operator, check:       axlib credentials network
EOF
