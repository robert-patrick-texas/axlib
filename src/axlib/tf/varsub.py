#!/usr/bin/env python3
"""
Variable substitution utility.

Behavior:
- Can be used as a module or run directly from the command line.
- When run from the command line, reads input text from stdin.
- Replaces any token in the form <var>name</var> with the value associated
  with "name".
- Tag matching is case-insensitive:
    <var>name</var>
    <VAR>name</VAR>
    <VaR>name</VaR>
  are all treated the same.
- Variable name matching is also case-insensitive.
- Replacement values preserve their original provided case exactly.
- If no variables are provided, input is returned unchanged.

CLI variable format:
    -v name=value
    --var name=value

Examples:
    echo 'Hello <var>Name</var>' | python3 var_substitute.py -v name=Robert
    -> Hello Robert

Module example:
    from var_substitute import substitute_variables

    text = "Device: <VAR>hostname</VAR>"
    result = substitute_variables(text, {"HOSTNAME": "Core-SW1"})
"""

import argparse
import re
import sys
from collections.abc import Iterable, Mapping

VAR_PATTERN = re.compile(r"<var>(.*?)</var>", re.IGNORECASE | re.DOTALL)


def normalize_variable_map(variables: Mapping[str, str] | None) -> dict[str, str]:
    """
    Return a case-insensitive variable mapping.

    Keys are normalized to lowercase for lookup.
    Values are preserved exactly as provided.
    """
    if not variables:
        return {}

    normalized: dict[str, str] = {}
    for key, value in variables.items():
        normalized[str(key).lower()] = str(value)
    return normalized


def substitute_variables(text: str, variables: Mapping[str, str] | None = None) -> str:
    """
    Replace <var>name</var> occurrences in text with values from variables.

    Matching rules:
    - <var> and </var> are matched case-insensitively
    - variable names are matched case-insensitively
    - replacement values are inserted exactly as provided

    If variables is empty or None, the input text is returned unchanged.
    """
    if not variables:
        return text

    normalized_vars = normalize_variable_map(variables)

    def replacer(match: re.Match[str]) -> str:
        var_name = match.group(1)
        lookup_key = var_name.lower()
        if lookup_key in normalized_vars:
            return normalized_vars[lookup_key]
        return match.group(0)

    return VAR_PATTERN.sub(replacer, text)


def parse_variable_assignments(assignments: Iterable[str] | None) -> dict[str, str]:
    """
    Parse CLI variable assignments in the form name=value.

    Raises ValueError for invalid entries.
    """
    result: dict[str, str] = {}

    if not assignments:
        return result

    for item in assignments:
        if "=" not in item:
            msg = f"Invalid var definition '{item}'. Expected format: name=value"
            raise ValueError(msg)

        name, value = item.split("=", 1)
        name = name.strip()

        if not name:
            msg = f"Invalid var definition '{item}'. Var name cannot be empty."
            raise ValueError(msg)

        result[name] = value

    return result


def build_arg_parser() -> argparse.ArgumentParser:
    """
    Build and return the command-line argument parser.
    """
    parser = argparse.ArgumentParser(
        description=(
            "Read text from stdin and replace <var>name</var> tokens using "
            "variables supplied with -v/--var name=value."
        )
    )
    parser.add_argument(
        "-v",
        "--var",
        action="append",
        dest="variables",
        default=[],
        metavar="NAME=VALUE",
        help="Define a variable for substitution. May be provided multiple times.",
    )
    return parser


def main(argv: Iterable[str] | None = None) -> int:
    """
    Command-line entry point.

    Reads stdin, performs substitution, writes to stdout.
    """
    parser = build_arg_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)

    try:
        variables = parse_variable_assignments(args.variables)
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2

    input_text = sys.stdin.read()
    output_text = substitute_variables(input_text, variables)
    sys.stdout.write(output_text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
