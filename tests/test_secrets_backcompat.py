"""Regression tests for the established axlib credential API."""

from __future__ import annotations

import grp
import os

import axlib as ax
import axlib.config as config
import axlib.secrets as secrets


def _clear_network_environment(monkeypatch) -> None:
    for name in ("NETUSER", "NETPASS", "NETENABLE"):
        monkeypatch.delenv(name, raising=False)


def test_package_root_getkeys_uses_user_and_shared_fallback(monkeypatch) -> None:
    _clear_network_environment(monkeypatch)
    monkeypatch.setenv("USER", "new.operator")
    monkeypatch.setattr(config, "shared_service", "approved.shared")
    services: list[str] = []

    def fake_readkeyring(**values):
        service = values["service"]
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


def test_missing_netenable_triggers_lookup(monkeypatch) -> None:
    monkeypatch.setenv("NETUSER", "environment-user")
    monkeypatch.setenv("NETPASS", "environment-pass")
    monkeypatch.delenv("NETENABLE", raising=False)
    calls: list[dict[str, str | None]] = []

    def fake_readkeyring(**values):
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


def test_blank_environment_values_are_replaced(monkeypatch) -> None:
    monkeypatch.setenv("NETUSER", "   ")
    monkeypatch.setenv("NETPASS", "")
    monkeypatch.setenv("NETENABLE", "environment-enable")

    def fake_readkeyring(**values):
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
    monkeypatch,
    tmp_path,
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
