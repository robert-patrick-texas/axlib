# Copyright 2026 Robert Patrick
# SPDX-License-Identifier: Apache-2.0

"""Tests for options shared by both credential administration CLIs."""

from __future__ import annotations

from io import StringIO
from pathlib import Path

import pytest

from axlib.credentials import admin as admin_module
from axlib.credentials import store_cli
from axlib.credentials.admin import StoreKind

from .conftest import write_store_config
from .test_credential_admin import _FakeCache

BOTH_KINDS = pytest.mark.parametrize("kind", list(StoreKind))


def _run(kind: StoreKind, config: Path, *arguments: str) -> tuple[int, str]:
    output = StringIO()
    status = store_cli.main(kind, ["--config", str(config), *arguments], stream=output)
    return status, output.getvalue()


def _initialized(tmp_path: Path, kind: StoreKind) -> Path:
    config = write_store_config(tmp_path)
    assert _run(kind, config, "init", "--generate-key")[0] == 0
    return config


@BOTH_KINDS
def test_update_can_remove_an_optional_field(tmp_path: Path, kind: StoreKind) -> None:
    config = _initialized(tmp_path, kind)
    _run(
        kind,
        config,
        "add",
        "--service",
        "ops",
        "--set",
        "netuser=ops",
        "--set",
        "netpass=pw-hidden",
        "--set",
        "netenable=en-hidden",
    )
    status, output = _run(
        kind, config, "update", "--service", "ops", "--remove", "netenable"
    )
    assert status == 0
    assert "removed=netenable" in output
    assert "status=updated" in output
    assert "hidden" not in output
    listing = _run(kind, config, "list", "--json")[1]
    assert '"netenable"' not in listing


def test_required_fields_cannot_be_removed_even_in_a_dry_run(tmp_path: Path) -> None:
    config = write_store_config(tmp_path)
    with pytest.raises(SystemExit):
        _run(
            StoreKind.SQLITE,
            config,
            "update",
            "--service",
            "ops",
            "--remove",
            "netpass",
            "--dry-run",
        )


def test_infoblox_profile_is_selectable(tmp_path: Path) -> None:
    config = _initialized(tmp_path, StoreKind.SQLITE)
    infoblox = (
        "add",
        "--service",
        "infoblox",
        "--set",
        "ibgrid=gm",
        "--set",
        "ibuser=api",
    )
    with pytest.raises(SystemExit):
        _run(StoreKind.SQLITE, config, *infoblox, "--set", "ibpass=pw")
    status, output = _run(
        StoreKind.SQLITE,
        config,
        *infoblox,
        "--set",
        "ibpass=pw",
        "--profile",
        "infoblox",
    )
    assert status == 0
    assert "fields=ibgrid,ibpass,ibuser" in output


def test_delete_reports_a_missing_service(tmp_path: Path) -> None:
    config = _initialized(tmp_path, StoreKind.FILE)
    status, output = _run(
        StoreKind.FILE, config, "delete", "--service", "ghost", "--yes"
    )
    assert status == 1
    assert "status=not-found" in output


def test_cache_failure_is_reported_after_a_successful_change(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config = _initialized(tmp_path, StoreKind.SQLITE)
    with config.open("a", encoding="utf-8") as handle:
        handle.write("\n[redis]\nenabled = true\n")
    monkeypatch.setattr(admin_module, "RedisCredentialCache", _FakeCache)
    monkeypatch.setattr(_FakeCache, "fail", True)

    status, output = _run(
        StoreKind.SQLITE,
        config,
        "add",
        "--service",
        "ops",
        "--set",
        "netuser=ops",
        "--set",
        "netpass=pw",
    )
    assert status == 1
    assert "status=added" in output
    assert "cache=stale" in output
    assert "Redis cache could not be cleared" in capsys.readouterr().err
