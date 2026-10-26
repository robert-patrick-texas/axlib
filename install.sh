#!/usr/bin/env bash
# Copyright 2026 Robert Patrick
# SPDX-License-Identifier: Apache-2.0
#
# Install axlib from an unpacked release tarball on a shared Linux host.
#
#   tar xzf axlib-<version>.tar.gz
#   sudo ./axlib-<version>/install.sh [--with-netenv] [--with-tui] [--with-textfsm]
#                                     [--with-markdown] [--with-all]
#
# Once installed, the same script installs the latest GitHub release:
#   sudo /opt/shared/axlib/current/install.sh update [--dry-run] [--proxy URL] [-k]
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
#   /etc/axlib/textfsm/          shared TextFSM templates (--with-textfsm)
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
WITH_TEXTFSM=
WITH_MARKDOWN=
WITH_NETENV=
# 1 turns every optional part on, now and in later runs (see below).
WITH_ALL=0
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
       sudo $0 update [--dry-run] [--proxy URL] [-k] [-- CURL-OPTION...]

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
  --with-textfsm      also install show-command parsing, axlib parse
                      (axlib[textfsm]: TextFSM and ntc-templates), and create
                      CONFIG_DIR/textfsm for the team's own templates
  --no-textfsm        remove show-command parsing (the templates are kept)
  --with-markdown     also install Markdown-to-HTML conversion, axlib md2html
                      (axlib[markdown]: markdown-it-py)
  --no-markdown       remove Markdown-to-HTML conversion
  --with-all          install every optional part above, now and in later runs,
                      including parts a newer release adds; a later --no-
                      option for one part (such as --no-tui) leaves that part
                      out and ends --with-all.  It does not include --with-netenv.
  --with-netenv       load NETUSER/NETPASS/NETENABLE at every interactive login
                      (/etc/profile.d/axlib-netenv.sh; read docs/NETENV.md first)
  --no-netenv         remove that login snippet
  --no-store          do not create the SQLite store or its key
  --store             create or verify the SQLite store (the default)
  -h, --help          show this help

Without --with-all, --with-tui/--no-tui, --with-textfsm/--no-textfsm,
--with-markdown/--no-markdown, or --with-netenv/--no-netenv, the host keeps
its current choice.

Update (on a host this installer set up; it takes only these options):
  update              download the latest axlib release from GitHub, check it
                      against the SHA-256 digest GitHub publishes, and run its
                      install.sh, which starts from the recorded options and
                      keeps the configuration, key, and credential store
  update --dry-run    only report whether a newer release exists and what
                      update would do; exits 100 when one does, 0 when axlib
                      is up to date.  Members of the group may run it too.
  --proxy URL         download through this HTTP or HTTPS proxy, such as
                      http://192.0.2.10:2055/; the new release's install.sh
                      gets it too, as HTTPS_PROXY and HTTP_PROXY, for uv
  -k, --insecure      accept any TLS certificate from GitHub and the proxy.
                      The digest then comes over the same unchecked
                      connection, so it no longer proves where the release
                      came from; prefer -- --cacert FILE (see below)
  -- CURL-OPTION...   pass the rest to curl, such as --cacert FILE and
                      --proxy-cacert FILE with a break-and-inspect proxy's
                      CA certificate

Environment:
  UV                  path to the uv binary (default: the uv of the previous
                      run, else uv on PATH)
  UPDATE_URL          GitHub API address of the latest release, for update
                      (default: $UPDATE_URL)
EOF
}

say() { printf '==> %s\n' "$*"; }
warn() { printf 'install.sh: warning: %s\n' "$*" >&2; }
die() { printf 'install.sh: error: %s\n' "$*" >&2; exit 1; }

