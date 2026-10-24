# Copyright 2026 Robert Patrick
# SPDX-License-Identifier: Apache-2.0

"""Release-notes extraction and the release documents of the current version.

``tools/release_notes.py`` is a standalone script, not part of the package, so
it is loaded from its file.  The release workflow publishes the version in
``pyproject.toml`` only after these tests pass, so a release cannot go out
without its ``RELEASE_NOTES.md`` section and dated ``CHANGELOG.md`` heading.
"""

from __future__ import annotations

import importlib.util
import re
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "release_notes", ROOT / "tools" / "release_notes.py"
)
assert SPEC is not None
assert SPEC.loader is not None
release_notes = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(release_notes)

NOTES = """\
# Release notes

## 9.1.0

### Overview

New things.

```bash
## a shell comment, not a heading
### neither is this
```

#### Detail

## 9.0.0

### Overview

Old things.
"""


def _project_version() -> str:
    """Return the release number from pyproject.toml, its single source."""
    with (ROOT / "pyproject.toml").open("rb") as handle:
        return tomllib.load(handle)["project"]["version"]


def test_release_section_drops_its_heading_and_raises_the_others() -> None:
    assert release_notes.release_section(NOTES, "9.1.0") == (
        "## Overview\n"
        "\n"
        "New things.\n"
        "\n"
        "```bash\n"
        "## a shell comment, not a heading\n"
        "### neither is this\n"
        "```\n"
        "\n"
        "### Detail\n"
    )


def test_release_section_ends_at_the_end_of_the_document() -> None:
    assert release_notes.release_section(NOTES, "9.0.0") == (
        "## Overview\n\nOld things.\n"
    )


def test_release_section_reports_a_missing_version() -> None:
    with pytest.raises(LookupError, match=r"no '## 9\.2\.0' section"):
        release_notes.release_section(NOTES, "9.2.0")


def test_main_prints_the_section_and_reports_errors(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    notes = tmp_path / "RELEASE_NOTES.md"
    notes.write_text(NOTES, encoding="utf-8")

    assert release_notes.main(["9.0.0", str(notes)]) == 0
    assert capsys.readouterr().out == "## Overview\n\nOld things.\n"
    assert release_notes.main(["9.2.0", str(notes)]) == 1
    assert "no '## 9.2.0' section" in capsys.readouterr().err
    assert release_notes.main([]) == 2


def test_current_version_has_release_notes() -> None:
    notes = release_notes.release_section(
        (ROOT / "RELEASE_NOTES.md").read_text(encoding="utf-8"), _project_version()
    )
    assert notes.startswith("## Overview\n")


def test_current_version_has_a_dated_changelog_heading() -> None:
    heading = rf"## {re.escape(_project_version())} - \d{{4}}-\d{{2}}-\d{{2}}"
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    assert re.search(rf"^{heading}$", changelog, re.MULTILINE)
