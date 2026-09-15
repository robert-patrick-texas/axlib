# Copyright 2026 Robert Patrick
# SPDX-License-Identifier: Apache-2.0

r"""Trim line edges and control blank lines in network-oriented text.

Configuration snippets copied from tickets, spreadsheets, or terminals often
contain indentation, trailing spaces, and runs of blank lines.  This module can
trim both edges or one selected edge, remove blank lines, or collapse them while
optionally preserving original LF/CRLF/CR endings.

The module has no third-party dependencies.

CLI example:
    ``python -m axlib.tf.rmline commands.txt --collapse-blank-lines``

Python example:
    >>> normalize_text('  show version  \n\n')
    'show version\n'
"""

from __future__ import annotations

import argparse
from collections.abc import Callable, Sequence

from ._cli import add_text_io_arguments, read_text, render_cli_error, write_text


def strip_line_both(line: str) -> str:
    """Remove leading and trailing whitespace from one line.

    Args:
        line (str): One command or configuration line without its line ending.

    Returns:
        str: Line with both edges stripped.

    Raises:
        None: :meth:`str.strip` is the only operation.
    """
    return line.strip()


def strip_line_leading(line: str) -> str:
    """Remove leading whitespace from one line.

    Args:
        line (str): One command or configuration line without its line ending.

    Returns:
        str: Line with left-side whitespace removed.

    Raises:
        None: :meth:`str.lstrip` is the only operation.
    """
    return line.lstrip()


def strip_line_trailing(line: str) -> str:
    """Remove trailing whitespace from one line.

    Args:
        line (str): One command or configuration line without its line ending.

    Returns:
        str: Line with right-side whitespace removed.

    Raises:
        None: :meth:`str.rstrip` is the only operation.
    """
    return line.rstrip()


def _split_line_ending(line: str) -> tuple[str, str]:
    """Separate line content from an LF, CRLF, or CR terminator.

    Args:
        line (str): Input line that may include a line ending.

    Returns:
        tuple[str, str]: Content and exact terminator.

    Raises:
        None: Suffix checks and slicing are deterministic.
    """
    if line.endswith("\r\n"):
        return line[:-2], "\r\n"
    if line.endswith("\n"):
        return line[:-1], "\n"
    if line.endswith("\r"):
        return line[:-1], "\r"
    return line, ""


def _stripper_for_mode(mode: str) -> Callable[[str], str]:
    """Select the line function associated with a public mode name.

    Args:
        mode (str): ``"both"``, ``"leading"``, or ``"trailing"``.

    Returns:
        Callable[[str], str]: Function that trims one line.

    Raises:
        ValueError: If ``mode`` is unsupported.
    """
    functions = {
        "both": strip_line_both,
        "leading": strip_line_leading,
        "trailing": strip_line_trailing,
    }
    try:
        return functions[mode]
    except KeyError as exc:
        raise ValueError("mode must be one of: 'both', 'leading', 'trailing'") from exc


def normalize_text(
    text: str,
    mode: str = "both",
    *,
    keep_original_blank_lines: bool = False,
    collapse_blank_lines: bool = False,
    preserve_line_endings: bool = False,
) -> str:
    """Trim each line and apply explicit blank-line handling.

    Args:
        text (str): Configuration, command list, or template text.
        mode (str): Edge selection: ``both``, ``leading``, or ``trailing``.
        keep_original_blank_lines (bool): In ``both`` mode, retain lines that
            were originally empty but remove lines containing only whitespace.
        collapse_blank_lines (bool): Keep at most one consecutive blank line.
        preserve_line_endings (bool): Preserve each original line ending instead
            of normalizing retained lines to LF.

    Returns:
        str: Normalized text.

    Raises:
        ValueError: If ``mode`` is unsupported.
    """
    if not text:
        return text
    strip_line = _stripper_for_mode(mode)
    lines = text.splitlines(keepends=preserve_line_endings)
    result_lines: list[str] = []
    previous_output_was_blank = False

    for raw_line in lines:
        content, ending = (
            _split_line_ending(raw_line) if preserve_line_endings else (raw_line, "")
        )
        processed = strip_line(content)
        original_blank = content == ""
        processed_blank = processed == ""
        keep_blank = False

        if processed_blank:
            keep_blank = (
                mode == "both" and keep_original_blank_lines and original_blank
            ) or collapse_blank_lines

        if processed_blank and not keep_blank:
            continue
        if processed_blank:
            if collapse_blank_lines and previous_output_was_blank:
                continue
            result_lines.append(ending if preserve_line_endings else "")
            previous_output_was_blank = True
            continue

        result_lines.append(processed + ending)
        previous_output_was_blank = False

    if preserve_line_endings:
        return "".join(result_lines)

    # splitlines() omits the final empty logical line.  Re-adding one LF keeps a
    # command file POSIX-friendly without reintroducing removed internal blanks.
    result = "\n".join(result_lines)
    if text.endswith(("\n", "\r")):
        result += "\n"
    return result


transform_text = normalize_text


def build_arg_parser() -> argparse.ArgumentParser:
    """Build the standardized line-normalization parser.

    Args:
        None: Parser configuration is defined by this module.

    Returns:
        argparse.ArgumentParser: Parser with mode, blank-line, and I/O options.

    Raises:
        argparse.ArgumentError: If an option conflicts with another definition.
    """
    parser = argparse.ArgumentParser(
        description="Trim line edges and control blank lines in text."
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--leading-only",
        action="store_true",
        help="Strip only leading whitespace.",
    )
    mode.add_argument(
        "--trailing-only",
        action="store_true",
        help="Strip only trailing whitespace.",
    )
    parser.add_argument(
        "--keep-original-blank-lines",
        action="store_true",
        help="Keep truly empty source lines in the default both-edge mode.",
    )
    parser.add_argument(
        "--collapse-blank-lines",
        action="store_true",
        help="Reduce each run of blank lines to one line.",
    )
    parser.add_argument(
        "--preserve-line-endings",
        action="store_true",
        help="Preserve LF, CRLF, and CR endings instead of normalizing to LF.",
    )
    add_text_io_arguments(parser)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run line normalization for a file or pipeline.

    Args:
        argv (Sequence[str] | None): Arguments excluding the program name.

    Returns:
        int: ``0`` on success or ``1`` for operational errors.

    Raises:
        None: Expected errors are rendered and converted to exit statuses.
    """
    args = build_arg_parser().parse_args(argv)
    selected_mode = (
        "leading" if args.leading_only else "trailing" if args.trailing_only else "both"
    )
    try:
        text = read_text(args.input, encoding=args.encoding)
        output = normalize_text(
            text,
            mode=selected_mode,
            keep_original_blank_lines=args.keep_original_blank_lines,
            collapse_blank_lines=args.collapse_blank_lines,
            preserve_line_endings=args.preserve_line_endings,
        )
        write_text(args.output, output, encoding=args.encoding)
    except (OSError, UnicodeError, LookupError, BrokenPipeError) as exc:
        return render_cli_error("rmline", exc)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