# ------------------------------------------------------------------- update
# `install.sh update` installs the latest release with the new release's own
# install.sh, which starts from the recorded options like any upgrade, so the
# configuration, key, and store are kept.  The tarball must match the SHA-256
# digest GitHub publishes for it before anything in it runs as root.
UPDATE_URL=${UPDATE_URL:-https://api.github.com/repos/robert-patrick-texas/axlib/releases/latest}
# The exit status of `update --dry-run` when a newer release exists, as with
# `dnf check-update`, so a monitoring job can tell it from an error.
UPDATE_AVAILABLE=100
UPDATE_DIR=
# curl and the options it is given, set by run_update.
CURL=()

# The axlib version in the shared environment, not the one of this script.
installed_version() {
    "$PREFIX/.venv/bin/python" -I -c \
        'from importlib.metadata import version; print(version("axlib"))'
}

# Reads GitHub's JSON description of a release and prints "VERSION NOTES_URL
# TARBALL_URL DIGEST".  The environment's Python reads it, so no jq is needed.
release_info() {
    "$PREFIX/.venv/bin/python" -I -c '
import json, sys
try:
    release = json.load(sys.stdin)
    tag, notes, assets = release["tag_name"], release["html_url"], release["assets"]
except (ValueError, KeyError, TypeError) as error:
    sys.exit(f"not a GitHub release: {error!r}")
version = tag.removeprefix("v")
name = f"axlib-{version}.tar.gz"
asset = next((a for a in assets if a.get("name") == name), None)
if asset is None:
    sys.exit(f"release {tag} has no {name}")
print(version, notes, asset["browser_download_url"], asset.get("digest") or "-")
'
}

# version_newer A B: A is a later version than B.
version_newer() {
    [ "$1" != "$2" ] && [ "$(printf '%s\n' "$1" "$2" | sort -V | tail -n 1)" = "$1" ]
}

run_update() {
    local dry_run=0 proxy='' insecure=0 curl_options=() via='' tls_note='' uv_note=''
    local installed json status info latest notes url digest tarball
    while [ $# -gt 0 ]; do
        case $1 in
        --dry-run) dry_run=1 ;;
        --proxy) proxy=${2:?--proxy needs a URL}; shift ;;
        --proxy=*) proxy=${1#--proxy=}; [ -n "$proxy" ] || die "--proxy needs a URL." ;;
        -k | --insecure) insecure=1 ;;
        # Only after --, so a mistyped update option is not handed to curl.
        --) shift; curl_options=("$@"); break ;;
        -h | --help) usage; exit 0 ;;
        *) usage >&2; die "unknown update option: $1 (options for curl go after --)." ;;
        esac
        shift
    done
    [ "$dry_run" -eq 1 ] || [ "$(id -u)" -eq 0 ] ||
        die "run as root (sudo $0 update), or check with $0 update --dry-run."
    [ "$RECORDED" -eq 1 ] || die "cannot read $INSTALL_RECORD.  Install axlib with install.sh first; to check for updates without root, be a member of the group that owns its directory."
    [ -x "$PREFIX/.venv/bin/python" ] || die "no axlib environment in $PREFIX."
    command -v curl >/dev/null || die "update needs curl (apt-get install curl, or dnf install curl)."

    CURL=(curl -fsSL --retry 2)
    if [ -n "$proxy" ]; then
        CURL+=(--proxy "$proxy")
        # Shown without a password, as in http://user:***@proxy/.
        via=" through $(printf '%s' "$proxy" | sed 's#//\([^/:@]*\):[^/@]*@#//\1:***@#')"
        uv_note=$'\n     uv downloads through the same proxy (HTTPS_PROXY and HTTP_PROXY).'
    fi
    if [ "$insecure" -eq 1 ]; then
        CURL+=(--insecure)
        [ -z "$proxy" ] || CURL+=(--proxy-insecure)
        tls_note=$'\n     TLS certificates are not checked (-k).'
        warn "-k: TLS certificates are not checked.  The digest comes over the same
  connection as the release, so anyone between this host and GitHub could
  replace both.  Prefer: -- --cacert FILE --proxy-cacert FILE"
    fi
    CURL+=(${curl_options[@]+"${curl_options[@]}"})

    installed=$(installed_version) || die "cannot read the axlib version in $PREFIX/.venv."
    json=$("${CURL[@]}" -H 'Accept: application/vnd.github+json' "$UPDATE_URL") || {
        status=$?
        # 60: a certificate on the way, GitHub's or the proxy's, is not trusted.
        [ "$status" -ne 60 ] || die "curl does not trust a TLS certificate on the way to
  $UPDATE_URL.  Behind a TLS-inspecting proxy, add its CA certificate to this
  host's trust store, or give it with -- --cacert FILE --proxy-cacert FILE
  (docs/INSTALL.md, \"Through a proxy\")."
        die "cannot read the latest release from $UPDATE_URL."
    }
    info=$(release_info <<<"$json") || die "$UPDATE_URL did not describe an axlib release."
    read -r latest notes url digest <<<"$info"
    # The version names files and directories below.
    case $latest in '' | *[!0-9A-Za-z.]*) die "unexpected release version: $latest" ;; esac
    say "Installed: axlib $installed in $PREFIX"
    say "Latest release: axlib $latest ($notes)${via}"
    if [ "$latest" = "$installed" ]; then
        printf 'axlib %s is up to date.\n' "$installed"
        exit 0
    elif ! version_newer "$latest" "$installed"; then
        printf 'axlib %s is newer than the latest release; nothing to do.\n' "$installed"
        exit 0
    fi
    case $digest in
    sha256:*) ;;
    *) die "GitHub publishes no SHA-256 digest for $url, so it cannot be checked." ;;
    esac

    if [ "$dry_run" -eq 1 ]; then
        cat <<EOF

