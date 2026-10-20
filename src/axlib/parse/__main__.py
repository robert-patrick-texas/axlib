# Copyright 2026 Robert Patrick
# SPDX-License-Identifier: Apache-2.0

"""Command dispatcher for ``axlib parse``.

``axlib parse PATH ...`` parses captures into a snapshot.  Three more
commands share the ``axlib parse`` name, each implemented by its own module
so it can also run on its own with ``python -m``:

* ``axlib parse diff BEFORE AFTER`` (:mod:`axlib.parse.diff`) compares two
  collections record by record.
* ``axlib parse templates [COMMAND]`` (:mod:`axlib.parse.templates`) shows
  the template search path and which template parses a command.
* ``axlib parse split [PATH]`` (:mod:`axlib.parse.capture`) lists the
  commands found in captures.

A capture file literally named ``diff``, ``templates``, or ``split`` can be
parsed as ``./diff``.

Dependencies:
    The selected module's; see each one.

Example:
    ``axlib parse templates -p cisco_ios sh cdp nei``
"""

from __future__ import annotations

import importlib
import sys
from collections.abc import Sequence

SUBCOMMAND_MODULES = {
    "diff": "axlib.parse.diff",
    "templates": "axlib.parse.templates",
    "split": "axlib.parse.capture",
}


def main(argv: Sequence[str] | None = None) -> int:
    """Run a subcommand, or parse captures when none is named.

    Args:
        argv (Sequence[str] | None): Arguments excluding the program name.

    Returns:
        int: The exit status of the selected command.

    Raises:
        ImportError: If an installed axlib module is missing.
    """
    arguments = list(sys.argv[1:] if argv is None else argv)
    if arguments and arguments[0] in SUBCOMMAND_MODULES:
        # Importing only the selected module keeps each command's startup
        # focused, as the axlib.tf dispatcher does.
        module = importlib.import_module(SUBCOMMAND_MODULES[arguments[0]])
        return int(module.main(arguments[1:]))

    from .snapshot import main as parse_main

    return parse_main(arguments)


if __name__ == "__main__":
    raise SystemExit(main())
