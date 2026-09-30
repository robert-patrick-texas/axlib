# Copyright 2026 Robert Patrick
# SPDX-License-Identifier: Apache-2.0

"""Load an operator's network credentials into, or clear them from, a shell.

Network Operations staff often want ``NETUSER``, ``NETPASS``, and ``NETENABLE``
set as soon as they SSH in to an automation host, so that every tool they run
(``ax.getkeys()`` scripts, Ansible, Netmiko one-liners) finds the same login.

A program cannot change the environment of the shell that started it: a child
process receives a *copy* of the environment, and the copy disappears when the
child exits.  So these commands do not set anything themselves.  They *print*
shell statements, and the shell runs them with ``eval``::

    eval "$(axlib netenv-set)"      # export NETUSER, NETPASS, NETENABLE
    eval "$(axlib netenv-clear)"    # unset all three

Because of that design, standard output carries **only** shell code; every
human-facing message goes to standard error.  Values are quoted with
:func:`shlex.quote`, so a password containing ``$``, quotes, backticks, or
``;`` is assigned literally instead of being run as a command by ``eval``.

The record is chosen exactly the way ``ax.getkeys()`` chooses it: ``$USER``
names the service, and the lookup order is Redis, then SQLite, then the
encrypted text file.  Two ``getkeys()`` behaviors are deliberately *not*
copied: existing ``NET*`` variables are ignored (a stale value from a parent
shell must not look like a successful lookup), and the shared fallback service
is used only when ``--allow-shared`` asks for it.

Dependencies:
    Python standard library plus axlib's credential stores (``cryptography``;
    ``redis`` only when caching is enabled).  POSIX only, because the login
    account is read with :mod:`pwd`.

Example:
    >>> from axlib.credentials.netenv import render_exports, render_unset
    >>> print(render_exports({"netuser": "ops", "netpass": "p$ss w0rd"}))
    export NETUSER=ops
    export NETPASS='p$ss w0rd'
    unset NETENABLE
    >>> render_unset()
    'unset NETUSER NETPASS NETENABLE'
"""

from __future__ import annotations

import argparse
import os
import pwd
import shlex
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from functools import partial
from types import MappingProxyType
from typing import TextIO

from .exceptions import CredentialError
from .manager import (
    Reporter,
    StoreProvider,
    lookup_values,
    normalize_legacy_service_name,
    stderr_reporter,
)
from .profiles import NETWORK_PROFILE, missing_required
from .settings import CONFIG_OPTION_HELP, CredentialSettings, load_settings

# Each network-profile field is exported under its upper-case name
# (netuser -> NETUSER).  Deriving the mapping from the profile keeps a single
# list of network fields for ax.getkeys(), the CLIs, the TUI, and this module.
ENV_VARIABLES: Mapping[str, str] = MappingProxyType(
    {name: name.upper() for name in NETWORK_PROFILE.field_names}
)

# Exit statuses are part of the command's interface: shell functions and login
# scripts branch on them, so each outcome keeps one stable number.
EXIT_EXPORTED = 0
EXIT_NOT_FOUND = 1
EXIT_ERROR = 2
EXIT_TERMINAL = 3


@dataclass(frozen=True, slots=True)
class NetworkLookup:
    """Outcome of one network-credential lookup for a single service.

    Attributes:
        service: Service name as the operator knows it (before normalization).
        values: Every network-profile field mapped to its value or ``None``.
            ``repr=False`` keeps passwords out of tracebacks and debug output.
        problems: Non-secret backend warnings reported during the lookup, such
            as an unreadable key file.  They explain *why* a record was missed.
    """

    service: str
    values: Mapping[str, str | None] = field(repr=False)
    problems: tuple[str, ...] = ()

    @property
    def missing(self) -> tuple[str, ...]:
        """Return required network fields that the lookup did not find.

        Args:
            None: The fields are read from this lookup result.

        Returns:
            tuple[str, ...]: Missing required names; empty when complete.

        Raises:
            None: The profile check only compares strings.
        """
        return missing_required(
            (name for name, value in self.values.items() if value),
            NETWORK_PROFILE,
        )

    @property
    def found(self) -> bool:
        """Return whether the record has every required network field.

        Args:
            None: The fields are read from this lookup result.

        Returns:
            bool: ``True`` when ``netuser`` and ``netpass`` are both present.

        Raises:
            None: Delegates to :attr:`missing`.
        """
        return not self.missing


