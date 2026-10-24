# Copyright 2026 Robert Patrick
# SPDX-License-Identifier: Apache-2.0

"""Print one release's section of RELEASE_NOTES.md as a GitHub release body.

The release workflow (``.github/workflows/release.yml``) runs this to write the
notes of the GitHub release it creates, so the release page and
``RELEASE_NOTES.md`` always say the same thing.  The section's
``## <version>`` heading is left out, because the release title already names
the version, and every deeper heading moves up one level (``### Overview``
becomes ``## Overview``).  Lines inside fenced code blocks are copied as they
are, so a ``#`` comment in an example is never mistaken for a heading.

Dependencies:
    Python standard library only.

Example:
    ``python tools/release_notes.py "$(uv version --short)" > notes.md``
"""

from __future__ import annotations

import sys
from collections.abc import Sequence
from pathlib import Path

DEFAULT_NOTES = Path(__file__).resolve().parents[1] / "RELEASE_NOTES.md"
FENCES = ("```", "~~~")


def release_section(text: str, version: str) -> str:
    """Return the ``## <version>`` section of release notes, headings raised.

    Args:
        text (str): The whole release notes document.
        version (str): Release number to find, such as ``"1.2.3"``.

    Returns:
        str: The section's lines after its heading and before the next ``##``
        heading, without leading or trailing blank lines, ending in a newline.

    Raises:
        LookupError: If the document has no ``## <version>`` heading.
    """
    section: list[str] | None = None
    in_fence = False
    for line in text.splitlines():
        if not in_fence and line.startswith("## "):
            if section is not None:
                break  # the next release's heading ends this one's section
            if line.removeprefix("## ").strip() == version:
                section = []
            continue
        if line.startswith(FENCES):
            in_fence = not in_fence
        if section is not None:
            raise_heading = not in_fence and line.startswith("###")
            section.append(line[1:] if raise_heading else line)
    if section is None:
        raise LookupError(f"no '## {version}' section")
    return "\n".join(section).strip("\n") + "\n"


def main(argv: Sequence[str] | None = None) -> int:
    """Print the release notes of one version to standard output.

    Args:
        argv (Sequence[str] | None): ``VERSION [NOTES_FILE]``; defaults to
            ``sys.argv[1:]``.  ``NOTES_FILE`` defaults to the repository's
            ``RELEASE_NOTES.md``.

    Returns:
        int: ``0`` when the section was printed, ``1`` when the file cannot be
        read or has no section for the version, or ``2`` for a usage error.

    Raises:
        OSError: If standard output cannot be written.
    """
    arguments = list(sys.argv[1:] if argv is None else argv)
    if len(arguments) not in {1, 2}:
        print("usage: release_notes.py VERSION [NOTES_FILE]", file=sys.stderr)
        return 2
    path = Path(arguments[1]) if len(arguments) == 2 else DEFAULT_NOTES
    try:
        notes = release_section(path.read_text(encoding="utf-8"), arguments[0])
    except (OSError, LookupError) as error:
        print(f"release_notes.py: {path}: {error}", file=sys.stderr)
        return 1
    sys.stdout.write(notes)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
