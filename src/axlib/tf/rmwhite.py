r"""Collapse tabs and repeated spaces outside straight quoted regions.

Network configuration templates often need predictable spacing for comparison or
substitution, while command descriptions and banners inside quotes must remain
unchanged.  This filter works one line at a time, protects balanced or unclosed
single/double quoted regions until line end, and preserves original line endings.

The module has no third-party dependencies.

CLI example:
    ``python -m axlib.tf.rmwhite raw.conf -o normalized.conf``

Python example:
    >>> normalize_spaces_outside_quotes('a\t  b "keep   this"\n')
    'a b "keep   this"\n'
"""

from __future__ import annotations

import argparse
from collections.abc import Sequence

from ._cli import add_text_io_arguments, read_text, render_cli_error, write_text


def normalize_line_outside_quotes(line: str) -> str:
    """Normalize tabs and repeated spaces outside quotes on one line.

    Args:
        line (str): One line of network text, optionally including its line
            ending.

    Returns:
        str: Line with external tabs converted to spaces and repeated external
            spaces collapsed.

    Raises:
        None: The state machine scans characters without external I/O.
    """
    result: list[str] = []
    in_quote = False
    quote_char = ""
    previous_was_space = False
    escaped = False

    for char in line:
        if in_quote:
            result.append(char)
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote_char:
                in_quote = False
                quote_char = ""
            continue

        if char in {"'", '"'}:
            in_quote = True
            quote_char = char
            previous_was_space = False
            result.append(char)
            continue

        candidate = " " if char == "\t" else char
        if candidate == " ":
            if not previous_was_space:
                result.append(candidate)
                previous_was_space = True
            continue

        previous_was_space = False
        result.append(candidate)
    return "".join(result)


def normalize_spaces_outside_quotes(text: str) -> str:
    """Normalize whitespace outside quotes across a block of text.

    Args:
        text (str): Device configuration, command output, or template text.

    Returns:
        str: Normalized text with original line endings preserved.

    Raises:
        None: The function delegates each line to a pure string transformation.
    """
    # Processing separate lines prevents an unmatched quote in one configuration
    # line from protecting all later lines by accident.
    return "".join(
        normalize_line_outside_quotes(line)
        for line in text.splitlines(keepends=True)
    )


transform_text = normalize_spaces_outside_quotes


def build_arg_parser() -> argparse.ArgumentParser:
    """Build the standardized parser for whitespace normalization.

    Args:
        None: Parser configuration is defined by this module.

    Returns:
        argparse.ArgumentParser: Parser with common text I/O options.

    Raises:
        argparse.ArgumentError: If common options conflict with local options.
    """
    parser = argparse.ArgumentParser(
        description="Collapse tabs and repeated spaces outside quoted regions."
    )
    add_text_io_arguments(parser)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run whitespace normalization for a file or pipeline.

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
        output = normalize_spaces_outside_quotes(text)
        write_text(args.output, output, encoding=args.encoding)
    except (OSError, UnicodeError, LookupError, BrokenPipeError) as exc:
        return render_cli_error("rmwhite", exc)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
