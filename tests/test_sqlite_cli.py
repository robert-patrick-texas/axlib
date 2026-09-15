"""Command-line lifecycle tests for the encrypted SQLite store."""

from __future__ import annotations

import grp
import os
from io import StringIO
from pathlib import Path

import pytest

from axlib.credentials.sqlite_cli import main


def _config_file(tmp_path: Path) -> Path:
    group = grp.getgrgid(os.getgid()).gr_name
    path = tmp_path / "axlib.toml"
    path.write_text(
        f"""
[sqlite]
enabled = true
database = "credentials.db"
key_file = "keys/sqlite.key"
timeout = 1.0
database_mode = "0660"
key_file_mode = "0640"
group = {group!r}
""",
        encoding="utf-8",
    )
    return path


def test_sqlite_cli_full_lifecycle_never_prints_secrets(tmp_path: Path) -> None:
    config = _config_file(tmp_path)

    initialized = StringIO()
    assert (
        main(
            ["--config", str(config), "init", "--generate-key"],
            stream=initialized,
        )
        == 0
    )
    assert "status=initialized" in initialized.getvalue()

    added = StringIO()
    assert (
        main(
            [
                "--config",
                str(config),
                "add",
                "--service",
                "first.last",
                "--set",
                "netuser=operator-user",
                "--set",
                "netpass=do-not-print-password",
                "--set",
                "netenable=do-not-print-enable",
            ],
            stream=added,
        )
        == 0
    )
    assert "service=firstlast" in added.getvalue()
    assert "fields=netenable,netpass,netuser" in added.getvalue()
    assert "status=added" in added.getvalue()
    assert "do-not-print" not in added.getvalue()

    listed = StringIO()
    assert main(["--config", str(config), "list", "--json"], stream=listed) == 0
    rendered = listed.getvalue()
    assert '"service": "firstlast"' in rendered
    assert '"netpass"' in rendered
    assert "operator-user" not in rendered
    assert "do-not-print" not in rendered

    updated = StringIO()
    assert (
        main(
            [
                "--config",
                str(config),
                "update",
                "--service",
                "first.last",
                "--set",
                "netpass=rotated-do-not-print",
            ],
            stream=updated,
        )
        == 0
    )
    assert "rotated-do-not-print" not in updated.getvalue()
    assert "status=updated" in updated.getvalue()

    deleted = StringIO()
    assert (
        main(
            [
                "--config",
                str(config),
                "delete",
                "--service",
                "first.last",
                "--yes",
            ],
            stream=deleted,
        )
        == 0
    )
    assert "status=deleted" in deleted.getvalue()

    empty = StringIO()
    assert main(["--config", str(config), "list"], stream=empty) == 0
    assert "firstlast" not in empty.getvalue()


def test_sqlite_cli_rotate_key(tmp_path: Path) -> None:
    config = _config_file(tmp_path)
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

    key_file = tmp_path / "keys" / "sqlite.key"
    old_key = key_file.read_text()

    dry_run = StringIO()
    assert (
        main(
            ["--config", str(config), "rotate-key", "--generate-key", "--dry-run"],
            stream=dry_run,
        )
        == 0
    )
    assert "dry_run=true" in dry_run.getvalue()
    assert "records=1" in dry_run.getvalue()
    assert key_file.read_text() == old_key

    with pytest.raises(SystemExit):
        main(["--config", str(config), "rotate-key", "--generate-key"])

    rotated = StringIO()
    assert (
        main(
            ["--config", str(config), "rotate-key", "--generate-key", "--yes"],
            stream=rotated,
        )
        == 0
    )
    assert "status=rotated" in rotated.getvalue()
    assert "rotated=1" in rotated.getvalue()
    assert key_file.read_text() != old_key

    listed = StringIO()
    assert main(["--config", str(config), "list", "--json"], stream=listed) == 0
    assert '"op"' in listed.getvalue()
