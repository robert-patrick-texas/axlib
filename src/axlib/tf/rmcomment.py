#!/usr/bin/env python3

import argparse
import sys
from collections.abc import Iterable


def strip_comments(
    text: str,
    *,
    comment_chars: Iterable[str] = (";", "!", "#"),
    preserve_line_endings: bool = True,
) -> str:
    """
    Remove comments from text based on a set of comment characters.

    A character starts a comment if:
      - It is in `comment_chars`
      - It is not inside single or double quotes
      - It is not escaped with a backslash
      - It is preceded by whitespace (or start-of-line)

    Args:
        text (str):
            Input text. May be empty, short, single-line, or multi-line.

        comment_chars (Iterable[str]):
            One or more single-character comment markers to treat as comment
            starters. Typical examples are ';', '!', and '#'.

        preserve_line_endings (bool):
            If False, output line endings are normalized to '\\n'.
            If True, original line endings from the input are preserved exactly.

    Returns:
        str:
            Text with qualifying comments removed.
    """

    if not text:
        return text

    # Normalize the configured comment characters into a set for fast lookup.
    active_comment_chars: set[str] = set(comment_chars)

    # When preserving line endings, keep each line terminator attached to its
    # line. Otherwise, strip line terminators and rebuild the output using '\n'.
    # note - splitlines eats an ending blank line so we'll add it back later
    lines = text.splitlines(keepends=preserve_line_endings)
    result_lines = []

    for line in lines:
        # Separate the content portion from the line ending, but only when we
        # are preserving the original line endings.
        content = line
        line_ending = ""

        if preserve_line_endings:
            if content.endswith("\r\n"):
                content = content[:-2]
                line_ending = "\r\n"
            elif content.endswith("\n"):
                content = content[:-1]
                line_ending = "\n"
            elif content.endswith("\r"):
                content = content[:-1]
                line_ending = "\r"

        in_single = False
        in_double = False
        escaped = False
        output_chars = []

        for i, ch in enumerate(content):
            prev_char = content[i - 1] if i > 0 else None

            # If the previous character was a backslash, this character is
            # treated literally and cannot start or end a quoted region or
            # begin a comment.
            if escaped:
                output_chars.append(ch)
                escaped = False
                continue

            # A backslash escapes the next character.
            if ch == "\\":
                output_chars.append(ch)
                escaped = True
                continue

            # Toggle single-quote state only when not inside double quotes.
            if ch == "'" and not in_double:
                in_single = not in_single
                output_chars.append(ch)
                continue

            # Toggle double-quote state only when not inside single quotes.
            if ch == '"' and not in_single:
                in_double = not in_double
                output_chars.append(ch)
                continue

            # A comment begins only if:
            #   - the character is one of the configured comment characters
            #   - we are not inside quotes
            #   - the character is preceded by whitespace, or is first on line
            if (
                ch in active_comment_chars
                and not in_single
                and not in_double
                and (prev_char is None or prev_char.isspace())
            ):
                # Discard the remainder of the content portion of this line.
                break

            output_chars.append(ch)

        processed_line = "".join(output_chars)

        # Discard lines that were only comments or whitespace
        #        if processed_line.strip() == "":
        #            continue

        # Discard lines that were only comments, keeps whitespace without comments
        if processed_line.strip() == "" and content.strip() != "":
            continue

        if preserve_line_endings:
            processed_line += line_ending

        result_lines.append(processed_line)

    if preserve_line_endings:
        return "".join(result_lines)

    # accomodate ending blank line if it existed in original input
    # this is lost by the splintlines Python function, added back
    result = "\n".join(result_lines)
    if text.endswith(("\n", "\r")):
        result += "\n"
    return result


def build_arg_parser() -> argparse.ArgumentParser:
    """
    Build and return the command-line argument parser.
    """

    parser = argparse.ArgumentParser(
        description=(
            "Read text from stdin, remove comments based on configured comment "
            "characters, and write the result to stdout."
        )
    )

    # Allow one or more repeated flags such as:
    #   -c ';' -c '#'
    # If omitted, all supported comment characters are enabled by default.
    parser.add_argument(
        "-c",
        "--comment-char",
        action="append",
        choices=[";", "!", "#"],
        help=(
            "Comment character to enable. May be provided multiple times. "
            "Defaults to all supported characters: ; ! #"
        ),
    )

    parser.add_argument(
        "--preserve-line-endings",
        action="store_true",
        help=(
            "Preserve original input line endings exactly, including LF, CRLF, "
            "or CR. By default, output line endings are normalized to LF."
        ),
    )

    return parser


def main() -> int:
    """
    Read text from stdin, strip comments, and write the result to stdout.

    Returns:
        int: Process exit status code.
    """

    parser = build_arg_parser()
    args = parser.parse_args()

    # Default behavior enables all supported comment characters unless the
    # user explicitly narrows the active set with one or more -c flags.
    comment_chars = args.comment_char if args.comment_char else [";", "!", "#"]

    # Read raw bytes so that line ending preservation is truly exact.
    input_bytes = sys.stdin.buffer.read()

    # Decode using stdin's encoding when available. Fall back to UTF-8.
    encoding = sys.stdin.encoding or "utf-8"
    input_text = input_bytes.decode(encoding, errors="replace")

    output_text = strip_comments(
        text=input_text,
        comment_chars=comment_chars,
        preserve_line_endings=args.preserve_line_endings,
    )

    # Write text output. Use the binary buffer when preserving line endings so
    # Python does not alter them during text-mode output.
    if args.preserve_line_endings:
        output_bytes = output_text.encode(encoding, errors="replace")
        sys.stdout.buffer.write(output_bytes)
    else:
        sys.stdout.write(output_text)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