axlib $latest is available.  Without --dry-run, update would:
  1. Download $url${via}
     and check it against GitHub's digest, $digest.${tls_note}
  2. Unpack it in a temporary directory and run its install.sh, which starts
     from the options in $INSTALL_RECORD:
     $(grep -E '^(WITH_[A-Z_]+|CREATE_STORE)=' "$INSTALL_RECORD" | paste -s -d ' ' -)${uv_note}
  3. Keep ${CONFIG_FILE:-$CONFIG_DIR/axlib.toml}, the credential stores and
     their keys, and the libraries added with axuv.
  4. Keep axlib $installed in $PREFIX/releases for a rollback.

Release notes: $notes
EOF
        exit "$UPDATE_AVAILABLE"
    fi

    UPDATE_DIR=$(mktemp -d)
    trap 'rm -rf "$UPDATE_DIR"' EXIT
    tarball=$UPDATE_DIR/axlib-$latest.tar.gz
    say "Downloading $url${via}"
    "${CURL[@]}" -o "$tarball" "$url" || die "cannot download $url."
    say "Checking it against GitHub's digest, $digest"
    [ "sha256:$(sha256sum "$tarball" | cut -d ' ' -f 1)" = "$digest" ] ||
        die "$url does not match GitHub's digest; it was not installed."
    tar -xzf "$tarball" -C "$UPDATE_DIR" --no-same-owner
    [ -x "$UPDATE_DIR/axlib-$latest/install.sh" ] ||
        die "axlib-$latest.tar.gz has no axlib-$latest/install.sh."
    say "Running the install.sh of axlib $latest"
    # uv downloads Python and packages through the same proxy.  Both spellings,
    # since programs differ in which one they read first.
    if [ -n "$proxy" ]; then
        export http_proxy=$proxy https_proxy=$proxy HTTP_PROXY=$proxy HTTPS_PROXY=$proxy
    fi
    "$UPDATE_DIR/axlib-$latest/install.sh"
    exit 0
}

if [ "${1:-}" = update ]; then
    shift
    run_update "$@"
fi

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
    --with-all) WITH_ALL=1 ;;
    --with-tui) WITH_TUI=1 ;;
    --no-tui) WITH_TUI=0 WITH_ALL=0 ;;
    --with-textfsm) WITH_TEXTFSM=1 ;;
    --no-textfsm) WITH_TEXTFSM=0 WITH_ALL=0 ;;
    --with-markdown) WITH_MARKDOWN=1 ;;
    --no-markdown) WITH_MARKDOWN=0 WITH_ALL=0 ;;
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

# The extras of the installed axlib requirement, from the line uv writes in
# the project's pyproject.toml: "axlib[markdown,tui]" gives markdown,tui.
installed_extras() {
    sed -n 's/.*"axlib\[\([^]]*\)\].*/\1/p' "$PREFIX/pyproject.toml" 2>/dev/null \
        | tr -d ' ' | head -n 1
}
has_extra() {
    case ",$(installed_extras)," in
    *",$1,"*) return 0 ;;
    *) return 1 ;;
    esac
}

# --with-all, given now or recorded, turns every optional part on; a --no-
# option clears it.  A part that a later release adds belongs in this list
# too, so upgrading a host installed with --with-all also installs that part.
if [ "$WITH_ALL" -eq 1 ]; then
    WITH_TUI=1 WITH_TEXTFSM=1 WITH_MARKDOWN=1
fi

# Options not given and not recorded keep what the host has, so the first run
# of this installer on a host set up by an older one changes nothing unasked.
if [ -z "$WITH_TUI" ]; then
    if has_extra tui; then WITH_TUI=1; else WITH_TUI=0; fi
fi
if [ -z "$WITH_TEXTFSM" ]; then
    if has_extra textfsm; then WITH_TEXTFSM=1; else WITH_TEXTFSM=0; fi
fi
if [ -z "$WITH_MARKDOWN" ]; then
    if has_extra markdown; then WITH_MARKDOWN=1; else WITH_MARKDOWN=0; fi
