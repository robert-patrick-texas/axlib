r"""Remove whitespace-delimited comments while respecting quoted text.

Network configuration snippets commonly use ``!``, ``#``, or ``;`` for
comments.  This filter treats a marker as a comment only at the beginning of a
line or after whitespace, outside single/double quotes, and when it is not
backslash-escaped.  That guardrail prevents values such as interface
``description "WAN #1"`` from being truncated.

The module has no third-party dependencies.

CLI example:
    ``python -m axlib.tf.rmcomment router.conf -o router.no-comments.conf``

Python example:
    >>> strip_comments('hostname r1  # inventory name\n')
    'hostname r1  \n'
"""

from __future__ import annotations

import argparse
from collections.abc import Iterable, Sequence

from ._cli import add_text_io_arguments, read_text, render_cli_error, write_text

DEFAULT_COMMENT_CHARS = (";", "!", "#")


def _split_line_ending(line: str) -> tuple[str, str]:
    """Separate text content from one LF, CRLF, or CR ending.

    Args:
        line (str): Input line that may include a terminator.

    Returns:
        tuple[str, str]: Content and its exact line-ending sequence.

    Raises:
        None: Suffix checks and string slicing are deterministic.
    """
    if line.endswith("\r\n"):
        return line[:-2], "\r\n"
    if line.endswith("\n"):
        return line[:-1], "\n"
    if line.endswith("\r"):
        return line[:-1], "\r"
    return line, ""


def _validate_comment_chars(comment_chars: Iterable[str]) -> set[str]:
    """Validate and deduplicate single-character comment markers.

    Args:
        comment_chars (Iterable[str]): Candidate marker characters.

    Returns:
        set[str]: Unique markers used for constant-time membership checks.

    Raises:
        ValueError: If any marker is not exactly one character.
    """
    markers = set(comment_chars)
    invalid = [marker for marker in markers if len(marker) != 1]
    if invalid:
        raise ValueError(
            "Comment markers must be single characters: " + ", ".join(invalid)
        )
    return markers


def strip_comments(
    text: str,
    *,
    comment_chars: Iterable[str] = DEFAULT_COMMENT_CHARS,
    preserve_line_endings: bool = True,
) -> str:
    """Remove qualifying comments from network-oriented text.

    Args:
        text (str): Configuration, command output, or template text.
        comment_chars (Iterable[str]): Single-character markers that may start a
            comment.
        preserve_line_endings (bool): Preserve each original line ending when
            ``True``; normalize retained lines to LF when ``False``.

    Returns:
        str: Text with comments removed.  Lines containing only a comment are
            removed, while truly blank lines are retained.

    Raises:
        ValueError: If a configured comment marker has more or fewer than one
            character.
    """
    if not text:
        return text

    active_markers = _validate_comment_chars(comment_chars)
    # keepends=True is required when the caller asks for byte-stable line-ending
    # behavior; otherwise the output is intentionally rebuilt with LF.
    lines = text.splitlines(keepends=preserve_line_endings)
    result_lines: list[str] = []

    for line in lines:
        content, line_ending = (
            _split_line_ending(line) if preserve_line_endings else (line, "")
        )
        in_single = False
        in_double = False
        escaped = False
        output_chars: list[str] = []

        for index, char in enumerate(content):
            previous = content[index - 1] if index > 0 else None
            if escaped:
                output_chars.append(char)
                escaped = False
                continue
            if char == "\\":
                output_chars.append(char)
                escaped = True
                continue
            if char == "'" and not in_double:
                in_single = not in_single
                output_chars.append(char)
                continue
            if char == '"' and not in_single:
                in_double = not in_double
                output_chars.append(char)
                continue
            if (
                char in active_markers
                and not in_single
                and not in_double
                and (previous is None or previous.isspace())
            ):
                # Discarding the remainder avoids sending annotations as device
                # commands while preserving markers embedded in real values.
                break
            output_chars.append(char)

        processed = "".join(output_chars)
        if processed.strip() == "" and content.strip() != "":
            continue
        result_lines.append(processed + line_ending)

    if preserve_line_endings:
        return "".join(result_lines)

    result = "\n".join(result_lines)
    if text.endswith(("\n", "\r")):
        result += "\n"
    return result


transform_text = strip_comments


def build_arg_parser() -> argparse.ArgumentParser:
    """Build the standardized comment-filter command-line parser.

    Args:
        None: Parser configuration is defined by this module.

    Returns:
        argparse.ArgumentParser: Parser with marker and common text I/O options.

    Raises:
        argparse.ArgumentError: If an option definition conflicts.
    """
    parser = argparse.ArgumentParser(
        description=(
            "Remove whitespace-delimited comments outside quoted text. "
            "Default markers are ;, !, and #."
        )
    )
    parser.add_argument(
        "-c",
        "--comment-char",
        action="append",
        dest="comment_chars",
        metavar="CHAR",
        help="Enable one single-character marker. May be repeated.",
    )
    parser.add_argument(
        "--preserve-line-endings",
        action="store_true",
        help="Preserve LF, CRLF, and CR endings instead of normalizing to LF.",
    )
    add_text_io_arguments(parser)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run comment removal for a file or pipeline.

    Args:
        argv (Sequence[str] | None): Arguments excluding the program name.

    Returns:
        int: ``0`` on success, ``1`` for operational errors, or ``2`` for an
            invalid comment marker.

    Raises:
        None: Expected errors are rendered and converted to exit statuses.
    """
    parser = build_arg_parser()
    args = parser.parse_args(argv)
    markers = args.comment_chars or list(DEFAULT_COMMENT_CHARS)
    try:
        text = read_text(args.input, encoding=args.encoding)
        output = strip_comments(
            text,
            comment_chars=markers,
            preserve_line_endings=args.preserve_line_endings,
        )
        write_text(args.output, output, encoding=args.encoding)
    except ValueError as exc:
        parser.error(str(exc))
    except (OSError, UnicodeError, LookupError, BrokenPipeError) as exc:
        return render_cli_error("rmcomment", exc)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
