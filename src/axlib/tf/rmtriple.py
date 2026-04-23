#!/usr/bin/env python3

import re
import sys


def remove_triple_quoted(text: str) -> str:
    # Regex pattern breakdown:
    # ('''|""")       : Match starting triple quotes (single or double)
    # (.*?)           : Non-greedy match for any content in between
    # (?<!\\)\1       : Match the same triple quote type that started it,
    #                   ensuring it isn't preceded by an escape backslash.
    # flags=re.DOTALL : Ensures the '.' matches newline characters.
    pattern = r"(\'\'\'|\"\"\")(.*?)(?<!\\)\1"

    # First pass: collapse multiline triple-quoted blocks into a single line
    # so the per-line logic can match and remove them cleanly.
    text = re.sub(
        pattern, lambda m: m.group(0).replace("\n", " "), text, flags=re.DOTALL
    )

    output_lines = []
    # Preserve original line endings by keeping splitlines(keepends=True)
    for line in text.splitlines(keepends=True):
        if '"""' in line or "'''" in line:
            cleaned = re.sub(pattern, "", line)
            if cleaned.strip():
                output_lines.append(cleaned)
            # else: drop blank/whitespace-only result
        else:
            output_lines.append(line)

    return "".join(output_lines)


if __name__ == "__main__":
    sys.stdout.write(remove_triple_quoted(sys.stdin.read()))


# Additional information

# Docstrings vs. Comments: In Python, triple-quoted strings are technically
# string literals, not true comments like #. If they appear at the start of
# a function or class, they are "docstrings." This script removes them
# regardless of their position.

# Handling Escapes: The regex uses a negative lookbehind (?<!\\) to prevent
# premature termination if a triple quote appears inside the comment preceded
# by a backslash.

# Multi-line Support: Using the re.DOTALL flag is essential to allow
# the . character to match line breaks, enabling the removal of comments
# that span across multiple lines.

# Regex Limitations: For complex code parsing such as distinguishing between
# a comment and a string assigned to a variable like msg = """text""",
# consider using the built-in Python ast module to parse the code's structure
# accurately.