fi
if [ -z "$WITH_NETENV" ]; then
    if [ -e /etc/profile.d/axlib-netenv.sh ]; then WITH_NETENV=1; else WITH_NETENV=0; fi
fi

# uv must use only its own Python from the shared directory.  Without these,
# uv may pick an interpreter under someone's home directory, which other users
# cannot read, and every axlib command would fail for them.
export UV_PYTHON_INSTALL_DIR=$PYTHON_DIR UV_MANAGED_PYTHON=1
# Any AXLIB_* setting in root's own environment (kept by sudo -E or sudo -i)
# overrides axlib.toml, so it could redirect or reshape the store this run
# sets up: a key, a path, a group, or a Redis cache.  Only axlib.toml counts.
# Variable names contain no spaces, so the unquoted list splits safely.
for name in $(compgen -e AXLIB_); do
    unset "$name"
done
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

# Until the environment runs this release, a failure puts things back: the
# project files saved before `uv remove` below are restored, and the copy and
# wheel of a release that was never installed are removed, so releases/ lists
# only releases that ran.  The trap is cleared once uv add and uv sync succeed.
PROJECT_BACKUP=
NEW_RELEASE=0
[ -e "$RELEASE_DIR" ] || NEW_RELEASE=1
undo_failed_install() {
    if [ -n "$PROJECT_BACKUP" ]; then
        warn "restoring $PREFIX/pyproject.toml and uv.lock"
        cp -p "$PROJECT_BACKUP"/* "$PREFIX/"
        rm -rf "$PROJECT_BACKUP"
    fi
    if [ "$NEW_RELEASE" -eq 1 ]; then
        rm -rf "$RELEASE_DIR"
        rm -f "$RELEASES"/axlib-"$VERSION"-*.whl
    fi
}
trap undo_failed_install EXIT

say "Copying release files to $RELEASE_DIR"
rm -rf "$RELEASE_DIR"
cp -R "$SOURCE_DIR" "$RELEASE_DIR"
rm -rf "$RELEASE_DIR/.venv"

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
UNWANTED=()
if [ "$WITH_TUI" -eq 1 ]; then
    EXTRAS+=(--extra tui)
elif has_extra tui; then
    UNWANTED+=("the credential manager (axlib[tui])")
fi
if [ "$WITH_TEXTFSM" -eq 1 ]; then
    EXTRAS+=(--extra textfsm)
elif has_extra textfsm; then
    UNWANTED+=("show-command parsing (axlib[textfsm])")
fi
if [ "$WITH_MARKDOWN" -eq 1 ]; then
    EXTRAS+=(--extra markdown)
elif has_extra markdown; then
    UNWANTED+=("Markdown-to-HTML conversion (axlib[markdown])")
fi
if [ "${#UNWANTED[@]}" -gt 0 ]; then
    # uv add keeps the extras of an existing requirement, so drop it first.
    # --no-sync leaves .venv alone until the commands below update it.
    for part in "${UNWANTED[@]}"; do say "Removing $part"; done
    PROJECT_BACKUP=$(mktemp -d)
    cp -p "$PREFIX/pyproject.toml" "$PROJECT_BACKUP/"
    [ ! -f "$PREFIX/uv.lock" ] || cp -p "$PREFIX/uv.lock" "$PROJECT_BACKUP/"
    (cd "$PREFIX" && "$UV" remove --quiet --no-sync axlib)
fi
say "Installing $REQUIREMENT ${EXTRAS[*]-} into $PREFIX/.venv"
# --upgrade/--reinstall-package make uv re-read the wheel: a rerun rebuilds
# it with a new hash, and an upgrade replaces the previous release.
(cd "$PREFIX" && "$UV" add --quiet --upgrade-package axlib --reinstall-package axlib \
    "$REQUIREMENT" ${EXTRAS[@]+"${EXTRAS[@]}"})
# uv add only installs; uv sync also uninstalls what uv.lock no longer lists
# (such as textual and rich, or textfsm and ntc-templates, and their
# dependencies).
if [ "${#UNWANTED[@]}" -gt 0 ]; then
    (cd "$PREFIX" && "$UV" sync --quiet)
fi
trap - EXIT
[ -z "$PROJECT_BACKUP" ] || rm -rf "$PROJECT_BACKUP"

install -d -m 0755 "$BIN_DIR"
ln -sfn "$PREFIX/.venv/bin/axlib" "$BIN_DIR/axlib"
"$BIN_DIR/axlib" version >/dev/null || die "$BIN_DIR/axlib does not run."

# Moved only now that the environment runs this release: if the build or
# uv add above fails, current still names the release the environment runs.
say "Pointing $PREFIX/current at axlib-$VERSION"
ln -sfn "releases/axlib-$VERSION" "$PREFIX/current"

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
# The team's own TextFSM templates, searched before ntc-templates because it
# sits beside axlib.toml.  Group members may add templates (2775), and it is
# created only once, so an administrator's chmod 2755 (root only) is kept.  It
# is never removed: --no-textfsm uninstalls the packages, not the templates.
if [ "$WITH_TEXTFSM" -eq 1 ] && [ ! -d "$CONFIG_DIR/textfsm" ]; then
    say "Creating $CONFIG_DIR/textfsm for shared TextFSM templates"
    install -d -o root -g "$GROUP" -m 2775 "$CONFIG_DIR/textfsm"
fi

if [ -e "$AXLIB_CONFIG_FILE" ]; then
    say "Keeping existing $AXLIB_CONFIG_FILE"
else
    # With --no-store there is no key or database, so the store starts off;
    # enabled, every lookup would warn that the database does not exist.  The
    # other [sqlite] lines stay, so turning it on later takes only a run with
    # --store and "enabled = true" here.
    if [ "$CREATE_STORE" -eq 1 ]; then SQLITE_ENABLED=true; else SQLITE_ENABLED=false; fi
    say "Writing $AXLIB_CONFIG_FILE (SQLite store enabled = $SQLITE_ENABLED)"
    cat >"$AXLIB_CONFIG_FILE" <<EOF
# Written by axlib install.sh.  Every option is described in docs/CREDENTIALS.md
# ($PREFIX/current/docs/CREDENTIALS.md).  This file holds no secrets.

# [credentials]
# shared_service = "network-shared"   # fallback account for ax.getkeys()

[sqlite]
enabled = $SQLITE_ENABLED
database = "$DATA_DIR/credentials.db"
key_file = "$CONFIG_DIR/sqlite-aes256.key"
group = "$GROUP"

# axlib parse (installed with --with-textfsm); see docs/PARSING.md.
# [textfsm]
# platform = "cisco_ios"              # used when --platform is not given
# template_dirs = ["textfsm"]         # default: the textfsm directory beside this file
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
WITH_ALL='$WITH_ALL'
WITH_TUI='$WITH_TUI'
WITH_TEXTFSM='$WITH_TEXTFSM'
WITH_MARKDOWN='$WITH_MARKDOWN'
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
# Everything is installed by now, so a store that is not ready (such as a
# text store enabled in axlib.toml but not yet initialized) is reported, not
# turned into a failed install that also skips the summary below.
if [ "$CREATE_STORE" -eq 1 ]; then
    say "Store health check"
    if ! "$PREFIX/.venv/bin/python" -m axlib.credentials.admin; then
        warn "the store health check failed.
  A store enabled in $AXLIB_CONFIG_FILE is not ready, or the file has an
  error; the lines above say which.  axlib itself is installed.  Fix the
  store, then check again with:
    $PREFIX/.venv/bin/python -m axlib.credentials.admin"
    fi
fi

cat <<EOF

axlib $VERSION is installed.

  Command:       $BIN_DIR/axlib   (uses $AXLIB_CONFIG_FILE unless given --config)
  Python:        $PREFIX/.venv/bin/python   (use as the #! line of ax.getkeys() scripts)
  Libraries:     $SBIN_DIR/axuv add netmiko scrapli   (root; uv on the shared environment)
  Options:       $INSTALL_RECORD   (the next install.sh run starts from these)
  Updates:       $PREFIX/current/install.sh update --dry-run   (checks GitHub)
  Docs:          $PREFIX/current/docs/
EOF
if [ "$WITH_TEXTFSM" -eq 1 ]; then
    cat <<EOF
  Parsing:       axlib parse -p cisco_ios -r captures/   (docs/PARSING.md)
  Templates:     $CONFIG_DIR/textfsm/   (the team's .textfsm files, searched first)
EOF
fi
if [ "$WITH_MARKDOWN" -eq 1 ]; then
    cat <<EOF
  Markdown:      axlib md2html notes.md   (writes notes.html; docs/MARKDOWN.md)
EOF
fi
cat <<EOF

Next steps:
  1. Add operators to the group:   usermod -aG $GROUP <user>   (effective at next login)
  2. Add a record per operator:    axlib credential-db add --service <user> \\
                                       --set netuser=<user> --prompt netpass --prompt netenable
  3. As the operator, check:       axlib credentials network
EOF
