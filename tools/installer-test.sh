#!/usr/bin/env bash
# Copyright 2026 Robert Patrick
# SPDX-License-Identifier: Apache-2.0
#
# Test install.sh end to end in a disposable container.
#
#   tools/installer-test.sh dist/axlib-<version>.tar.gz
#   tools/installer-test.sh --from axlib-<old-version>.tar.gz dist/axlib-<version>.tar.gz
#
# install.sh needs root and writes to /opt, /etc, and /var/lib, so it is never
# run on the development host.  This script starts a throwaway Ubuntu
# container with Docker, mounts itself, the release tarballs, and the host's
# uv binary read-only, and runs again inside the container, as root, where it
# installs the release with every optional part (--with-all), checks the
# result, then removes parsing (--no-textfsm) and checks that only parsing
# and the recorded --with-all are gone.
#
# With --from, it tests what an administrator does instead:
#   1. install the old release with the credential manager (--with-tui),
#   2. upgrade to the new one, adding parsing and Markdown, and check that the
#      recorded options kept the credential manager,
#   3. roll back to the old release as docs/INSTALL.md describes,
#   4. upgrade again with `install.sh update`, from a stand-in for GitHub that
#      offers the new release: a tarball that does not match its digest is
#      refused, --dry-run reports, a certificate curl does not trust is
#      refused unless -k or -- --cacert is given, a mistyped option is
#      refused, and the update, through a proxy (tinyproxy) with --proxy,
#      keeps the configuration, key, and a credential record.
# A fresh install also checks that `update --dry-run` finds it up to date, as
# root and as a member of netops.
#
# `make install-test` runs it on a fresh build; CI runs it with --from the
# previous release.  A run downloads Python and packages: about 2 minutes.
#
# Environment:
#   INSTALL_TEST_IMAGE   container image (default: ubuntu:26.04)
#   UV                   uv binary to mount (default: uv on PATH)

set -euo pipefail

PREFIX=/opt/shared/axlib
INSTALL_RECORD=/etc/axlib/install.env
# The stand-in for GitHub's releases API that `install.sh update` reads, over
# HTTP and over HTTPS with a self-signed certificate.
GITHUB=http://127.0.0.1:8000
GITHUB_TLS=https://127.0.0.1:8443
GITHUB_DIR=/tmp/github
GITHUB_CERT=/tmp/github.pem
# The stand-in for an organization's proxy, and its log of what it passed on.
PROXY=http://127.0.0.1:8888/
PROXY_LOG=/tmp/tinyproxy.log

say() { printf '==> %s\n' "$*"; }
die() { printf 'installer-test: error: %s\n' "$*" >&2; exit 1; }
usage() {
    printf 'Usage: %s [--from OLD.tar.gz] NEW.tar.gz\n' "$0" >&2
    exit 2
}

# .../axlib-<version>.tar.gz -> <version>
version_of() {
    local name
    name=$(basename "$1" .tar.gz)
    printf '%s\n' "${name#axlib-}"
}

# ------------------------------------------------------------------ host side

