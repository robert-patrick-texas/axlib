# Copyright 2026 Robert Patrick
# SPDX-License-Identifier: Apache-2.0

"""Command-line interface tests using temporary files."""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

import pytest

import axlib
from axlib.__main__ import main as axlib_main
from axlib.credentials.file_cli import main as credential_file_main
from axlib.tf.__main__ import main as text_main
from axlib.tf.rmcomment import main as comment_main


def test_filter_cli_uses_standard_input_and_output_paths(tmp_path: Path) -> None:
    source = tmp_path / "input.conf"
    output = tmp_path / "output.conf"
    source.write_text("hostname edge-1  # note\n", encoding="utf-8")

    status = comment_main([str(source), "--output", str(output)])

    assert status == 0
    assert output.read_text(encoding="utf-8") == "hostname edge-1  \n"


def test_unified_text_dispatcher(tmp_path: Path) -> None:
    source = tmp_path / "input.txt"
    output = tmp_path / "output.txt"
    source.write_text("// remove\nkeep\n", encoding="utf-8")

    status = text_main(["slash-comments", str(source), "-o", str(output)])

    assert status == 0
    assert output.read_text(encoding="utf-8") == "keep\n"


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _project_version() -> str:
    """Return the release number from pyproject.toml, its single source."""
    with (PROJECT_ROOT / "pyproject.toml").open("rb") as handle:
        return tomllib.load(handle)["project"]["version"]


def test_root_version_command(capsys: pytest.CaptureFixture[str]) -> None:
    assert axlib_main(["version"]) == 0
    assert capsys.readouterr().out.strip() == axlib.__version__


def test_root_version_flag(capsys: pytest.CaptureFixture[str]) -> None:
    assert axlib_main(["--version"]) == 0
    assert capsys.readouterr().out.strip() == axlib.__version__


def test_credential_file_dry_run_never_prints_values(
    capsys: pytest.CaptureFixture[str],
) -> None:
    status = credential_file_main(
        [
            "add",
            "--service",
            "first.last",
            "--set",
            "netuser=operator-user",
            "--set",
            "netpass=do-not-print-this",
            "--dry-run",
        ]
    )
    captured = capsys.readouterr()

    assert status == 0
    assert "firstlast" in captured.out
    assert "netpass" in captured.out
    assert "do-not-print-this" not in captured.out


def test_package_version_matches_project_metadata() -> None:
    """The installed API and built artifact must report one release version.

    A failure here usually means the environment is stale after a version
    bump; ``uv sync`` reinstalls the project with the new metadata.
    """
    assert axlib.__version__ == _project_version()
    from axlib import secrets

    assert secrets.__version__ == axlib.__version__


# Files that legitimately contain release numbers: the single source of truth,
# the lock file uv generates from it, the two release-history documents, and
# PKG-INFO, the metadata file generated at the top of a source distribution.
VERSIONED_FILES = {
    "pyproject.toml",
    "uv.lock",
    "CHANGELOG.md",
    "RELEASE_NOTES.md",
    "PKG-INFO",
}
SKIPPED_DIRECTORIES = {".git", ".venv", "venv", "build", "dist", "__pycache__"}


def test_release_number_is_not_hard_coded_elsewhere() -> None:
    """Prevent version drift: no other file may spell out a release number.

    Prose that names the package followed by a release number, or any copy of
    the current version, goes stale at the next release.  Code should use
    ``axlib.__version__`` and prose should not mention the number at all.
    """
    current = re.escape(_project_version())
    pattern = re.compile(
        rf"(?<![\w.]){current}(?![\w.])|\baxlib[ -]v?\d+\.\d+\.\d+",
        re.IGNORECASE,
    )
    offenders = []
    for path in PROJECT_ROOT.rglob("*"):
        relative = path.relative_to(PROJECT_ROOT)
        if (
            not path.is_file()
            or str(relative) in VERSIONED_FILES
            or SKIPPED_DIRECTORIES.intersection(relative.parts)
            or any(part.endswith(".egg-info") for part in relative.parts)
        ):
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue  # binary files cannot contain prose or code
        offenders.extend(
            f"{relative}:{text.count(chr(10), 0, match.start()) + 1}: {match.group()}"
            for match in pattern.finditer(text)
        )
    assert not offenders, "Hard-coded release numbers:\n" + "\n".join(offenders)
