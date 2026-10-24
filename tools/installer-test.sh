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
# installs the release with every optional part and checks the result.
#
# With --from, it tests what an administrator does instead:
#   1. install the old release with the credential manager (--with-tui),
#   2. upgrade to the new one, adding parsing and Markdown, and check that the
#      recorded options kept the credential manager,
#   3. roll back to the old release as docs/INSTALL.md describes,
#   4. upgrade again with no options.
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

# check_release VERSION PART...: the axlib command runs VERSION, current names
# it, and each optional PART (tui, textfsm, markdown) is recorded and importable.
check_release() {
    local version=$1 part module recorded status=0
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
        recorded="WITH_${part^^}='1'"
        grep -qx "$recorded" "$INSTALL_RECORD" || die "$INSTALL_RECORD lacks $recorded"
        "$PREFIX/.venv/bin/python" -c "import $module" || die "$module is not installed"
    done
}

in_container() {
    local new=$1 old=${2:-} tarball
    apt-get -qq update
    apt-get -qq install -y ca-certificates >/dev/null
    cd /tmp
    for tarball in /releases/*.tar.gz; do tar xzf "$tarball"; done

    if [ -z "$old" ]; then
        install_release "$new" --with-tui --with-textfsm --with-markdown
        check_release "$new" tui textfsm markdown
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

        install_release "$new"
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
