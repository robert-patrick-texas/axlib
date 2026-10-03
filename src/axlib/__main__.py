# Copyright 2026 Robert Patrick
# SPDX-License-Identifier: Apache-2.0

"""Top-level command dispatcher for the axlib teaching package.

The package command groups text processing, credential diagnostics, AES-256-GCM
text-file management, encrypted SQLite management, the optional full-screen
credential manager, the shell-session credential loaders, and a RADIUS
login check behind discoverable subcommands. Existing module commands and
Python imports remain available, so engineers can move gradually from a shell
pipeline to reusable Python functions.

Dependencies vary by command: text filters use only the standard library, while
credential commands need the packages documented in ``docs/CREDENTIALS.md``.

Example:
    ``python -m axlib tf variables -v hostname=edge-01 template.txt``
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable, Sequence

from . import __version__

CommandMain = Callable[[Sequence[str] | None], int]

# One tuple lists every sub-command, so the parser's choices and the dispatch
# check below cannot drift apart when a command is added.
COMMANDS = (
    "version",
    "tf",
    "credentials",
    "credential-file",
    "credential-db",
    "credential-tui",
    "netenv-set",
    "netenv-clear",
    "radius",
)


def build_arg_parser() -> argparse.ArgumentParser:
    """Build the top-level axlib help parser.

    Args:
        None: Command metadata is fixed by the package.

    Returns:
        argparse.ArgumentParser: Parser used for global help and command checks.

    Raises:
        None: Parser construction performs no backend access.
    """
    parser = argparse.ArgumentParser(
        prog="python -m axlib",
        description="Educational network-automation helpers.",
        epilog=(
            f"Commands: {', '.join(COMMANDS)}. Add --help after a command for "
            "command-specific options."
        ),
    )
    parser.add_argument(
        "-V",
        "--version",
        action="version",
        version=__version__,
        help="Show the installed axlib version and exit.",
    )
    parser.add_argument("command", nargs="?", choices=COMMANDS)
    return parser


def _load_command(command: str) -> CommandMain:
    """Import a command entry point only when it is requested.

    Args:
        command (str): One of ``tf``, ``credentials``, ``credential-file``,
            ``credential-db``, ``credential-tui``, ``netenv-set``,
            ``netenv-clear``, or ``radius``.

    Returns:
        CommandMain: Callable accepting an optional argument sequence.

    Raises:
        ValueError: If ``command`` has no dispatch target.
        ImportError: If an installed command module is unavailable.
    """
    if command == "tf":
        from .tf.__main__ import main

        return main
    if command == "credentials":
        from .secrets import main

        return main
    if command == "credential-file":
        from .credentials.file_cli import main

        return main
    if command == "credential-db":
        from .credentials.sqlite_cli import main

        return main
    if command == "credential-tui":
        # The TUI package imports Textual only after its own safety checks, so
        # this import succeeds even when the optional extra is not installed.
        from .credentials.tui import main

        return main
    if command == "netenv-set":
        from .credentials.netenv import set_main

        return set_main
    if command == "netenv-clear":
        from .credentials.netenv import clear_main

        return clear_main
    if command == "radius":
        from .radius import main

        return main
    raise ValueError(f"Unsupported axlib command: {command}")


def main(argv: Sequence[str] | None = None) -> int:
    """Dispatch the selected package command.

    Args:
        argv (Sequence[str] | None): Arguments excluding the module name.

    Returns:
        int: Command exit status, or ``0`` for help/version output.

    Raises:
        ImportError: If a selected installed command module is missing.
        ValueError: If internal dispatch metadata is inconsistent.
    """
    arguments = list(sys.argv[1:] if argv is None else argv)
    parser = build_arg_parser()
    if not arguments or arguments[0] in {"-h", "--help"}:
        parser.print_help()
        return 0

    command = arguments.pop(0)
    if command in {"-V", "--version", "version"}:
        # Supporting both a conventional flag and the teaching-oriented command
        # makes shell usage intuitive without breaking the documented subcommand.
        print(__version__)
        return 0
    if command not in COMMANDS:
        parser.error(f"unknown command: {command}")

    return _load_command(command)(arguments)


if __name__ == "__main__":
    raise SystemExit(main())
