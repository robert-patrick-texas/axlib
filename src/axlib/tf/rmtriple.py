r"""Remove triple-quoted string spans from general text.

Network engineers sometimes receive Python-like templates that use triple-quoted
blocks as informal notes.  This filter removes both single- and double-triple-
quoted spans, including spans that cross lines.  It is a text filter, not a
Python parser: it will also remove a legitimate assigned multiline string.
Use :mod:`ast` or :mod:`tokenize` instead when only Python docstrings should be
removed.

The module has no third-party dependencies.

CLI example:
    ``python -m axlib.tf.rmtriple generated.py -o generated.no-notes.py``

Python example:
    >>> remove_triple_quoted("before '''note''' after\n")
    'before  after\n'
"""

from __future__ import annotations

import argparse
import re
from collections.abc import Sequence

from ._cli import add_text_io_arguments, read_text, render_cli_error, write_text

TRIPLE_QUOTE_PATTERN = re.compile(r"(\'\'\'|\"\"\")((?:[^\\]|\\.)*?)\1", re.DOTALL)


def _flatten_match(match: re.Match[str]) -> str:
    """Replace newlines inside one matched block before per-line cleanup.

    Args:
        match (re.Match[str]): Triple-quoted span found by the compiled pattern.

    Returns:
        str: Same matched text with embedded ``\n`` characters changed to spaces.

    Raises:
        None: The replacement is a direct string operation.
    """
    return match.group(0).replace("\n", " ")


def remove_triple_quoted(text: str) -> str:
    """Remove all balanced triple-quoted spans from text.

    Args:
        text (str): Template or source-like text to filter.

    Returns:
        str: Text with triple-quoted spans removed; lines that become entirely
            blank are dropped.

    Raises:
        None: The regular expression is precompiled and string operations are
            deterministic.
    """
    # Flattening a multiline match lets the original line-oriented behavior drop
    # a now-empty note line while preserving surrounding content on that line.
    flattened = TRIPLE_QUOTE_PATTERN.sub(_flatten_match, text)
    output_lines: list[str] = []
    for line in flattened.splitlines(keepends=True):
        if '"""' in line or "'''" in line:
            cleaned = TRIPLE_QUOTE_PATTERN.sub("", line)
            if cleaned.strip():
                output_lines.append(cleaned)
            continue
        output_lines.append(line)
    return "".join(output_lines)


transform_text = remove_triple_quoted


def build_arg_parser() -> argparse.ArgumentParser:
    """Build the standardized parser for triple-quote removal.

    Args:
        None: Parser configuration is defined by this module.

    Returns:
        argparse.ArgumentParser: Parser with common text I/O options.

    Raises:
        argparse.ArgumentError: If a shared option conflicts with another option.
    """
    parser = argparse.ArgumentParser(
        description=(
            "Remove all balanced triple-quoted spans. This is a text operation, "
            "not Python-aware docstring parsing."
        )
    )
    add_text_io_arguments(parser)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run triple-quote removal for a file or pipeline.

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
        write_text(args.output, remove_triple_quoted(text), encoding=args.encoding)
    except (OSError, UnicodeError, LookupError, BrokenPipeError) as exc:
        return render_cli_error("rmtriple", exc)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
