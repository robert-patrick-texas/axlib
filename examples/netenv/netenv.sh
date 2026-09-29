# Copyright 2026 Robert Patrick
# SPDX-License-Identifier: Apache-2.0
#
# axlib netenv shell integration for bash and zsh.
#
# Source this file from ~/.bashrc or ~/.zshrc, or install it as
# /etc/profile.d/axlib-netenv.sh.  It defines two shell functions:
#
#   netenv-set [options]   load NETUSER, NETPASS, NETENABLE from axlib
#   netenv-clear           unset all three
#
# and loads the operator's credentials once when an interactive shell starts.
# A program cannot change its parent shell's environment, so the functions run
# the axlib command, capture the shell code it prints, and eval it here.
#
# Settings (export them before this file is sourced):
#   AXLIB_CONFIG_FILE      axlib.toml that enables the credential stores.
#   AXLIB_NETENV_SCRIPTS   Directory holding the uv launchers netenv-set and
#                          netenv-clear (a development checkout's scripts/).
#                          When unset, the installed `axlib` command is used.
#   AXLIB_NETENV_AUTOLOAD  Set to 0 to define the functions without loading
#                          credentials at shell start.
#
# See docs/NETENV.md for warnings and troubleshooting.

# /etc/profile.d is also read by POSIX sh, where "netenv-set" is not a legal
# function name; stop quietly in any shell other than bash or zsh.
[ -n "${BASH_VERSION-}${ZSH_VERSION-}" ] || return 0

_axlib_netenv() {
    # $1 is "set" or "clear"; the remaining arguments go to the axlib command.
    local action=$1 code rc xtrace=
    shift
    # With `set -x` on, eval would echo every export (and the password) to the
    # terminal, so tracing is paused here and restored before returning.
    case $- in *x*) xtrace=1 ;; esac
    { set +x; } 2>/dev/null
    if [ -n "${AXLIB_NETENV_SCRIPTS-}" ]; then
        code="$("$AXLIB_NETENV_SCRIPTS/netenv-$action" "$@")"
    else
        code="$(command axlib "netenv-$action" "$@")"
    fi
    rc=$?
    # Only a successful run is evaluated; on failure stdout is empty anyway and
    # the environment is left exactly as it was.
    [ "$rc" -eq 0 ] && eval "$code"
    [ -n "$xtrace" ] && set -x
    return "$rc"
}

netenv-set() { _axlib_netenv set "$@"; }
netenv-clear() { _axlib_netenv clear "$@"; }

# Autoload in interactive shells only.  bash also reads ~/.bashrc for
# `ssh host command`, scp, sftp, and rsync sessions; those must stay silent and
# should not receive credentials they did not ask for.
case $- in
*i*)
    if [ "${AXLIB_NETENV_AUTOLOAD-1}" != 0 ]; then
        netenv-set --quiet
    fi
    ;;
esac
