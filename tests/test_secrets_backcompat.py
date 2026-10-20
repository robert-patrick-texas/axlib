# Copyright 2026 Robert Patrick
# SPDX-License-Identifier: Apache-2.0

"""Regression tests for the established axlib credential API."""

from __future__ import annotations

import grp
import os
import subprocess
import sys
from pathlib import Path

import pytest

import axlib as ax
from axlib import config, secrets

from .test_settings import NOT_ROOT, locked_directory


def _clear_network_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("NETUSER", "NETPASS", "NETENABLE"):
        monkeypatch.delenv(name, raising=False)


def test_package_root_getkeys_uses_user_and_shared_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_network_environment(monkeypatch)
    monkeypatch.setenv("USER", "new.operator")
    monkeypatch.setattr(config, "shared_service", "approved.shared")
    services: list[str] = []

    def fake_readkeyring(**values: str | None) -> dict[str, str | None]:
        service = values["service"]
        assert isinstance(service, str)
        services.append(service)
        if service == "new.operator":
            return {"netuser": None, "netpass": None, "netenable": None}
        return {
            "netuser": "shared-user",
            "netpass": "shared-pass",
            "netenable": "shared-enable",
        }

    monkeypatch.setattr(secrets, "readkeyring", fake_readkeyring)

    netuser, netpass, netenable = ax.getkeys()

    assert (netuser, netpass, netenable) == (
        "shared-user",
        "shared-pass",
        "shared-enable",
    )
    assert services == ["new.operator", "approved.shared"]


def test_missing_netenable_triggers_lookup(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NETUSER", "environment-user")
    monkeypatch.setenv("NETPASS", "environment-pass")
    monkeypatch.delenv("NETENABLE", raising=False)
    calls: list[dict[str, str | None]] = []

    def fake_readkeyring(**values: str | None) -> dict[str, str | None]:
        calls.append(values)
        return {
            "netuser": values["netuser"],
            "netpass": values["netpass"],
            "netenable": "stored-enable",
        }

    monkeypatch.setattr(secrets, "readkeyring", fake_readkeyring)
    assert secrets.getnetkeys("operator") == (
        "environment-user",
        "environment-pass",
        "stored-enable",
    )
    assert len(calls) == 1


def test_blank_environment_values_are_replaced(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NETUSER", "   ")
    monkeypatch.setenv("NETPASS", "")
    monkeypatch.setenv("NETENABLE", "environment-enable")

    def fake_readkeyring(**values: str | None) -> dict[str, str | None]:
        assert values["netuser"] is None
        assert values["netpass"] is None
        return {
            "netuser": "stored-user",
            "netpass": "stored-pass",
            "netenable": values["netenable"],
        }

    monkeypatch.setattr(secrets, "readkeyring", fake_readkeyring)
    assert secrets.getnetkeys("operator") == (
        "stored-user",
        "stored-pass",
        "environment-enable",
    )


def test_service_filter_and_updatedict_remain_available() -> None:
    assert secrets.servicenamefilter("first.last-name_ops") == "firstlastnameops"
    destination = {"one": None, "two": "keep"}
    assert secrets.updatedict(destination, {"one": "fill", "two": "replace"}) == {
        "one": "fill",
        "two": "keep",
    }


def test_package_root_getkeys_reads_sqlite_and_preserves_shared_fallback(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from axlib.credentials.settings import CredentialSettings
    from axlib.credentials.sqlite_store import (
        SQLiteCredentialStore,
        generate_sqlite_key_file,
    )

    _clear_network_environment(monkeypatch)
    group = grp.getgrgid(os.getgid()).gr_name
    key_file = tmp_path / "sqlite.key"
    database = tmp_path / "credentials.db"
    generate_sqlite_key_file(key_file, group=group)
    settings = CredentialSettings(
        sqlite_enabled=True,
        sqlite_database=database,
        sqlite_key_file=key_file,
        sqlite_group=group,
    )
    store = SQLiteCredentialStore(settings)
    store.initialize()
    store.create(
        "approvedshared",
        {
            "netuser": "shared-user",
            "netpass": "shared-pass",
            "netenable": "shared-enable",
        },
    )

    monkeypatch.setenv("USER", "not.enlisted")
    monkeypatch.setattr(config, "sqlite_enable", True)
    monkeypatch.setattr(config, "sqlite_database", str(database))
    monkeypatch.setattr(config, "sqlite_key", None)
    monkeypatch.setattr(config, "sqlite_key_file", str(key_file))
    monkeypatch.setattr(config, "sqlite_timeout", 1.0)
    monkeypatch.setattr(config, "sqlite_database_mode", 0o660)
    monkeypatch.setattr(config, "sqlite_key_file_mode", 0o640)
    monkeypatch.setattr(config, "sqlite_owner", None)
    monkeypatch.setattr(config, "sqlite_group", group)
    monkeypatch.setattr(config, "sqlite_enforce_permissions", True)
    monkeypatch.setattr(config, "shared_service", "approved.shared")
    monkeypatch.setattr(config, "redis_enable", False)
    monkeypatch.setattr(config, "credential_file_enable", False)
    monkeypatch.setattr(config, "credential_file", None)
    monkeypatch.setattr(config, "credential_file_key", None)
    monkeypatch.setattr(config, "credential_file_key_file", None)

    assert ax.getkeys() == ("shared-user", "shared-pass", "shared-enable")


def _credentials_command(config_file: Path) -> subprocess.CompletedProcess[str]:
    """Run ``axlib credentials network`` in a fresh Python process.

    A new process is needed because ``axlib.config`` reads the configuration
    file when it is first imported, and this test process did that long ago.
    """
    environment = {**os.environ, "AXLIB_CONFIG_FILE": str(config_file)}
    for name in ("NETUSER", "NETPASS", "NETENABLE"):
        environment.pop(name, None)
    return subprocess.run(
        [sys.executable, "-m", "axlib", "credentials", "network", "--service", "ops"],
        capture_output=True,
        text=True,
        env=environment,
        check=False,
        timeout=120,
    )


def test_credentials_command_reports_a_missing_config_file_in_one_line(
    tmp_path: Path,
) -> None:
    missing = tmp_path / "missing.toml"

    result = _credentials_command(missing)

    # Status 2 keeps "could not check" apart from 1, "fields are missing".
    assert result.returncode == 2
    assert result.stdout == ""
    assert result.stderr == (
        f"axlib credentials: Credential configuration file does not exist: {missing}\n"
    )


@NOT_ROOT
def test_credentials_command_reports_an_unreadable_config_file(
    tmp_path: Path,
) -> None:
    config_file = tmp_path / "etc-axlib" / "axlib.toml"
    config_file.parent.mkdir()
    config_file.write_text("[credentials]\n", encoding="utf-8")

    with locked_directory(config_file.parent):
        result = _credentials_command(config_file)

    assert result.returncode == 2
    assert result.stderr == (
        "axlib credentials: Credential configuration file cannot be read: "
        f"{config_file} (Permission denied)\n"
    )
