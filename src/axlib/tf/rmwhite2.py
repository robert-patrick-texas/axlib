#!/usr/bin/env python3
"""
normalize_whitespace.py

Normalizes whitespace in text:
  - Converts tabs to spaces
  - Collapses multiple consecutive spaces into one
  - Preserves content inside quoted pairs

Quote protection is line-scoped: a quote character is treated as a delimiter
only if a matching close quote exists on the same line. Unmatched quote
characters (apostrophes, inch-marks, etc.) are passed through as literals.

Supported quote pairs:
  - Straight double quotes: "..."
  - Curly/smart single quotes: \u2018...\u2019
  - Straight single quotes: '...' — treated as an opener only when preceded
    by whitespace, equal =, or at the start of a line (heuristic to avoid
    treating apostrophes as delimiters).

Usage (CLI):
    echo "some\t  text" | python normalize_whitespace.py
    python normalize_whitespace.py < input.txt

Usage (module):
    from normalize_whitespace import normalize
    result = normalize("some\t  text")
"""

import re
import sys


def normalize(text: str) -> str:
    """
    Normalize whitespace in text, preserving quoted regions.

    Supported quote pairs:
      - Straight double quotes: "..."
      - Curly/smart single quotes: '...' (U+2018/U+2019)
      - Straight single quotes: '...' (U+0027), opener valid only after
        whitespace, equal or at start of line

    Outside quoted regions:
      - Tabs -> single space
      - Multiple consecutive spaces -> single space

    Quote protection is line-scoped: a quote character is treated as a
    delimiter only if a matching close quote exists on the same line.
    Unmatched quote characters (apostrophes, inch-marks, etc.) are passed
    through as literals without triggering protection.

    Args:
        text: Input string to process.

    Returns:
        Normalized string.
    """
    # Standard quote pairs: (open_char, close_char)
    # Straight single quotes handled separately due to apostrophe ambiguity.
    quote_pairs = [
        ('"', '"'),  # straight double quotes
        ("\u2018", "\u2019"),  # curly single quotes
    ]
    straight_single = "'"

    def find_close_on_line(src: str, start: int, close_q: str) -> int:
        """
        Scan forward from `start` for `close_q`, skipping backslash-escaped
        characters. Stop and return -1 if a newline is reached before the
        closing quote (line-scoped balancing). Returns the index of the
        closing quote character if found, else -1.
        """
        j = start
        while j < len(src):
            ch = src[j]
            if ch == "\n":
                return -1  # newline boundary — no match on this line
            if ch == "\\" and j + 1 < len(src) and src[j + 1] != "\n":
                j += 2  # skip escaped character
                continue
            if ch == close_q:
                return j
            j += 1
        return -1  # end of input without finding close

    def is_straight_single_opener(src: str, pos: int) -> bool:
        """
        Heuristic: treat a straight single quote as an opener only when it
        appears at the start of the string, after whitespace, or after '='.
        The '=' case covers attribute-style syntax (e.g. key='value').
        This filters out apostrophes mid-word.
        """
        if src[pos] != straight_single:
            return False
        if pos == 0:
            return True
        prev = src[pos - 1]
        return prev in (" ", "\t", "\n", "=")

    def match_opener(src: str, pos: int) -> tuple[str, str, int] | None:
        """
        If src[pos] is a balanced open-quote (close found on same line),
        return (open_q, close_q, close_pos). Otherwise return None.

        Checks standard pairs first, then straight single quote with the
        whitespace-precedence heuristic.
        """
        # Standard pairs
        for open_q, close_q in quote_pairs:
            if src[pos] == open_q:
                close_pos = find_close_on_line(src, pos + 1, close_q)
                if close_pos != -1:
                    return (open_q, close_q, close_pos)
                return None  # unmatched opener — don't check other pairs

        # Straight single quote with heuristic guard
        if is_straight_single_opener(src, pos):
            close_pos = find_close_on_line(src, pos + 1, straight_single)
            if close_pos != -1:
                return (straight_single, straight_single, close_pos)

        return None

    result = []
    i = 0
    n = len(text)

    while i < n:
        m = match_opener(text, i)
        if m:
            _, _, close_pos = m
            j = close_pos + 1  # index after the closing quote
            result.append(text[i:j])  # verbatim
            i = j
        else:
            # Accumulate unprotected characters until a balanced opener or end
            chunk_start = i
            while i < n:
                if match_opener(text, i) is not None:
                    break
                i += 1
            chunk = text[chunk_start:i]
            chunk = chunk.replace("\t", " ")
            chunk = re.sub(r" {2,}", " ", chunk)
            result.append(chunk)

    return "".join(result)


def main() -> None:
    input_text = sys.stdin.read()
    sys.stdout.write(normalize(input_text))


if __name__ == "__main__":
    main()