def operator_service(
    environ: Mapping[str, str] | None = None,
) -> tuple[str, str | None]:
    """Choose the credential service for the operator running this shell.

    ``$USER`` is used first so this command and ``ax.getkeys()`` always read
    the same record.  The login account from the password database is the
    fallback, and a mismatch between the two is reported because it usually
    means ``USER`` was changed by hand.

    Args:
        environ (Mapping[str, str] | None): Environment to read ``USER`` from;
            defaults to :data:`os.environ`.

    Returns:
        tuple[str, str | None]: The service name and an optional non-secret
            warning for the operator.

    Raises:
        CredentialError: If neither ``USER`` nor the login account is known.
    """
    environment = os.environ if environ is None else environ
    user = (environment.get("USER") or "").strip()
    try:
        account = pwd.getpwuid(os.getuid()).pw_name
    except KeyError:
        # Containers can run with a numeric UID that has no /etc/passwd entry.
        account = ""

    if not user and not account:
        raise CredentialError("USER is not set and the login account is unknown.")
    if not user:
        return account, f"USER is not set; using login account {account!r}."
    if account and user != account:
        return user, (
            f"USER={user!r} differs from login account {account!r}; "
            f"using {user!r} as ax.getkeys() would."
        )
    return user, None


def lookup_network_record(
    service: str,
    *,
    settings: CredentialSettings,
    store: StoreProvider | None = None,
    reporter: Reporter = stderr_reporter,
) -> NetworkLookup:
    """Read one service's network fields from the configured stores.

    Every field is requested as ``None`` so values already exported in this
    shell cannot mask what the stores actually contain.

    Args:
        service (str): Operator or shared service name.
        settings (CredentialSettings): Loaded axlib credential settings.
        store (StoreProvider | None): Optional single store, used by tests in
            place of the configured SQLite and text-file stores.
        reporter (Reporter): Receives non-secret backend warnings as they occur.

    Returns:
        NetworkLookup: Field values plus any backend warnings.

    Raises:
        None: :func:`lookup_values` reports backend failures instead of
            raising, and they are returned in :attr:`NetworkLookup.problems`.
    """
    problems: list[str] = []

    def collect(message: str) -> None:
        """Record a backend warning, then pass it on to ``reporter``.

        Keeping a copy lets the caller tell "no record" apart from "the store
        could not be read".

        Args:
            message (str): Non-secret warning from :func:`lookup_values`.

        Returns:
            None: The message is stored and forwarded.

        Raises:
            OSError: If ``reporter`` cannot write the message.
        """
        problems.append(message)
        reporter(message)

    values = lookup_values(
        service,
        dict.fromkeys(NETWORK_PROFILE.field_names),
        settings=settings,
        store=store,
        reporter=collect,
    )
    return NetworkLookup(service=service, values=values, problems=tuple(problems))


def render_exports(values: Mapping[str, str | None]) -> str:
    """Render ``export`` statements for bash, zsh, and other POSIX shells.

    A field without a value is emitted as ``unset`` so a value inherited from
    an earlier session (for example an old enable secret) cannot linger.

    Args:
        values (Mapping[str, str | None]): Network fields such as ``netpass``.

    Returns:
        str: Newline-separated shell statements, safe to pass to ``eval``.

    Raises:
        None: Quoting and string joins cannot fail.
    """
    lines = []
    for name, variable in ENV_VARIABLES.items():
        value = values.get(name)
        if value:
            # shlex.quote wraps the text in single quotes when needed; inside
            # them the shell performs no expansion, so eval assigns it literally.
            lines.append(f"export {variable}={shlex.quote(value)}")
        else:
            lines.append(f"unset {variable}")
    return "\n".join(lines)


def render_unset() -> str:
    """Render the statement that removes every network variable.

    Args:
        None: The variable names come from :data:`ENV_VARIABLES`.

    Returns:
        str: A single ``unset`` statement for ``eval``.

    Raises:
        None: String joins cannot fail.
    """
    return "unset " + " ".join(ENV_VARIABLES.values())


