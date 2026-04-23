#!/usr/bin/env python3

import argparse
import sys


def strip_line_both(line: str) -> str:
    """
    Strip both leading and trailing whitespace from a single line.
    """
    return line.strip()


def strip_line_leading(line: str) -> str:
    """
    Strip leading whitespace from a single line.
    """
    return line.lstrip()


def strip_line_trailing(line: str) -> str:
    """
    Strip trailing whitespace from a single line.
    """
    return line.rstrip()


def _split_line_ending(line: str) -> tuple[str, str]:
    """
    Split a line into (content, line_ending).

    Supports LF, CRLF, and CR.
    If no trailing line ending is present, line_ending is "".
    """
    if line.endswith("\r\n"):
        return line[:-2], "\r\n"
    if line.endswith("\n"):
        return line[:-1], "\n"
    if line.endswith("\r"):
        return line[:-1], "\r"
    return line, ""


def normalize_text(
    text: str,
    mode: str = "both",
    *,
    keep_original_blank_lines: bool = False,
    collapse_blank_lines: bool = False,
    preserve_line_endings: bool = False,
) -> str:
    """
    Normalize line whitespace in a block of text.

    Default behavior:
      - Strip leading and trailing whitespace from each line
      - Remove lines that become empty after stripping
      - Remove all blank lines

    Optional behavior:
      - mode='leading'  -> strip only leading whitespace
      - mode='trailing' -> strip only trailing whitespace
      - keep_original_blank_lines=True
            In 'both' mode, preserve lines that were originally truly blank
            (empty lines with no whitespace), while still removing lines that
            become blank because they contained only whitespace.
      - collapse_blank_lines=True
            Keep at most one consecutive blank line

    Args:
        text:
            Input text. May be empty, short, single-line, or multi-line.

        mode:
            One of: 'both', 'leading', 'trailing'

        keep_original_blank_lines:
            Only meaningful for default 'both' mode. When enabled, lines that
            were already exactly blank are preserved, while whitespace-only
            lines are still removed after stripping.

        collapse_blank_lines:
            If True, consecutive blank lines are reduced to a single blank line.

        preserve_line_endings:
            If True, original input line endings are preserved exactly.
            If False, output line endings are normalized to '\\n'.

    Returns:
        The normalized text.
    """
    if not text:
        return text

    if mode not in {"both", "leading", "trailing"}:
        msg = "mode must be one of: 'both', 'leading', 'trailing'"
        raise ValueError(msg)

    if mode == "leading":
        strip_func = strip_line_leading
    elif mode == "trailing":
        strip_func = strip_line_trailing
    else:
        strip_func = strip_line_both

    # note - splitlines eats an ending blank line so we'll add it back later
    lines = text.splitlines(keepends=preserve_line_endings)
    result_lines: list[str] = []
    previous_output_was_blank = False

    for raw_line in lines:
        if preserve_line_endings:
            original_content, line_ending = _split_line_ending(raw_line)
        else:
            original_content = raw_line
            line_ending = ""

        processed_content = strip_func(original_content)

        original_was_truly_blank = original_content == ""
        processed_is_blank = processed_content == ""

        keep_blank = False

        if processed_is_blank:
            if (
                mode == "both"
                and keep_original_blank_lines
                and original_was_truly_blank
            ):
                keep_blank = True
            elif collapse_blank_lines:
                # For collapse mode, preserve a single blank line regardless of
                # whether it came from an originally blank line or a line that
                # became blank after stripping.
                keep_blank = True
            else:
                keep_blank = False

        if processed_is_blank and not keep_blank:
            continue

        if processed_is_blank and keep_blank:
            if collapse_blank_lines and previous_output_was_blank:
                continue

            output_line = line_ending if preserve_line_endings else ""
            result_lines.append(output_line)
            previous_output_was_blank = True
            continue

        output_line = processed_content + line_ending
        result_lines.append(output_line)
        previous_output_was_blank = False

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
            "Read text from stdin, normalize leading/trailing whitespace on "
            "each line, and write the result to stdout."
        )
    )

    mode_group = parser.add_mutually_exclusive_group()
    mode_group.add_argument(
        "--leading-only",
        action="store_true",
        help=(
            "Strip only leading whitespace from each line, then remove blank "
            "lines unless blank-line preservation options are enabled."
        ),
    )
    mode_group.add_argument(
        "--trailing-only",
        action="store_true",
        help=(
            "Strip only trailing whitespace from each line, then remove blank "
            "lines unless blank-line preservation options are enabled."
        ),
    )

    parser.add_argument(
        "--keep-original-blank-lines",
        action="store_true",
        help=(
            "In default mode, preserve lines that were already truly blank "
            "(empty lines with no whitespace). Lines containing only whitespace "
            "are still removed after stripping."
        ),
    )

    parser.add_argument(
        "--collapse-blank-lines",
        action="store_true",
        help=("Keep at most one blank line in any run of consecutive blank lines."),
    )

    parser.add_argument(
        "--preserve-line-endings",
        action="store_true",
        help=(
            "Preserve original line endings exactly (LF, CRLF, or CR). "
            "By default, output line endings are normalized to LF."
        ),
    )

    return parser


def main() -> int:
    """
    Read text from stdin, normalize whitespace, and write the result to stdout.

    Returns:
        Process exit status code.
    """
    parser = build_arg_parser()
    args = parser.parse_args()

    if args.leading_only:
        mode = "leading"
    elif args.trailing_only:
        mode = "trailing"
    else:
        mode = "both"

    input_bytes = sys.stdin.buffer.read()
    encoding = sys.stdin.encoding or "utf-8"
    input_text = input_bytes.decode(encoding, errors="replace")

    output_text = normalize_text(
        input_text,
        mode=mode,
        keep_original_blank_lines=args.keep_original_blank_lines,
        collapse_blank_lines=args.collapse_blank_lines,
        preserve_line_endings=args.preserve_line_endings,
    )

    if args.preserve_line_endings:
        sys.stdout.buffer.write(output_text.encode(encoding, errors="replace"))
    else:
        sys.stdout.write(output_text)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
