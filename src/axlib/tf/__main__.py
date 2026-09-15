# Copyright 2026 Robert Patrick
# SPDX-License-Identifier: Apache-2.0

"""Unified command dispatcher for axlib text filters.

Running ``python -m axlib.tf`` exposes consistent, descriptive command names for
all text transformations while each original module remains directly runnable.
This is useful in network-automation pipelines where one tool expands fragments,
another removes comments, and a third substitutes inventory values.

The dispatcher has no dependencies beyond the modules in :mod:`axlib.tf`.

Example:
    ``python -m axlib.tf comments router.conf | python -m axlib.tf whitespace``
"""

from __future__ import annotations

import argparse
import importlib
import sys
from collections.abc import Sequence

COMMAND_MODULES = {
    "include": "axlib.tf.incfile",
    "comments": "axlib.tf.rmcomment",
    "slash-comments": "axlib.tf.rmdouble",
    "triple-quotes": "axlib.tf.rmtriple",
    "lines": "axlib.tf.rmline",
    "whitespace": "axlib.tf.rmwhite",
    "whitespace-safe": "axlib.tf.rmwhite2",
    "variables": "axlib.tf.varsub",
}


def build_arg_parser() -> argparse.ArgumentParser:
    """Build the top-level text-filter help parser.

    Args:
        None: Command metadata is defined by :data:`COMMAND_MODULES`.

    Returns:
        argparse.ArgumentParser: Parser used for global help and command checks.

    Raises:
        None: Constructing the parser does not import individual filter modules.
    """
    parser = argparse.ArgumentParser(
        prog="python -m axlib.tf",
        description="Run an axlib streaming text filter.",
        epilog=(
            "Commands: include, comments, slash-comments, triple-quotes, lines, "
            "whitespace, whitespace-safe, variables. Add --help after a command "
            "for its options."
        ),
    )
    parser.add_argument("command", nargs="?", choices=sorted(COMMAND_MODULES))
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Dispatch one standardized command to its module ``main`` function.

    Args:
        argv (Sequence[str] | None): Arguments excluding the package module name.

    Returns:
        int: Exit status returned by the selected filter, or ``0`` after global
            help is displayed.

    Raises:
        AttributeError: If an internal command module does not expose ``main``;
            this indicates a packaging defect rather than operator input.
        ImportError: If an installed axlib filter module is missing.
    """
    arguments = list(sys.argv[1:] if argv is None else argv)
    parser = build_arg_parser()
    if not arguments or arguments[0] in {"-h", "--help"}:
        parser.print_help()
        return 0

    command = arguments.pop(0)
    if command not in COMMAND_MODULES:
        parser.error(f"unknown command: {command}")

    # Importing only the selected module keeps command startup focused and gives
    # learners a clear one-command-to-one-module relationship.
    module = importlib.import_module(COMMAND_MODULES[command])
    return int(module.main(arguments))


if __name__ == "__main__":
    raise SystemExit(main())