def _refuse_terminal(command: str, stream: TextIO) -> int:
    """Explain the ``eval`` pattern instead of writing shell code to a screen.

    Typing ``axlib netenv-set`` on its own would print the password on the
    terminal (and into scrollback, ``script`` logs, and screen sharing) while
    changing nothing, so the command stops and shows the correct usage.

    Args:
        command (str): Sub-command name shown in the message.
        stream (TextIO): Standard-error stream for the explanation.

    Returns:
        int: :data:`EXIT_TERMINAL`.

    Raises:
        OSError: If the explanation cannot be written.
    """
    stderr_reporter(
        f"{command} prints shell code for eval and will not write it to a "
        f'terminal. Run:  eval "$(axlib {command})"',
        stream=stream,
    )
    return EXIT_TERMINAL


def build_set_parser() -> argparse.ArgumentParser:
    """Build the ``axlib netenv-set`` argument parser.

    Args:
        None: Options are defined by this module.

    Returns:
        argparse.ArgumentParser: Parser for the set command.

    Raises:
        None: Parser construction performs no backend access.
    """
    parser = argparse.ArgumentParser(
        prog="axlib netenv-set",
        description=(
            "Print export statements for NETUSER, NETPASS, and NETENABLE from "
            'the operator\'s axlib record. Use with: eval "$(axlib netenv-set)"'
        ),
        epilog=(
            "Exit status: 0 exported, 1 no complete record, 2 configuration or "
            "store error, 3 refused because standard output is a terminal."
        ),
    )
    parser.add_argument(
        "--config",
        help=CONFIG_OPTION_HELP,
    )
    parser.add_argument(
        "--service",
        help="Credential service to load instead of $USER.",
    )
    parser.add_argument(
        "--allow-shared",
        action="store_true",
        help="Fall back to the configured shared service when the operator "
        "record is missing or incomplete.",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Report which fields would be set (never their values) on "
        "standard error, and export nothing.",
    )
    parser.add_argument(
        "-q",
        "--quiet",
        action="store_true",
        help="Suppress the success and not-found notices; warnings still print.",
    )
    return parser


def _find_record(
    args: argparse.Namespace,
    settings: CredentialSettings,
    environ: Mapping[str, str] | None,
    report: Reporter,
) -> NetworkLookup:
    """Look up the operator record, then the shared one if the flags allow it.

    Args:
        args (argparse.Namespace): Parsed ``netenv-set`` options.
        settings (CredentialSettings): Loaded axlib credential settings.
        environ (Mapping[str, str] | None): Environment used to find ``$USER``.
        report (Reporter): Receives non-secret warnings.

    Returns:
        NetworkLookup: The lookup whose values should be exported or reported.

    Raises:
        CredentialError: If no operator name can be determined.
    """
    service = args.service
    if not service:
        service, warning = operator_service(environ)
        if warning:
            report(f"WARNING: {warning}")

    result = lookup_network_record(service, settings=settings, reporter=report)
    shared = settings.shared_service
    if (
        not result.found
        and args.allow_shared
        and shared
        and normalize_legacy_service_name(shared)
        != normalize_legacy_service_name(service)
    ):
        report(
            f"WARNING: record {service!r} is missing {', '.join(result.missing)}; "
            f"using shared service {shared!r}."
        )
        result = lookup_network_record(shared, settings=settings, reporter=report)
    return result