on_host() {
    local old='' new uv image=${INSTALL_TEST_IMAGE:-ubuntu:26.04}
    if [ "${1:-}" = --from ]; then
        old=${2:?--from needs a tarball}
        shift 2
    fi
    [ $# -eq 1 ] || usage
    new=$1
    command -v docker >/dev/null || die "Docker is needed to run install.sh in a container"
    uv=${UV:-$(command -v uv)} || die "uv was not found; pass its location: UV=/path/to/uv $0 ..."

    # Each tarball keeps its file name under /releases, which is how the
    # container side learns the versions.
    local mounts=(
        -v "$(readlink -f "$0")":/installer-test.sh:ro
        -v "$(readlink -f "$uv")":/usr/local/bin/uv:ro
    )
    local tarball tarballs=("$new")
    [ -z "$old" ] || tarballs+=("$old")
    for tarball in "${tarballs[@]}"; do
        [ -f "$tarball" ] || die "not found: $tarball"
        mounts+=(-v "$(readlink -f "$tarball")":"/releases/$(basename "$tarball")":ro)
    done
    if [ -n "$old" ] && [ "$(version_of "$old")" = "$(version_of "$new")" ]; then
        die "--from must name an earlier release than $(basename "$new")"
    fi

    docker run --rm "${mounts[@]}" "$image" \
        bash /installer-test.sh --in-container "$(version_of "$new")" \
        ${old:+"$(version_of "$old")"}
}

# ------------------------------------------------------------- container side

install_release() {
    local version=$1
    shift
    say "Running install.sh of axlib $version${*:+ $*}"
    "/tmp/axlib-$version/install.sh" "$@"
}

# check_recorded OPTION VALUE: install.env records OPTION='VALUE'.
check_recorded() {
    grep -qx "$1='$2'" "$INSTALL_RECORD" || die "$INSTALL_RECORD lacks $1='$2'"
}

# check_release VERSION PART...: the axlib command runs VERSION, current names
# it, and each optional PART (tui, textfsm, markdown) is recorded and importable.
check_release() {
    local version=$1 part module status=0
    shift
    say "Checking axlib $version${*:+ with $*}"
    [ "$(axlib version)" = "$version" ] || die "axlib version printed $(axlib version), not $version"
    [ "$(readlink "$PREFIX/current")" = "releases/axlib-$version" ] ||
        die "$PREFIX/current points at $(readlink "$PREFIX/current")"
    # 0 or 1 (credentials found or missing) means axlib read its configuration
    # and store; 2 means it could not.
    axlib credentials network || status=$?
    [ "$status" -le 1 ] || die "axlib credentials network exited with status $status"
    for part in "$@"; do
        case $part in
        tui) module=textual ;;
        textfsm) module=ntc_templates ;;
        markdown) module=markdown_it ;;
        *) die "unknown optional part: $part" ;;
        esac
        check_recorded "WITH_${part^^}" 1
        "$PREFIX/.venv/bin/python" -c "import $module" || die "$module is not installed"
    done
}

# wait_for URL [CURL-OPTION...]: wait until a server answers at URL.
wait_for() {
    local url=$1
    shift
    for _ in $(seq 50); do
        curl -fs -o /dev/null "$@" "$url" && return
        sleep 0.1
    done
    die "nothing answers at $url"
}

# A static web server: DIRECTORY on 127.0.0.1:PORT, over HTTPS when given a
# certificate and key.
SERVER='
import functools, http.server, ssl, sys
directory, port, *tls = sys.argv[1:]
handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=directory)
server = http.server.ThreadingHTTPServer(("127.0.0.1", int(port)), handler)
if tls:
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(*tls)
    server.socket = context.wrap_socket(server.socket, server_side=True)
server.serve_forever()
'

# Static web servers answer for GitHub at GITHUB and GITHUB_TLS, so `install.sh
# update` can be tested with releases that are not published.  The
# environment's Python runs them, since the image has none of its own.
start_github() {
    mkdir -p "$GITHUB_DIR/releases"
    openssl req -x509 -newkey rsa:2048 -nodes -days 1 -subj /CN=127.0.0.1 \
        -addext subjectAltName=IP:127.0.0.1 -keyout /tmp/github.key \
        -out "$GITHUB_CERT" 2>/dev/null
    "$PREFIX/.venv/bin/python" -c "$SERVER" "$GITHUB_DIR" 8000 >/dev/null 2>&1 &
    "$PREFIX/.venv/bin/python" -c "$SERVER" "$GITHUB_DIR" 8443 \
        "$GITHUB_CERT" /tmp/github.key >/dev/null 2>&1 &
    export UPDATE_URL=$GITHUB/releases/latest
    wait_for "$GITHUB/"
    wait_for "$GITHUB_TLS/" --cacert "$GITHUB_CERT"
}

