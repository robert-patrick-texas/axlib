# Copyright 2026 Robert Patrick
# SPDX-License-Identifier: Apache-2.0

r"""Normalize whitespace with cautious, line-scoped quote detection.

This module is the conservative companion to :mod:`axlib.tf.rmwhite`.  It
protects straight double quotes, curly single quotes, and straight single quotes
only when a matching close quote exists on the same line.  The straight-single-
quote heuristic avoids treating apostrophes inside words as the start of a
quoted network description.

The module has no third-party dependencies.

CLI example:
    ``python -m axlib.tf.rmwhite2 inventory.txt -o inventory.clean.txt``

Python example:
    >>> normalize("port\t  description='WAN   Link'\n")
    "port description='WAN   Link'\n"
"""

from __future__ import annotations

import argparse
import re
from collections.abc import Sequence

from ._cli import add_text_io_arguments, read_text, render_cli_error, write_text

QUOTE_PAIRS = (('"', '"'), ("\u2018", "\u2019"))
STRAIGHT_SINGLE = "'"


def _find_close_on_line(text: str, start: int, close_quote: str) -> int:
    """Find an unescaped closing quote before the next newline.

    Args:
        text (str): Complete input text.
        start (int): Character index where scanning begins.
        close_quote (str): Expected one-character closing delimiter.

    Returns:
        int: Index of the close quote, or ``-1`` when none occurs on the line.

    Raises:
        None: Bounds checks keep all character access safe.
    """
    index = start
    while index < len(text):
        char = text[index]
        if char == "\n":
            return -1
        if char == "\\" and index + 1 < len(text) and text[index + 1] != "\n":
            index += 2
            continue
        if char == close_quote:
            return index
        index += 1
    return -1


def _is_straight_single_opener(text: str, position: int) -> bool:
    """Decide whether a straight quote is likely a delimiter, not an apostrophe.

    Args:
        text (str): Complete input text.
        position (int): Index of the candidate single quote.

    Returns:
        bool: ``True`` at line/string start or after whitespace or ``=``.

    Raises:
        IndexError: If ``position`` is outside ``text``; internal callers pass a
            validated index.
    """
    if text[position] != STRAIGHT_SINGLE:
        return False
    if position == 0:
        return True
    return text[position - 1] in {" ", "\t", "\n", "="}


def _match_balanced_opener(text: str, position: int) -> tuple[str, str, int] | None:
    """Identify a supported quote opener with a close on the same line.

    Args:
        text (str): Complete input text.
        position (int): Candidate opener index.

    Returns:
        tuple[str, str, int] | None: Opening delimiter, closing delimiter, and
            close index, or ``None`` when the character is unprotected text.

    Raises:
        IndexError: If ``position`` is outside ``text``; internal callers keep it
            within bounds.
    """
    for open_quote, close_quote in QUOTE_PAIRS:
        if text[position] == open_quote:
            close_position = _find_close_on_line(text, position + 1, close_quote)
            return (
                (open_quote, close_quote, close_position)
                if close_position != -1
                else None
            )

    if _is_straight_single_opener(text, position):
        close_position = _find_close_on_line(
            text,
            position + 1,
            STRAIGHT_SINGLE,
        )
        if close_position != -1:
            return STRAIGHT_SINGLE, STRAIGHT_SINGLE, close_position
    return None


def normalize(text: str) -> str:
    """Collapse tabs and repeated spaces outside balanced quoted regions.

    Args:
        text (str): Device configuration, inventory, or template text.

    Returns:
        str: Normalized text with protected quoted text unchanged.

    Raises:
        None: The transformation uses bounded character scans and regular
            expression substitution only.
    """
    result: list[str] = []
    index = 0

    while index < len(text):
        matched = _match_balanced_opener(text, index)
        if matched is not None:
            _, _, close_position = matched
            end = close_position + 1
            result.append(text[index:end])
            index = end
            continue

        chunk_start = index
        while index < len(text) and _match_balanced_opener(text, index) is None:
            index += 1
        chunk = text[chunk_start:index].replace("\t", " ")
        # A regular expression communicates the intent directly: collapse only
        # repeated literal spaces, leaving line endings and other whitespace
        # untouched for configuration diff stability.
        result.append(re.sub(r" {2,}", " ", chunk))

    return "".join(result)


normalize_whitespace_safely = normalize
transform_text = normalize


def build_arg_parser() -> argparse.ArgumentParser:
    """Build the standardized cautious-whitespace parser.

    Args:
        None: Parser configuration is defined by this module.

    Returns:
        argparse.ArgumentParser: Parser with common text I/O options.

    Raises:
        argparse.ArgumentError: If an option conflicts with another definition.
    """
    parser = argparse.ArgumentParser(
        description=(
            "Collapse whitespace outside line-balanced straight or curly quoted "
            "regions."
        )
    )
    add_text_io_arguments(parser)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run cautious whitespace normalization for a file or pipeline.

    Args:
        argv (Sequence[str] | None): Arguments excluding the program name.

    Returns:
        int: ``0`` on success or ``1`` for operational errors.

    Raises:
        None: Expected errors are rendered and converted to exit statuses.
    """
    args = build_arg_parser().parse_args(argv)
    try:
        text = read_text(args.input, encoding=args.encoding)
        write_text(args.output, normalize(text), encoding=args.encoding)
    except (OSError, UnicodeError, LookupError, BrokenPipeError) as exc:
        return render_cli_error("rmwhite2", exc)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