def set_main(
    argv: Sequence[str] | None = None,
    *,
    environ: Mapping[str, str] | None = None,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
) -> int:
    """Run ``axlib netenv-set``.

    Args:
        argv (Sequence[str] | None): Arguments excluding the command name;
            defaults to :data:`sys.argv`.
        environ (Mapping[str, str] | None): Environment for ``USER`` and axlib
            settings; defaults to :data:`os.environ`.
        stdout (TextIO | None): Destination for shell code only.
        stderr (TextIO | None): Destination for every operator message.

    Returns:
        int: One of the ``EXIT_*`` statuses defined in this module.

    Raises:
        SystemExit: If argparse rejects the command line.
    """
    args = build_set_parser().parse_args(argv)
    out = sys.stdout if stdout is None else stdout
    err = sys.stderr if stderr is None else stderr

    # partial() fixes the stream argument, giving a one-argument Reporter.
    report = partial(stderr_reporter, stream=err)

    # Checked before any store is opened, so a mistaken bare run costs nothing
    # and never holds the password in this process.
    if not args.check and out.isatty():
        return _refuse_terminal("netenv-set", err)

    try:
        settings = load_settings(args.config, environ=environ)
        if not (
            settings.sqlite_enabled
            or settings.credential_file_enabled
            or settings.redis_enabled
        ):
            report(
                "ERROR: no credential store is enabled. Set AXLIB_CONFIG_FILE or "
                "pass --config with the path to axlib.toml."
            )
            return EXIT_ERROR
        result = _find_record(args, settings, environ, report)
    except CredentialError as exc:
        report(f"ERROR: {exc}")
        return EXIT_ERROR

    if args.check:
        # The report goes to stderr too: stdout is reserved for shell code, so
        # even `eval "$(axlib netenv-set --check)"` shows it and runs nothing.
        report(f"service={result.service}")
        for name, variable in ENV_VARIABLES.items():
            state = "set" if result.values.get(name) else "missing"
            report(f"{variable}={state}")

    if not result.found:
        cause = "could not be read" if result.problems else "was not found"
        if result.problems or not args.quiet:
            report(
                f"netenv: a complete network record for {result.service!r} {cause}"
                f" (missing {', '.join(result.missing)}); environment unchanged."
            )
        return EXIT_ERROR if result.problems else EXIT_NOT_FOUND

    if args.check:
        return EXIT_EXPORTED

    print(render_exports(result.values), file=out)
    if not args.quiet:
        exported = [v for n, v in ENV_VARIABLES.items() if result.values.get(n)]
        report(f"netenv: exported {', '.join(exported)} for {result.service!r}.")
    return EXIT_EXPORTED


def build_clear_parser() -> argparse.ArgumentParser:
    """Build the ``axlib netenv-clear`` argument parser.

    Args:
        None: The command has no options beyond ``--help``.

    Returns:
        argparse.ArgumentParser: Parser for the clear command.

    Raises:
        None: Parser construction performs no backend access.
    """
    return argparse.ArgumentParser(
        prog="axlib netenv-clear",
        description=(
            "Print the unset statement for NETUSER, NETPASS, and NETENABLE. "
            'Use with: eval "$(axlib netenv-clear)"'
        ),
    )


def clear_main(
    argv: Sequence[str] | None = None,
    *,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
) -> int:
    """Run ``axlib netenv-clear``.

    Args:
        argv (Sequence[str] | None): Arguments excluding the command name;
            defaults to :data:`sys.argv`.
        stdout (TextIO | None): Destination for the ``unset`` statement.
        stderr (TextIO | None): Destination for the terminal-usage message.

    Returns:
        int: :data:`EXIT_EXPORTED` on success or :data:`EXIT_TERMINAL`.

    Raises:
        SystemExit: If argparse rejects the command line.
    """
    build_clear_parser().parse_args(argv)
    out = sys.stdout if stdout is None else stdout
    err = sys.stderr if stderr is None else stderr

    # Printing "unset ..." to a screen would look like it worked while the
    # variables stay set, so a bare run explains the eval pattern instead.
    if out.isatty():
        return _refuse_terminal("netenv-clear", err)
    print(render_unset(), file=out)
    return EXIT_EXPORTED


def main(argv: Sequence[str] | None = None) -> int:
    """Run ``python -m axlib.credentials.netenv set|clear [options]``.

    Args:
        argv (Sequence[str] | None): ``set`` or ``clear`` followed by that
            command's options; defaults to :data:`sys.argv`.

    Returns:
        int: Status from :func:`set_main` or :func:`clear_main`.

    Raises:
        SystemExit: If the action is missing or argparse rejects an option.
    """
    arguments = list(sys.argv[1:] if argv is None else argv)
    actions = {"set": set_main, "clear": clear_main}
    if not arguments or arguments[0] not in actions:
        build_set_parser().exit(
            EXIT_ERROR, "usage: python -m axlib.credentials.netenv {set,clear} ...\n"
        )
    return actions[arguments[0]](arguments[1:])


if __name__ == "__main__":
    raise SystemExit(main())
