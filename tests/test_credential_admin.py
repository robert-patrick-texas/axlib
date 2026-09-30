# Copyright 2026 Robert Patrick
# SPDX-License-Identifier: Apache-2.0

"""Tests for the store-agnostic credential administration API."""

from __future__ import annotations

import base64
import dataclasses
import json
import os
from io import StringIO
from pathlib import Path
from types import TracebackType
from typing import ClassVar, Self

import pytest

from axlib.credentials import (
    INFOBLOX_PROFILE,
    CredentialBackendError,
    CredentialConfigurationError,
    CredentialRecordExistsError,
    CredentialRecordNotFoundError,
    CredentialSettings,
    NoteKind,
    StoreAdmin,
    StoreKind,
    configured_kinds,
    load_settings,
)
from axlib.credentials import admin as admin_module

from .conftest import write_store_config

BOTH_KINDS = pytest.mark.parametrize("kind", list(StoreKind))


def _ready(settings: CredentialSettings, kind: StoreKind) -> StoreAdmin:
    admin = StoreAdmin(settings, kind)
    admin.initialize(generate_key=True)
    return admin


def test_status_reports_each_lifecycle_state(
    store_settings: CredentialSettings,
) -> None:
    unconfigured = StoreAdmin(CredentialSettings(), StoreKind.SQLITE).status()
    assert unconfigured.state == "not-configured"
    assert not unconfigured.can_initialize

    admin = StoreAdmin(store_settings, StoreKind.SQLITE)
    missing = admin.status()
    assert missing.state == "not-initialized"
    assert missing.can_initialize
    assert missing.can_generate_key
    assert missing.error is None

    admin.initialize(generate_key=True)
    ready = admin.status()
    assert ready.state == "ready"
    assert ready.record_count == 0
    assert ready.key_file_exists
    assert not ready.can_initialize
    assert json.loads(json.dumps(ready.to_dict()))["state"] == "ready"


def test_status_reports_a_wrong_key_without_raising(
    store_settings: CredentialSettings,
) -> None:
    _ready(store_settings, StoreKind.SQLITE)
    wrong_key = base64.urlsafe_b64encode(os.urandom(32)).decode("ascii")
    settings = dataclasses.replace(store_settings, sqlite_key=wrong_key)
    status = StoreAdmin(settings, StoreKind.SQLITE).status()
    assert status.state == "error"
    assert status.key_source == "environment"
    assert status.error


def test_generate_key_refuses_an_environment_key(
    store_settings: CredentialSettings,
) -> None:
    settings = dataclasses.replace(store_settings, credential_file_key="ignored")
    with pytest.raises(CredentialConfigurationError, match="AXLIB_CREDENTIAL_FILE_KEY"):
        StoreAdmin(settings, StoreKind.FILE).initialize(generate_key=True)


@BOTH_KINDS
def test_record_lifecycle_never_returns_secrets(
    store_settings: CredentialSettings, kind: StoreKind
) -> None:
    admin = _ready(store_settings, kind)
    values = {"netuser": "jsmith", "netpass": "pw-secret", "netenable": "en-secret"}
    added = admin.add("j.smith", values)
    assert (added.service, added.action) == ("jsmith", "added")
    assert added.fields == ("netenable", "netpass", "netuser")
    assert added.cache_error is None
    assert "secret" not in repr(added)

    with pytest.raises(CredentialRecordExistsError):
        admin.add("jsmith", values)
    assert admin.read_non_secret(
        "j.smith", admin.annotate(admin.list_records())[0].profile
    ) == {"netuser": "jsmith"}

    updated = admin.update("jsmith", {"netpass": "new-secret"}, remove=["netenable"])
    assert (updated.fields, updated.removed) == (("netpass",), ("netenable",))
    assert admin.store.read("jsmith", ["netpass", "netenable"]) == {
        "netpass": "new-secret"
    }

    assert admin.delete("jsmith").action == "deleted"
    assert admin.list_records() == []
    with pytest.raises(CredentialRecordNotFoundError):
        admin.delete("jsmith")
    with pytest.raises(CredentialRecordNotFoundError):
        admin.update("jsmith", {"netpass": "x"})


def test_profile_rules_are_enforced(store_settings: CredentialSettings) -> None:
    admin = _ready(store_settings, StoreKind.SQLITE)
    with pytest.raises(ValueError, match="missing: netpass"):
        admin.add("ops", {"netuser": "ops"})
    with pytest.raises(ValueError, match="received: ibpass"):
        admin.add("infoblox", {"ibpass": "x"})
    admin.add(
        "infoblox",
        {"ibgrid": "gm.example.net", "ibuser": "api", "ibpass": "pw"},
        profile=INFOBLOX_PROFILE,
    )
    admin.add("ops", {"netuser": "ops", "netpass": "pw"})
    with pytest.raises(ValueError, match="required"):
        admin.update("ops", remove=["netpass"])
    with pytest.raises(ValueError, match="both set and remove"):
        admin.update("ops", {"netenable": "x"}, remove=["netenable"])
    with pytest.raises(ValueError, match="at least one field"):
        admin.update("ops")
    with pytest.raises(ValueError, match="whitespace"):
        admin.add("first last", {"netuser": "x", "netpass": "y"})