# tinyproxy stands in for an organization's proxy at PROXY.
start_proxy() {
    apt-get -qq install -y tinyproxy >/dev/null
    cat >/tmp/tinyproxy.conf <<EOF
Port 8888
Listen 127.0.0.1
Allow 127.0.0.1
Timeout 600
LogFile "$PROXY_LOG"
LogLevel Connect
EOF
    tinyproxy -c /tmp/tinyproxy.conf
    wait_for "$GITHUB/" --proxy "$PROXY"
}

# serve_release VERSION [DIGEST]: GitHub's latest release is VERSION, with
# the SHA-256 digest of its tarball, or DIGEST instead.
serve_release() {
    local version=$1 digest
    cp "/releases/axlib-$version.tar.gz" "$GITHUB_DIR/"
    digest=${2:-sha256:$(sha256sum "$GITHUB_DIR/axlib-$version.tar.gz" | cut -d ' ' -f 1)}
    cat >"$GITHUB_DIR/releases/latest" <<EOF
{"tag_name": "v$version", "html_url": "$GITHUB/v$version",
 "assets": [{"name": "axlib-$version.tar.gz", "digest": "$digest",
             "browser_download_url": "$GITHUB/axlib-$version.tar.gz"}]}
EOF
}

# check_up_to_date VERSION [USER]: with VERSION installed and offered, update
# --dry-run, run as root or as USER, says so and exits 0.
check_up_to_date() {
    local version=$1 as=() output
    [ -z "${2:-}" ] || as=(runuser -u "$2" --)
    say "Checking that update --dry-run${2:+ as $2} finds axlib $version up to date"
    output=$(${as[@]+"${as[@]}"} "/tmp/axlib-$version/install.sh" update --dry-run) ||
        die "update --dry-run failed or found an update with axlib $version installed"
    [[ $output == *"axlib $version is up to date."* ]] || die "update --dry-run printed: $output"
}

# check_curl_options UPDATE: against a certificate curl does not trust, the
# update command UPDATE fails, accepts it with -k and a warning, and trusts it
# with -- --cacert, which shows that options after -- reach curl.  A mistyped
# option is refused, not passed to curl.
check_curl_options() {
    local update=$1 tls_url=$GITHUB_TLS/releases/latest output status=0
    say "Checking that update --dry-run refuses a certificate curl does not trust"
    output=$(UPDATE_URL=$tls_url "$update" update --dry-run 2>&1) || status=$?
    [ "$status" -eq 1 ] || die "update --dry-run exited with $status, not 1"
    [[ $output == *"curl does not trust a TLS certificate"* ]] ||
        die "update --dry-run did not explain the refused certificate: $output"

    say "Checking that update --dry-run -k accepts it, with a warning"
    status=0
    output=$(UPDATE_URL=$tls_url "$update" update --dry-run -k 2>&1) || status=$?
    [ "$status" -eq 100 ] || die "update --dry-run -k exited with $status, not 100: $output"
    [[ $output == *"-k: TLS certificates are not checked."* ]] || die "-k did not warn: $output"

    say "Checking that update --dry-run -- --cacert trusts it"
    status=0
    UPDATE_URL=$tls_url "$update" update --dry-run -- --cacert "$GITHUB_CERT" >/dev/null ||
        status=$?
    [ "$status" -eq 100 ] || die "update --dry-run -- --cacert exited with $status, not 100"

    say "Checking that update refuses an option it does not know"
    status=0
    "$update" update --dryrun || status=$?
    [ "$status" -eq 1 ] || die "update --dryrun exited with $status, not 1"
}

