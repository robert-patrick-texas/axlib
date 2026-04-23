#!/usr/bin/env python3
"""
strip_double_slash_comments.py

Purpose:
    Remove lines where the first non-whitespace characters are "//".

Capabilities:
    - Can be imported as a module and used via function call
    - Can be executed directly from the command line
    - Reads input from stdin
    - Writes cleaned output to stdout

Definition of removal:
    A line is removed if, after stripping leading whitespace,
    it begins with "//"
"""

import sys
from collections.abc import Iterable


def remove_double_slash(text: str) -> str:
    """
    Remove lines where the first non-whitespace characters are "//".

    Args:
        text: Input string (may contain multiple lines)

    Returns:
        Filtered string with matching lines removed
    """
    output_lines = []

    # Preserve original line endings by keeping splitlines(keepends=True)
    for line in text.splitlines(keepends=True):
        stripped = line.lstrip()

        # Skip lines where first non-whitespace chars are "//"
        if stripped.startswith("//"):
            continue

        output_lines.append(line)

    return "".join(output_lines)


def main(argv: Iterable[str] | None = None) -> int:
    """
    CLI entry point.

    Reads from stdin and writes to stdout.
    """
    # Read entire stdin
    input_text = sys.stdin.read()

    # Process text
    output_text = remove_double_slash(input_text)

    # Write result
    sys.stdout.write(output_text)

    return 0


if __name__ == "__main__":
    sys.exit(main())