def test_annotations_explain_lookup_behavior(
    store_settings: CredentialSettings,
) -> None:
    sqlite = _ready(store_settings, StoreKind.SQLITE)
    sqlite.add("j.smith", {"netuser": "jsmith", "netpass": "db-pw"})
    sqlite.add("network-shared", {"netuser": "svc", "netpass": "pw"})
    sqlite.store.create("akumar", {"netuser": "akumar"})  # bypasses profile rules

    notes = {
        item.record.service: [(note.kind, note.text) for note in item.notes]
        for item in sqlite.annotate(sqlite.list_records(), operator="j_smith")
    }
    assert notes["jsmith"] == [(NoteKind.OPERATOR, "you")]
    assert notes["networkshared"] == [(NoteKind.SHARED, "shared fallback")]
    assert notes["akumar"] == [(NoteKind.INCOMPLETE, "missing netpass")]

    text_file = _ready(store_settings, StoreKind.FILE)
    text_file.add(
        "jsmith", {"netuser": "jsmith", "netpass": "file-pw", "netenable": "e"}
    )
    (item,) = text_file.annotate(text_file.list_records())
    # SQLite is consulted first, so only the fields it also holds are overridden.
    assert [(note.kind, note.text) for note in item.notes] == [
        (NoteKind.OVERRIDDEN, "SQLite overrides netpass,netuser")
    ]


@BOTH_KINDS
def test_listing_carries_the_note_but_never_secret_values(
    store_settings: CredentialSettings, kind: StoreKind
) -> None:
    admin = _ready(store_settings, kind)
    admin.add("ops", {"netuser": "ops", "netpass": "pw", "note": " DC2 jump host "})
    admin.add("lab", {"netuser": "lab", "netpass": "pw"})

    records = {record.service: record for record in admin.list_records()}
    assert records["ops"].visible == {"note": "DC2 jump host"}
    assert records["ops"].fields == ("netpass", "netuser", "note")
    assert records["lab"].visible == {}
    notes = {
        item.record.service: item.note for item in admin.annotate(records.values())
    }
    assert notes == {"ops": "DC2 jump host", "lab": None}

    admin.update("ops", remove=["note"])
    (ops,) = [record for record in admin.list_records() if record.service == "ops"]
    assert ops.visible == {}


def test_a_note_in_both_stores_is_not_reported_as_overridden(
    store_settings: CredentialSettings,
) -> None:
    sqlite = _ready(store_settings, StoreKind.SQLITE)
    sqlite.add("ops", {"netuser": "ops", "netpass": "a", "note": "in SQLite"})
    text_file = _ready(store_settings, StoreKind.FILE)
    text_file.add("ops", {"netuser": "ops", "netpass": "b", "note": "in the file"})

    (item,) = text_file.annotate(text_file.list_records())
    # ax.getkeys() never reads the note, so SQLite's note hides nothing.
    assert [note.text for note in item.notes] == ["SQLite overrides netpass,netuser"]


@BOTH_KINDS
def test_rotate_key_keeps_records_readable(
    store_settings: CredentialSettings, kind: StoreKind
) -> None:
    admin = _ready(store_settings, kind)
    admin.add("ops", {"netuser": "ops", "netpass": "pw"})
    key_file = admin.config.key_file
    assert key_file is not None
    old_key = key_file.read_text()
    assert admin.rotate_key() == 1
    assert key_file.read_text() != old_key
    assert admin.store.read("ops", ["netpass"]) == {"netpass": "pw"}
    assert admin.load_key_file(key_file) == base64.urlsafe_b64decode(
        key_file.read_text()
    )


class _FakeCache:
    """Stand-in for RedisCredentialCache that records or fails deletions."""

    deleted: ClassVar[list[str]] = []
    fail: ClassVar[bool] = False

    def __init__(self, settings: CredentialSettings) -> None:
        self.settings = settings

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool:
        return False

    def delete(self, service: str) -> None:
        if self.fail:
            raise CredentialBackendError("Redis is unreachable.")
        self.deleted.append(service)


def test_changes_clear_the_redis_cache_or_report_why_not(
    store_settings: CredentialSettings, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(admin_module, "RedisCredentialCache", _FakeCache)
    monkeypatch.setattr(_FakeCache, "deleted", [])
    settings = dataclasses.replace(
        store_settings, redis_enabled=True, redis_cache_ttl=60
    )
    admin = _ready(settings, StoreKind.SQLITE)

    admin.add("ops", {"netuser": "ops", "netpass": "pw"})
    assert _FakeCache.deleted == ["ops"]

    monkeypatch.setattr(_FakeCache, "fail", True)
    result = admin.update("ops", {"netpass": "new"})
    assert result.cache_error is not None
    assert "up to 60 seconds" in result.cache_error
    # The durable change still happened.
    assert admin.store.read("ops", ["netpass"]) == {"netpass": "new"}


def test_configured_kinds_follow_lookup_order(tmp_path: Path) -> None:
    both = load_settings(write_store_config(tmp_path), environ={})
    assert configured_kinds(both) == (StoreKind.SQLITE, StoreKind.FILE)
    only_file = load_settings(write_store_config(tmp_path, sqlite=False), environ={})
    assert configured_kinds(only_file) == (StoreKind.FILE,)
    assert configured_kinds(CredentialSettings()) == ()


def test_standalone_status_command(tmp_path: Path) -> None:
    config = write_store_config(tmp_path)
    output = StringIO()
    # Neither store exists yet, so the health check fails.
    assert admin_module.main(["--config", str(config)], stream=output) == 1
    assert "not-initialized" in output.getvalue()

    settings = load_settings(config, environ={})
    _ready(settings, StoreKind.SQLITE)
    _ready(settings, StoreKind.FILE)
    output = StringIO()
    assert admin_module.main(["--config", str(config), "--json"], stream=output) == 0
    payload = json.loads(output.getvalue())
    assert [(entry["store"], entry["state"]) for entry in payload] == [
        ("db", "ready"),
        ("file", "ready"),
    ]
