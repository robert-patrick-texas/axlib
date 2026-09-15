# Copyright 2026 Robert Patrick
# SPDX-License-Identifier: Apache-2.0

r"""Remove full lines whose first non-whitespace characters are ``//``.

Some network template formats and inventory exports use C-style ``//`` lines
for operator notes.  This filter removes only those full comment lines; inline
``//`` text and URLs remain unchanged.  Original line endings are preserved.

The module has no third-party dependencies.

CLI example:
    ``python -m axlib.tf.rmdouble template.txt -o template.clean.txt``

Python example:
    >>> remove_double_slash("hostname r1\n  // lab note\ninterface Gi0/1\n")
    'hostname r1\ninterface Gi0/1\n'
"""

from __future__ import annotations

import argparse
from collections.abc import Sequence

from ._cli import add_text_io_arguments, read_text, render_cli_error, write_text


def remove_double_slash(text: str) -> str:
    """Remove lines that begin with ``//`` after optional indentation.

    Args:
        text (str): Device configuration, template, or inventory text.

    Returns:
        str: Text with matching full comment lines removed and all remaining line
            endings preserved.

    Raises:
        None: String splitting and joining are the only operations performed.
    """
    output_lines: list[str] = []

    # keepends=True retains the exact newline style used by exported device
    # configurations, which avoids noisy diffs caused only by line endings.
    for line in text.splitlines(keepends=True):
        if line.lstrip().startswith("//"):
            continue
        output_lines.append(line)
    return "".join(output_lines)


transform_text = remove_double_slash


def build_arg_parser() -> argparse.ArgumentParser:
    """Build the standardized command-line parser for this filter.

    Args:
        None: Parser configuration is defined by this module.

    Returns:
        argparse.ArgumentParser: Parser with common text I/O options.

    Raises:
        argparse.ArgumentError: If shared options conflict with local options.
    """
    parser = argparse.ArgumentParser(
        description="Remove lines whose first non-whitespace characters are //.",
    )
    add_text_io_arguments(parser)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run the double-slash line filter from the command line.

    Args:
        argv (Sequence[str] | None): Arguments excluding the program name.

    Returns:
        int: ``0`` on success or ``1`` for input/output/encoding errors.

    Raises:
        None: Expected operational errors are converted to exit statuses.
    """
    args = build_arg_parser().parse_args(argv)
    try:
        text = read_text(args.input, encoding=args.encoding)
        write_text(args.output, remove_double_slash(text), encoding=args.encoding)
    except (OSError, UnicodeError, LookupError, BrokenPipeError) as exc:
        return render_cli_error("rmdouble", exc)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
