#!/usr/bin/env python3
"""
Normalize whitespace outside of quoted regions.

Behavior:
- Processes input text from a string when used as a module.
- Processes input text from stdin when run from the command line.
- Outside of quoted regions:
  - converts tabs to a single space
  - collapses runs of multiple consecutive spaces into a single space
- Inside quoted regions:
  - leaves text unchanged

Supported quote types:
- single quotes: '
- double quotes: "

Quoted regions do not span lines. A quote only protects text until the
matching unescaped quote character on the same line.

Examples:
    Input:
        one\t\t two   three "keep \t  this   same" 'and   this\ttoo'

    Output:
        one two three "keep \t  this   same" 'and   this\ttoo'

CLI usage:
    cat input.txt | python3 normalize_spaces_outside_quotes.py
    echo 'a\t\t b   "c   d"' | python3 normalize_spaces_outside_quotes.py

Module usage:
    from normalize_spaces_outside_quotes import normalize_spaces_outside_quotes
    result = normalize_spaces_outside_quotes(text)
"""

import sys


def normalize_line_outside_quotes(line: str) -> str:
    """
    Normalize a single line of text outside quoted regions.

    Outside quotes:
    - tabs become a single space
    - multiple consecutive spaces collapse to one space

    Inside quotes:
    - content is preserved exactly

    Backslash escaping is supported for quote characters inside quoted text.
    """
    result: list[str] = []

    in_quote = False
    quote_char = ""
    previous_was_space_outside_quotes = False
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

        if char in ("'", '"'):
            in_quote = True
            quote_char = char
            previous_was_space_outside_quotes = False
            result.append(char)
            continue

        if char == "\t":
            char = " "

        if char == " ":
            if not previous_was_space_outside_quotes:
                result.append(" ")
                previous_was_space_outside_quotes = True
            continue

        previous_was_space_outside_quotes = False
        result.append(char)

    return "".join(result)


def normalize_spaces_outside_quotes(text: str) -> str:
    """
    Normalize whitespace outside quotes for a block of text.

    Line endings are preserved as they appear in the input.
    """
    parts = text.splitlines(keepends=True)
    return "".join(normalize_line_outside_quotes(part) for part in parts)


def main() -> int:
    """
    Read text from stdin, normalize it, and write to stdout.
    """
    try:
        input_text = sys.stdin.read()
        output_text = normalize_spaces_outside_quotes(input_text)
        sys.stdout.write(output_text)
    except BrokenPipeError:
        return 1
    except KeyboardInterrupt:
        return 130
    else:
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
