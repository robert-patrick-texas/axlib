# Copyright 2026 Robert Patrick
# SPDX-License-Identifier: Apache-2.0

"""Parse show-command output with TextFSM and compare collections of it.

Network operators collect ``show cdp neighbors``, ``show lldp neighbors
detail``, ``show ip interface brief`` and the like before and after a
maintenance window, then compare.  Devices list rows in a different order on
every run and print counters that always change, so comparing the text shows
differences where there are none.  This package parses the output into
records with TextFSM templates (the ntc-templates collection plus an
operator's own ``.textfsm`` files), sorts them by what identifies each row,
and compares collections record by record.

The modules, in the order data flows through them:

* :mod:`axlib.parse.capture` cuts a session log or piped text into one block
  per command.
* :mod:`axlib.parse.templates` finds the template for a command: operator
  directories first, then ntc-templates, accepting abbreviations.
* :mod:`axlib.parse.snapshot` runs TextFSM and builds a :class:`Snapshot`.
* :mod:`axlib.parse.normalize` drops volatile fields, spells interface names
  one way, and sorts.
* :mod:`axlib.parse.diff` compares two snapshots.

Names are loaded lazily, like :mod:`axlib.tf`, so ``python -m`` on a single
module does not import it twice.

Dependencies:
    ``textfsm`` and ``ntc-templates``: ``uv add 'axlib[textfsm]'``.  Splitting
    captures and comparing saved snapshots need only the standard library.

Example::

    from axlib.parse import compare, parse_paths

    before = parse_paths(["before"], platform="cisco_ios", recursive=True)
    after = parse_paths(["after"], platform="cisco_ios", recursive=True)
    print(len(compare(before, after).differences), "commands differ")
"""

from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING

from .errors import (
    AmbiguousCommandError,
    ParseConfigurationError,
    ParseDependencyError,
    ParseError,
    SnapshotFormatError,
    TemplateNotFoundError,
    TemplateParseError,
)

if TYPE_CHECKING:
    from .capture import CommandBlock, split_capture
    from .diff import CommandDiff, RecordChange, SnapshotDiff, compare
    from .normalize import PROFILES, Profile, canonical_interface, normalize_records
    from .settings import ParseSettings, load_parse_settings
    from .snapshot import (
        ParsedCommand,
        Problem,
        Snapshot,
        load_snapshot,
        parse_output,
        parse_paths,
        parse_text,
    )
    from .templates import TemplateCatalog, TemplateMatch

__all__ = [
    "PROFILES",
    "AmbiguousCommandError",
    "CommandBlock",
    "CommandDiff",
    "ParseConfigurationError",
    "ParseDependencyError",
    "ParseError",
    "ParseSettings",
    "ParsedCommand",
    "Problem",
    "Profile",
    "RecordChange",
    "Snapshot",
    "SnapshotDiff",
    "SnapshotFormatError",
    "TemplateCatalog",
    "TemplateMatch",
    "TemplateNotFoundError",
    "TemplateParseError",
    "canonical_interface",
    "compare",
    "load_parse_settings",
    "load_snapshot",
    "normalize_records",
    "parse_output",
    "parse_paths",
    "parse_text",
    "split_capture",
]

_LAZY_EXPORTS = {
    "CommandBlock": ".capture",
    "split_capture": ".capture",
    "CommandDiff": ".diff",
    "RecordChange": ".diff",
    "SnapshotDiff": ".diff",
    "compare": ".diff",
    "PROFILES": ".normalize",
    "Profile": ".normalize",
    "canonical_interface": ".normalize",
    "normalize_records": ".normalize",
    "ParseSettings": ".settings",
    "load_parse_settings": ".settings",
    "ParsedCommand": ".snapshot",
    "Problem": ".snapshot",
    "Snapshot": ".snapshot",
    "load_snapshot": ".snapshot",
    "parse_output": ".snapshot",
    "parse_paths": ".snapshot",
    "parse_text": ".snapshot",
    "TemplateCatalog": ".templates",
    "TemplateMatch": ".templates",
}


def __getattr__(name: str) -> object:
    """Load an export from its module the first time it is used.

    Args:
        name (str): Name requested from :mod:`axlib.parse`.

    Returns:
        object: The class or function.

    Raises:
        AttributeError: If ``name`` is not a package export.
        ImportError: If the implementing module cannot be imported.
    """
    module_name = _LAZY_EXPORTS.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(import_module(module_name, __name__), name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    """Show lazy exports during interactive exploration.

    Args:
        None: Python calls this function without arguments.

    Returns:
        list[str]: Sorted module attributes including lazy exports.

    Raises:
        None: Set and list operations are deterministic.
    """
    return sorted(set(globals()) | set(__all__))
