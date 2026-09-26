# Copyright 2026 Robert Patrick
# SPDX-License-Identifier: Apache-2.0

"""Shared command-line helpers for axlib credential-store administration.

Both ``axlib credential-file`` and ``axlib credential-db`` intentionally expose
the same operator workflow.  This module holds their common input-validation and
secret-collection code so the two CLIs cannot drift into subtly different rules.
That consistency is useful on Network Operations hosts where staff may switch
between the text and SQLite stores without relearning command syntax.

Secret values can be supplied directly, read from an existing environment
variable, or entered with terminal echo disabled.  Direct command-line secrets
are supported for scripting convenience but secure prompts or injected
environment variables are preferable because shell history is often retained on
shared jump hosts.

The module also provides :func:`render_table`, the one aligned-column text
renderer used by every credential command that prints a table.  Plain aligned
columns (rather than box-drawing characters) keep the output friendly to
``grep``, ``awk``, and ``cut`` in shell pipelines.

Dependencies:
    Only the Python standard library and :mod:`axlib.credentials.manager`.

Example:
    >>> normalize_service_for_write("first.last")
    'firstlast'
    >>> parse_assignment("netuser=operator")
    ('netuser', 'operator')
"""

from __future__ import annotations

import getpass
import os
import re
from collections.abc import Sequence
from typing import TextIO

# ``normalize_service_for_write`` now lives in the manager module beside the
# lookup-time normalization it builds on, so the Python admin API can use it
# without importing CLI code.  Importing it here (and naming it in __all__)
# keeps existing ``from axlib.credentials.cli_common import
# normalize_service_for_write`` statements working.
from .manager import normalize_service_for_write

# __all__ declares this module's public names.  It is what ``from module
# import *`` exports, and it tells linters that the re-export above is
# intentional rather than an unused import.
__all__ = [
    "FIELD_PATTERN",
    "collect_values",
    "normalize_service_for_write",
    "parse_assignment",
    "parse_environment_assignment",
    "render_table",
    "validate_field_name",
]

FIELD_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_-]*$")


def render_table(
    headers: Sequence[str],
    rows: Sequence[Sequence[str]],
    *,
    stream: TextIO,
) -> None:
    """Print rows as left-aligned text columns separated by two spaces.

    Args:
        headers (Sequence[str]): Column titles, printed as the first line.
        rows (Sequence[Sequence[str]]): Table cells; every row must have the
            same number of cells as ``headers``.
        stream (TextIO): Destination such as :data:`sys.stdout`.

    Returns:
        None: The header and zero or more rows are written to ``stream``.

    Raises:
        ValueError: If a row has a different number of cells than ``headers``.
        OSError: If ``stream`` cannot be written.
    """
    for row in rows:
        if len(row) != len(headers):
            raise ValueError(
                f"Table row has {len(row)} cells but there are {len(headers)} headers."
            )
    # Each column is as wide as its longest cell, including the header.
    widths = [
        max(len(cell) for cell in (header, *(row[index] for row in rows)))
        for index, header in enumerate(headers)
    ]
    for line in (headers, *rows):
        cells = (cell.ljust(width) for cell, width in zip(line, widths, strict=True))
        # rstrip() avoids trailing spaces that would confuse `diff` or `wc -L`.
        print("  ".join(cells).rstrip(), file=stream)


def validate_field_name(field: str) -> str:
    """Validate a credential field before using it as a backend key.

    Args:
        field (str): Field such as ``netuser``, ``netpass``, or ``ibgrid``.

    Returns:
        str: Validated field unchanged.

    Raises:
        ValueError: If the field contains unsupported characters or does not
            start with a letter.
    """
    candidate = field.strip()
    if not FIELD_PATTERN.fullmatch(candidate):
        raise ValueError(
            f"Invalid field name {field!r}; use letters, digits, '_' or '-', "
            "starting with a letter."
        )
    return candidate


def parse_assignment(assignment: str) -> tuple[str, str]:
    """Parse one ``FIELD=VALUE`` command-line assignment.

    Args:
        assignment (str): Text supplied with ``--set``.

    Returns:
        tuple[str, str]: Validated field name and its exact value.

    Raises:
        ValueError: If the assignment lacks ``=`` or has an invalid field name.
    """
    if "=" not in assignment:
        raise ValueError(f"Invalid assignment {assignment!r}; expected FIELD=VALUE.")
    field, value = assignment.split("=", 1)
    return validate_field_name(field), value


def parse_environment_assignment(assignment: str) -> tuple[str, str]:
    """Read one credential value from a named environment variable.

    Args:
        assignment (str): ``FIELD=ENVIRONMENT_VARIABLE`` text supplied with
            ``--from-env``.

    Returns:
        tuple[str, str]: Validated field and exact environment value.

    Raises:
        ValueError: If syntax, field, variable name, or variable value is
            missing.
    """
    if "=" not in assignment:
        raise ValueError(
            f"Invalid environment assignment {assignment!r}; expected FIELD=ENV_VAR."
        )
    field, env_name = assignment.split("=", 1)
    field = validate_field_name(field)
    env_name = env_name.strip()
    if not env_name:
        raise ValueError("Environment variable name cannot be blank.")
    if env_name not in os.environ:
        raise ValueError(f"Environment variable {env_name!r} is not set.")
    return field, os.environ[env_name]


def collect_values(
    direct_assignments: Sequence[str],
    environment_assignments: Sequence[str],
    prompt_fields: Sequence[str],
) -> dict[str, str]:
    """Collect fields from CLI text, environment variables, and secure prompts.

    Later sources replace earlier values for the same field.  This lets an
    operator provide a general value with ``--set`` and safely override a secret
    with ``--prompt``.

    Args:
        direct_assignments (Sequence[str]): Repeated ``FIELD=VALUE`` entries.
        environment_assignments (Sequence[str]): Repeated ``FIELD=ENV_VAR``
            entries.
        prompt_fields (Sequence[str]): Fields read with terminal echo disabled.

    Returns:
        dict[str, str]: Field names and exact plaintext values ready for an
            encryption backend.

    Raises:
        ValueError: If an assignment is invalid or a prompted value is empty.
        EOFError: If a non-interactive input source cannot satisfy a prompt.
    """
    values: dict[str, str] = {}
    for assignment in direct_assignments:
        field, value = parse_assignment(assignment)
        values[field] = value
    for assignment in environment_assignments:
        field, value = parse_environment_assignment(assignment)
        values[field] = value
    for raw_field in prompt_fields:
        field = validate_field_name(raw_field)
        # getpass disables terminal echo, reducing accidental disclosure during
        # shared-screen troubleshooting or recorded terminal sessions.
        value = getpass.getpass(f"{field}: ")
        if not value:
            raise ValueError(f"Prompted value for {field!r} cannot be empty.")
        values[field] = value
    return values
