# Copyright 2026 Robert Patrick
# SPDX-License-Identifier: Apache-2.0

"""Command-line interface tests using temporary files."""

from __future__ import annotations

from pathlib import Path

import pytest

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


def test_root_version_command(capsys: pytest.CaptureFixture[str]) -> None:
    assert axlib_main(["version"]) == 0
    assert capsys.readouterr().out.strip() == "1.0.0"


def test_root_version_flag(capsys: pytest.CaptureFixture[str]) -> None:
    assert axlib_main(["--version"]) == 0
    assert capsys.readouterr().out.strip() == "1.0.0"


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
    """The installed API and built artifact must report one release version."""
    import tomllib
    from pathlib import Path

    import axlib

    project_file = Path(__file__).resolve().parents[1] / "pyproject.toml"
    with project_file.open("rb") as handle:
        project = tomllib.load(handle)

    assert axlib.__version__ == project["project"]["version"]
