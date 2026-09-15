# Copyright 2026 Robert Patrick
# SPDX-License-Identifier: Apache-2.0

"""Tests for the standardized encrypted credential-file CLI."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from axlib.credentials.file_cli import main


def _write_config(tmp_path: Path) -> Path:
    config = tmp_path / "axlib.toml"
    config.write_text(
        f"""
[credential_file]
enabled = true
file = "{tmp_path / "credentials.axc"}"
key_file = "{tmp_path / "credentials.key"}"
file_mode = "0600"
key_file_mode = "0600"
group = ""
""",
        encoding="utf-8",
    )
    return config


def test_credential_file_cli_lifecycle(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    config = _write_config(tmp_path)
    assert main(["--config", str(config), "init", "--generate-key"]) == 0
    assert (
        main(
            [
                "--config",
                str(config),
                "add",
                "--service",
                "first.last",
                "--set",
                "netuser=operator",
                "--set",
                "netpass=initial-secret",
            ]
        )
        == 0
    )
    output = capsys.readouterr().out
    assert "initial-secret" not in output
    assert main(["--config", str(config), "list", "--json"]) == 0
    output = capsys.readouterr().out
    assert "firstlast" in output
    assert "netpass" in output
    assert "initial-secret" not in output
    assert (
        main(
            [
                "--config",
                str(config),
                "update",
                "--service",
                "first.last",
                "--set",
                "netpass=rotated-secret",
            ]
        )
        == 0
    )
    assert "rotated-secret" not in capsys.readouterr().out
    assert (
        main(
            [
                "--config",
                str(config),
                "delete",
                "--service",
                "first.last",
                "--yes",
            ]
        )
        == 0
    )


def test_credential_file_key_can_come_from_environment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    import base64

    key = base64.urlsafe_b64encode(os.urandom(32)).decode("ascii")
    config = tmp_path / "axlib.toml"
    config.write_text(
        f"""
[credential_file]
enabled = true
file = "{tmp_path / "credentials.axc"}"
file_mode = "0600"
key_file_mode = "0600"
group = ""
""",
        encoding="utf-8",
    )
    monkeypatch.setenv("AXLIB_CREDENTIAL_FILE_KEY", key)
    assert main(["--config", str(config), "init"]) == 0
    assert "status=initialized" in capsys.readouterr().out


def test_credential_file_cli_rotate_key(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    config = _write_config(tmp_path)
    assert main(["--config", str(config), "init", "--generate-key"]) == 0
    assert (
        main(
            [
                "--config",
                str(config),
                "add",
                "--service",
                "op",
                "--set",
                "netuser=bob",
                "--set",
                "netpass=s3cret",
            ]
        )
        == 0
    )
    capsys.readouterr()

    key_file = tmp_path / "credentials.key"
    old_key = key_file.read_text()

    assert (
        main(["--config", str(config), "rotate-key", "--generate-key", "--dry-run"])
        == 0
    )
    dry_run_output = capsys.readouterr().out
    assert "dry_run=true" in dry_run_output
    assert "records=1" in dry_run_output
    assert key_file.read_text() == old_key

    with pytest.raises(SystemExit):
        main(["--config", str(config), "rotate-key", "--generate-key"])
    capsys.readouterr()

    assert main(["--config", str(config), "rotate-key", "--generate-key", "--yes"]) == 0
    rotated_output = capsys.readouterr().out
    assert "status=rotated" in rotated_output
    assert "rotated=1" in rotated_output
    assert key_file.read_text() != old_key

    assert main(["--config", str(config), "list", "--json"]) == 0
    assert '"op"' in capsys.readouterr().out
