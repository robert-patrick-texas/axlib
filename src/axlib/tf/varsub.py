# Copyright 2026 Robert Patrick
# SPDX-License-Identifier: Apache-2.0

"""Replace case-insensitive ``<var>name</var>`` template tokens.

Simple network templates often need only a few substitutions such as hostname,
management IP, or site code.  This module performs those replacements without a
full templating engine.  Variable names are matched case-insensitively, while
replacement values retain their exact case and whitespace.

The module has no third-party dependencies.  Values provided directly on a
command line may be visible in shell history, so secrets should not be passed to
this text utility.

CLI example:
    ``echo '<var>hostname</var>' | python -m axlib.tf.varsub -v hostname=edge-01``

Python example:
    >>> substitute_variables("Device <VAR>Name</VAR>", {"name": "Core-SW1"})
    'Device Core-SW1'
"""

from __future__ import annotations

import argparse
import re
from collections.abc import Iterable, Mapping, Sequence
from functools import partial

from ._cli import add_text_io_arguments, read_text, render_cli_error, write_text

VAR_PATTERN = re.compile(r"<var>(.*?)</var>", re.IGNORECASE | re.DOTALL)


def normalize_variable_map(
    variables: Mapping[str, str] | None,
) -> dict[str, str]:
    """Create a lowercase-key lookup mapping while preserving values.

    Args:
        variables (Mapping[str, str] | None): Variable names and replacement
            text, such as ``hostname`` and a device hostname.

    Returns:
        dict[str, str]: Case-insensitive lookup dictionary.

    Raises:
        None: Keys and values are converted to strings deterministically.
    """
    if not variables:
        return {}
    return {str(key).casefold(): str(value) for key, value in variables.items()}


def _replace_match(
    match: re.Match[str],
    *,
    variables: Mapping[str, str],
) -> str:
    """Replace one variable tag when its name exists in the mapping.

    Args:
        match (re.Match[str]): Token matched by :data:`VAR_PATTERN`.
        variables (Mapping[str, str]): Lowercase variable lookup mapping.

    Returns:
        str: Replacement value, or the original tag when no variable exists.

    Raises:
        None: Match group access is guaranteed by the compiled pattern.
    """
    key = match.group(1).casefold()
    return variables.get(key, match.group(0))


def substitute_variables(
    text: str,
    variables: Mapping[str, str] | None = None,
) -> str:
    """Replace matching variable tags in template text.

    Args:
        text (str): Network template or command text.
        variables (Mapping[str, str] | None): Names and replacement values.

    Returns:
        str: Substituted text; unchanged input when no variables are supplied.

    Raises:
        None: Regular expression substitution uses a fixed compiled pattern.
    """
    normalized = normalize_variable_map(variables)
    if not normalized:
        return text

    # functools.partial supplies the variable mapping without defining an opaque
    # lambda, making the callback easier for new Python readers to inspect.
    replacer = partial(_replace_match, variables=normalized)
    return VAR_PATTERN.sub(replacer, text)


transform_text = substitute_variables


def parse_variable_assignments(
    assignments: Iterable[str] | None,
) -> dict[str, str]:
    """Parse repeated ``NAME=VALUE`` command-line assignments.

    Args:
        assignments (Iterable[str] | None): Raw values supplied with ``-v`` or
            ``--var``.

    Returns:
        dict[str, str]: Parsed names and exact replacement values.

    Raises:
        ValueError: If an item lacks ``=`` or has a blank variable name.
    """
    result: dict[str, str] = {}
    for item in assignments or ():
        if "=" not in item:
            raise ValueError(
                f"Invalid variable definition {item!r}; expected NAME=VALUE."
            )
        name, value = item.split("=", 1)
        name = name.strip()
        if not name:
            raise ValueError(
                f"Invalid variable definition {item!r}; name cannot be empty."
            )
        result[name] = value
    return result


def build_arg_parser() -> argparse.ArgumentParser:
    """Build the standardized variable-substitution parser.

    Args:
        None: Parser configuration is defined by this module.

    Returns:
        argparse.ArgumentParser: Parser with variable and common I/O options.

    Raises:
        argparse.ArgumentError: If an option conflicts with another definition.
    """
    parser = argparse.ArgumentParser(
        description=(
            "Replace <var>name</var> tags using repeated -v/--var NAME=VALUE "
            "assignments."
        )
    )
    parser.add_argument(
        "-v",
        "--var",
        action="append",
        dest="variables",
        default=[],
        metavar="NAME=VALUE",
        help="Define a replacement variable. May be repeated.",
    )
    add_text_io_arguments(parser)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run variable substitution for a file or pipeline.

    Args:
        argv (Sequence[str] | None): Arguments excluding the program name.

    Returns:
        int: ``0`` on success, ``1`` for operational errors, or ``2`` for an
            invalid variable assignment.

    Raises:
        None: Expected errors are rendered and converted to exit statuses.
    """
    parser = build_arg_parser()
    args = parser.parse_args(argv)
    try:
        variables = parse_variable_assignments(args.variables)
    except ValueError as exc:
        parser.error(str(exc))

    try:
        text = read_text(args.input, encoding=args.encoding)
        output = substitute_variables(text, variables)
        write_text(args.output, output, encoding=args.encoding)
    except (OSError, UnicodeError, LookupError, BrokenPipeError) as exc:
        return render_cli_error("varsub", exc)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