# check_update OLD NEW: with OLD installed and NEW offered, NEW's install.sh
# update refuses a tarball that does not match its digest, reports with
# --dry-run, passes options to curl (check_curl_options), then installs NEW
# through a proxy and keeps axlib.toml, the key, and a record.
check_update() {
    local old=$1 new=$2 update="/tmp/axlib-$2/install.sh" output kept status=0
    axlib credential-db add --service root --set netuser=root --set netpass=kept >/dev/null
    kept=$(sha256sum /etc/axlib/axlib.toml /etc/axlib/sqlite-aes256.key)

    say "Checking that update refuses a tarball that does not match its digest"
    serve_release "$new" "sha256:$(printf '0%.0s' {1..64})"
    "$update" update || status=$?
    [ "$status" -ne 0 ] || die "update installed a tarball that does not match its digest"
    [ "$(axlib version)" = "$old" ] || die "a refused update changed axlib to $(axlib version)"

    say "Checking that update --dry-run reports axlib $new"
    serve_release "$new"
    status=0
    output=$("$update" update --dry-run) || status=$?
    printf '%s\n' "$output"
    [ "$status" -eq 100 ] || die "update --dry-run exited with $status, not 100"
    [[ $output == *"axlib $new is available."* ]] || die "update --dry-run did not report axlib $new"
    [ "$(axlib version)" = "$old" ] || die "update --dry-run changed axlib to $(axlib version)"

    check_curl_options "$update"
    [ "$(axlib version)" = "$old" ] || die "a check changed axlib to $(axlib version)"

    say "Updating axlib $old to $new with install.sh update --proxy"
    start_proxy
    # An empty cache makes uv download, so the log shows whether it used the proxy.
    uv cache clean --quiet
    "$update" update --proxy="$PROXY"
    grep -qF "GET $GITHUB/releases/latest " "$PROXY_LOG" ||
        die "update did not read the latest release through the proxy"
    grep -qF "GET $GITHUB/axlib-$new.tar.gz " "$PROXY_LOG" ||
        die "update did not download axlib-$new.tar.gz through the proxy"
    grep -qF "CONNECT pypi.org:443 " "$PROXY_LOG" ||
        die "uv did not reach pypi.org through the proxy"
    [ "$(sha256sum /etc/axlib/axlib.toml /etc/axlib/sqlite-aes256.key)" = "$kept" ] ||
        die "update changed axlib.toml or the key"
    output=$(USER=root axlib credentials network) || true
    [[ $output == *netuser=set*netpass=set* ]] ||
        die "the credential record did not survive the update: $output"
    check_up_to_date "$new"
}

in_container() {
    local new=$1 old=${2:-} tarball
    apt-get -qq update
    apt-get -qq install -y ca-certificates curl >/dev/null
    cd /tmp
    for tarball in /releases/*.tar.gz; do tar xzf "$tarball"; done

    if [ -z "$old" ]; then
        install_release "$new" --with-all
        check_release "$new" tui textfsm markdown
        check_recorded WITH_ALL 1
        start_github
        serve_release "$new"
        check_up_to_date "$new"
        # Members of the group may check without root.
        useradd --groups netops alice
        check_up_to_date "$new" alice

        # Not --no-markdown: rich and textual need markdown-it-py too.
        install_release "$new" --no-textfsm
        check_release "$new" tui markdown
        check_recorded WITH_ALL 0
        check_recorded WITH_TEXTFSM 0
        if "$PREFIX/.venv/bin/python" -c "import ntc_templates" 2>/dev/null; then
            die "--no-textfsm left ntc_templates installed"
        fi
    else
        install_release "$old" --with-tui
        check_release "$old" tui
        install_release "$new" --with-textfsm --with-markdown
        check_release "$new" tui textfsm markdown

        say "Rolling back to axlib $old"
        axuv add --upgrade-package axlib --reinstall-package axlib \
            "./releases/axlib-$old-py3-none-any.whl"
        ln -sfn "releases/axlib-$old" "$PREFIX/current"
        check_release "$old"

        start_github
        check_update "$old" "$new"
        check_release "$new" tui textfsm markdown
    fi
    say "install.sh test passed"
}

if [ "${1:-}" = --in-container ]; then
    shift
    in_container "$@"
else
    on_host "$@"
fi
