"""Tests for credential precedence and resource cleanup."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from axlib.credentials.exceptions import CredentialBackendError
from axlib.credentials.manager import (
    lookup_values,
    merge_missing,
    normalize_legacy_service_name,
)
from axlib.credentials.settings import CredentialSettings


class FakeStore:
    def __init__(self, values: Mapping[str, str]) -> None:
        self.values = dict(values)
        self.calls: list[tuple[str, tuple[str, ...]]] = []

    def read(self, service: str, fields: Sequence[str]) -> dict[str, str]:
        self.calls.append((service, tuple(fields)))
        return {field: self.values[field] for field in fields if field in self.values}


class FakeCache:
    def __init__(
        self,
        values: Mapping[str, str] | None = None,
        *,
        fail_on_enter: bool = False,
    ) -> None:
        self.values = dict(values or {})
        self.fail_on_enter = fail_on_enter
        self.entered = False
        self.exited = False
        self.read_calls: list[tuple[str, tuple[str, ...]]] = []
        self.writes: list[tuple[str, dict[str, str]]] = []

    def __enter__(self) -> FakeCache:
        if self.fail_on_enter:
            raise CredentialBackendError("cache down")
        self.entered = True
        return self

    def __exit__(self, exc_type, exc, traceback) -> bool:
        self.exited = True
        return False

    def read(self, service: str, fields: Sequence[str]) -> dict[str, str]:
        self.read_calls.append((service, tuple(fields)))
        return {field: self.values[field] for field in fields if field in self.values}

    def write(self, service: str, values: Mapping[str, str]) -> None:
        self.writes.append((service, dict(values)))


def test_lookup_precedence_and_cache_cleanup() -> None:
    settings = CredentialSettings(redis_enabled=True)
    cache = FakeCache({"netpass": "cached-pass"})
    store = FakeStore({"netpass": "file-pass", "netenable": "file-enable"})
    warnings: list[str] = []

    result = lookup_values(
        "first.last",
        {"netuser": "environment-user", "netpass": None, "netenable": None},
        settings=settings,
        cache=cache,
        store=store,
        reporter=warnings.append,
    )

    assert result == {
        "netuser": "environment-user",
        "netpass": "cached-pass",
        "netenable": "file-enable",
    }
    assert cache.read_calls == [("firstlast", ("netpass", "netenable"))]
    assert store.calls == [("firstlast", ("netenable",))]
    assert cache.writes == [("firstlast", {"netenable": "file-enable"})]
    assert cache.entered is True
    assert cache.exited is True
    assert warnings == []


def test_cache_failure_falls_back_to_store() -> None:
    settings = CredentialSettings(redis_enabled=True)
    cache = FakeCache(fail_on_enter=True)
    store = FakeStore({"secret": "from-file"})
    warnings: list[str] = []

    result = lookup_values(
        "service",
        {"secret": None},
        settings=settings,
        cache=cache,
        store=store,
        reporter=warnings.append,
    )

    assert result == {"secret": "from-file"}
    assert store.calls == [("service", ("secret",))]
    assert warnings and "cache down" in warnings[0]


def test_merge_missing_preserves_higher_priority_values() -> None:
    destination = {"user": "environment", "password": None}
    returned = merge_missing(destination, {"user": "cache", "password": "file"})
    assert returned is destination
    assert destination == {"user": "environment", "password": "file"}


def test_legacy_service_normalization_is_retained() -> None:
    assert normalize_legacy_service_name("first.last-name_ops") == "firstlastnameops"


def test_default_chain_prefers_sqlite_before_credential_file(monkeypatch) -> None:
    events: list[tuple[str, tuple[str, ...]]] = []

    class FakeSQLiteStore:
        def __init__(self, settings: CredentialSettings) -> None:
            assert settings.sqlite_enabled is True

        def read(self, service: str, fields: Sequence[str]) -> dict[str, str]:
            events.append(("sqlite", tuple(fields)))
            return {"netpass": "sqlite-pass"} if "netpass" in fields else {}

    class FakeCredentialFileStore:
        def __init__(self, settings: CredentialSettings) -> None:
            assert settings.sqlite_enabled is True

        def read(self, service: str, fields: Sequence[str]) -> dict[str, str]:
            events.append(("file", tuple(fields)))
            return {"netenable": "file-enable"} if "netenable" in fields else {}

    monkeypatch.setattr(
        "axlib.credentials.manager.SQLiteCredentialStore",
        FakeSQLiteStore,
    )
    monkeypatch.setattr(
        "axlib.credentials.manager.CredentialFileStore",
        FakeCredentialFileStore,
    )

    result = lookup_values(
        "first.last",
        {"netuser": "environment-user", "netpass": None, "netenable": None},
        settings=CredentialSettings(sqlite_enabled=True, credential_file_enabled=True),
    )

    assert result == {
        "netuser": "environment-user",
        "netpass": "sqlite-pass",
        "netenable": "file-enable",
    }
    assert events == [
        ("sqlite", ("netpass", "netenable")),
        ("file", ("netenable",)),
    ]


def test_sqlite_failure_still_falls_back_to_credential_file(monkeypatch) -> None:
    warnings: list[str] = []

    class FailingSQLiteStore:
        def __init__(self, settings: CredentialSettings) -> None:
            pass

        def read(self, service: str, fields: Sequence[str]) -> dict[str, str]:
            raise CredentialBackendError("database unavailable")

    class WorkingCredentialFileStore:
        def __init__(self, settings: CredentialSettings) -> None:
            pass

        def read(self, service: str, fields: Sequence[str]) -> dict[str, str]:
            return {"secret": "file-value"}

    monkeypatch.setattr(
        "axlib.credentials.manager.SQLiteCredentialStore",
        FailingSQLiteStore,
    )
    monkeypatch.setattr(
        "axlib.credentials.manager.CredentialFileStore",
        WorkingCredentialFileStore,
    )

    result = lookup_values(
        "service",
        {"secret": None},
        settings=CredentialSettings(sqlite_enabled=True, credential_file_enabled=True),
        reporter=warnings.append,
    )

    assert result == {"secret": "file-value"}
    assert warnings and "SQLite credential lookup failed" in warnings[0]
