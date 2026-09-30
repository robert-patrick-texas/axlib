# Copyright 2026 Robert Patrick
# SPDX-License-Identifier: Apache-2.0

"""Optional terminal user interface (TUI) for axlib credential stores.

The credential manager is an "easy button" for operators who use automation
scripts built on ``netuser, netpass, netenable = ax.getkeys()`` but are not
Python developers.  It runs full-screen in any terminal, including over SSH,
and manages the same encrypted stores as ``axlib credential-db`` and
``axlib credential-file`` through the same Python API
(:mod:`axlib.credentials.admin`).

The TUI is an optional add-on.  Install the extra and start it with any of::

    uv add 'axlib[tui]'
    axlib credential-tui --config /etc/axlib/axlib.toml
    axlib-credential-tui
    python -m axlib.credentials.tui

This module deliberately imports nothing from Textual at import time.  The
launcher first checks that starting a full-screen app is safe and possible,
so a missing dependency or an unsafe environment produces one clear sentence
rather than a traceback.

Dependencies:
    ``textual`` (the optional ``tui`` extra), imported only by :func:`main`.

Example:
    >>> from axlib.credentials.tui import environment_problem
    >>> environment_problem({"TEXTUAL_LOG": "/tmp/textual.log"}) is not None
    True
"""

from __future__ import annotations

import argparse
import importlib.util
import os
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import TextIO

from axlib.credentials.admin import StoreKind
from axlib.credentials.exceptions import CredentialError
from axlib.credentials.settings import CONFIG_OPTION_HELP, load_settings

PROG = "axlib credential-tui"
DEFAULT_IDLE_MINUTES = 5


def environment_problem(environ: Mapping[str, str]) -> str | None:
    """Explain why the environment would make the TUI leak secrets.

    Textual has two debugging aids that record every keystroke -- including
    each character of a password as it is typed:

    * ``TEXTUAL_LOG=<file>`` appends the app's internal event log to a file.
    * ``TEXTUAL=devtools`` (or ``debug``) streams it to a developer console.

    Both are useful when developing Textual apps and dangerous when entering
    credentials, so the credential manager refuses to start with either.

    Args:
        environ (Mapping[str, str]): Environment variables to inspect.

    Returns:
        str | None: A sentence describing the problem, or ``None`` when safe.

    Raises:
        None: Only dictionary lookups and string checks are performed.
    """
    if environ.get("TEXTUAL_LOG"):
        return (
            "TEXTUAL_LOG is set, so Textual would write every keystroke, including "
            "passwords, to that file. Unset TEXTUAL_LOG and try again."
        )
    features = {part.strip().lower() for part in environ.get("TEXTUAL", "").split(",")}
    if features & {"devtools", "debug"}:
        return (
            "TEXTUAL enables devtools/debug mode, which records every keystroke, "
            "including passwords. Unset TEXTUAL and try again."
        )
    return None


def terminal_problem(stdin: TextIO, stdout: TextIO) -> str | None:
    """Explain why the current streams cannot host a full-screen app.

    Args:
        stdin (TextIO): Standard input stream.
        stdout (TextIO): Standard output stream.

    Returns:
        str | None: A sentence describing the problem, or ``None`` when both
            streams are interactive terminals.

    Raises:
        None: ``isatty()`` does not raise on ordinary streams.
    """
    if stdin.isatty() and stdout.isatty():
        return None
    return (
        "the credential manager needs an interactive terminal. Over SSH use "
        "'ssh -t host axlib credential-tui'; in scripts use 'axlib credential-db' "
        "or 'axlib credential-file' instead."
    )


def color_system_for(environ: Mapping[str, str]) -> str:
    """Choose how many colors to use from what the terminal advertises.

    Textual draws into an in-memory buffer rather than directly to the
    terminal, so it cannot detect color support itself and would always send
    24-bit "truecolor" codes.  Those look wrong on terminals that support fewer
    colors -- and over SSH the ``COLORTERM`` variable that advertises truecolor
    is usually not forwarded.  The same conventions Rich uses are applied here:

    * ``COLORTERM=truecolor`` or ``24bit``: 16 million colors.
    * ``TERM`` containing ``256color`` (the common SSH case): 256 colors.
    * anything else: the 16 standard ANSI colors.

    Args:
        environ (Mapping[str, str]): Environment variables to inspect.

    Returns:
        str: A ``TEXTUAL_COLOR_SYSTEM`` value: ``"truecolor"``, ``"256"``, or
            ``"standard"``.

    Raises:
        None: Only dictionary lookups and string checks are performed.
    """
    if environ.get("COLORTERM", "").strip().lower() in {"truecolor", "24bit"}:
        return "truecolor"
    if "256color" in environ.get("TERM", "").lower():
        return "256"
    return "standard"


def build_arg_parser() -> argparse.ArgumentParser:
    """Build the credential manager's command-line parser.

    Args:
        None: Options are fixed by this module.

    Returns:
        argparse.ArgumentParser: Parser for ``--config``, ``--store``, and
            ``--idle-timeout``.

    Raises:
        None: Constructing argparse objects has no side effects.
    """
    parser = argparse.ArgumentParser(
        prog=PROG,
        description=(
            "Full-screen manager for axlib's encrypted credential stores. "
            "Credential values are never displayed."
        ),
    )
    parser.add_argument("--config", type=Path, help=CONFIG_OPTION_HELP)
    parser.add_argument(
        "--store",
        type=StoreKind,
        choices=list(StoreKind),
        help="Store to open first (default: SQLite when configured).",
    )
    parser.add_argument(
        "--idle-timeout",
        type=float,
        default=DEFAULT_IDLE_MINUTES,
        metavar="MINUTES",
        help=(
            "Close after this many minutes without input "
            f"(default: {DEFAULT_IDLE_MINUTES}; 0 disables)."
        ),
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Check the environment, then run the credential manager.

    Args:
        argv (Sequence[str] | None): Arguments excluding the program name.

    Returns:
        int: ``0`` after a normal exit, or ``1`` when the manager cannot start.

    Raises:
        SystemExit: If :mod:`argparse` rejects the arguments.
    """
    parser = build_arg_parser()
    args = parser.parse_args(argv)
    if args.idle_timeout < 0:
        parser.error("--idle-timeout cannot be negative.")

    problem = environment_problem(os.environ)
    if problem is None and importlib.util.find_spec("textual") is None:
        problem = (
            "the credential manager needs the optional 'tui' extra. Install it "
            "with: uv add 'axlib[tui]'"
        )
    if problem is None:
        problem = terminal_problem(sys.stdin, sys.stdout)
    if problem is not None:
        print(f"{PROG}: {problem}", file=sys.stderr)
        return 1

    # Textual reads TEXTUAL_COLOR_SYSTEM once, when it is first imported, so it
    # must be set before the import below.  setdefault() keeps any value the
    # operator chose explicitly.
    os.environ.setdefault("TEXTUAL_COLOR_SYSTEM", color_system_for(os.environ))
    try:
        settings = load_settings(args.config)
        # Imported only now: Textual is an optional dependency, and importing
        # it is the slowest part of starting up.
        from axlib.credentials.tui.app import CredentialAdminApp

        app = CredentialAdminApp(
            settings,
            kind=args.store,
            operator=os.environ.get("USER"),
            idle_minutes=args.idle_timeout,
        )
    except CredentialError as exc:
        print(f"{PROG}: {exc}", file=sys.stderr)
        return 1
    app.run()
    return app.return_code or 0


if __name__ == "__main__":
    raise SystemExit(main())
