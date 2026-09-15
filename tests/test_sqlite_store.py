# Copyright 2026 Robert Patrick
# SPDX-License-Identifier: Apache-2.0

"""Tests for AES-256-GCM SQLite credential storage."""

from __future__ import annotations

import grp
import os
import sqlite3
from contextlib import closing
from pathlib import Path

import pytest

from axlib.credentials.exceptions import (
    CredentialBackendError,
    CredentialConfigurationError,
    CredentialRecordExistsError,
    CredentialRecordNotFoundError,
)
from axlib.credentials.settings import CredentialSettings
from axlib.credentials.sqlite_store import (
    SQLiteCredentialStore,
    generate_sqlite_key_file,
)


def _current_group() -> str:
    """Return an existing group name for permission tests in the test runner."""
    return grp.getgrgid(os.getgid()).gr_name


def _settings(tmp_path: Path) -> CredentialSettings:
    group = _current_group()
    key_file = tmp_path / "sqlite.key"
    generate_sqlite_key_file(key_file, group=group)
    return CredentialSettings(
        sqlite_enabled=True,
        sqlite_database=tmp_path / "credentials.db",
        sqlite_key_file=key_file,
        sqlite_group=group,
    )


def test_sqlite_store_crud_encrypts_values_at_rest(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    store = SQLiteCredentialStore(settings)
    store.initialize()
    store.initialize()

    store.create(
        "firstlast",
        {
            "netuser": "operator-login",
            "netpass": "login-password",
            "netenable": "enable-password",
        },
    )
    assert store.read("firstlast", ["netuser", "netpass", "missing"]) == {
        "netuser": "operator-login",
        "netpass": "login-password",
    }

    store.update("firstlast", {"netpass": "rotated-password"})
    assert store.read(
        "firstlast",
        ["netuser", "netpass", "netenable"],
    ) == {
        "netuser": "operator-login",
        "netpass": "rotated-password",
        "netenable": "enable-password",
    }

    records = store.list_records()
    assert len(records) == 1
    assert records[0].service == "firstlast"
    assert records[0].fields == ("netenable", "netpass", "netuser")

    database_bytes = settings.sqlite_database.read_bytes()
    assert b"operator-login" not in database_bytes
    assert b"login-password" not in database_bytes
    assert b"enable-password" not in database_bytes
    assert settings.sqlite_database.stat().st_mode & 0o777 == 0o660
    assert settings.sqlite_key_file.stat().st_mode & 0o777 == 0o640
    assert settings.sqlite_database.stat().st_gid == os.getgid()
    assert settings.sqlite_key_file.stat().st_gid == os.getgid()

    assert store.delete_service("firstlast") is True
    assert store.delete_service("firstlast") is False
    assert store.list_records() == []


def test_sqlite_store_create_update_and_field_delete_errors(tmp_path: Path) -> None:
    store = SQLiteCredentialStore(_settings(tmp_path))
    store.initialize()
    store.create("operator", {"netuser": "user", "netpass": "pass"})

    with pytest.raises(CredentialRecordExistsError):
        store.create("operator", {"netuser": "other", "netpass": "other"})
    with pytest.raises(CredentialRecordNotFoundError):
        store.update("missing", {"netpass": "new"})

    store.delete("operator", ["netpass"])
    assert store.read("operator", ["netuser", "netpass"]) == {"netuser": "user"}
    store.delete("operator", ["netuser"])
    assert store.read("operator", ["netuser"]) == {}


def test_sqlite_store_requires_initialization_before_crud(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    store = SQLiteCredentialStore(settings)

    with pytest.raises(CredentialBackendError, match="credential-db init"):
        store.create("operator", {"netuser": "user", "netpass": "pass"})

    assert settings.sqlite_database.exists() is False


def test_sqlite_store_atomic_write_creates_then_merges(tmp_path: Path) -> None:
    store = SQLiteCredentialStore(_settings(tmp_path))
    store.initialize()

    store.write("operator", {"netuser": "user", "netpass": "first"})
    store.write("operator", {"netpass": "second", "netenable": "enable"})

    assert store.read("operator", ["netuser", "netpass", "netenable"]) == {
        "netuser": "user",
        "netpass": "second",
        "netenable": "enable",
    }


def test_sqlite_database_and_key_file_must_be_distinct(tmp_path: Path) -> None:
    shared_path = tmp_path / "do-not-share-this-path"
    shared_path.write_text("not-a-real-key", encoding="ascii")
    store = SQLiteCredentialStore(
        CredentialSettings(
            sqlite_enabled=True,
            sqlite_database=shared_path,
            sqlite_key_file=shared_path,
        )
    )

    with pytest.raises(CredentialConfigurationError, match="must be different"):
        store.initialize()


def test_sqlite_store_rejects_wrong_key_and_tampered_ciphertext(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    store = SQLiteCredentialStore(settings)
    store.initialize()
    store.create("operator", {"netuser": "user", "netpass": "pass"})

    wrong_key_file = tmp_path / "wrong.key"
    generate_sqlite_key_file(wrong_key_file, group=_current_group())
    wrong_store = SQLiteCredentialStore(
        CredentialSettings(
            sqlite_enabled=True,
            sqlite_database=settings.sqlite_database,
            sqlite_key_file=wrong_key_file,
            sqlite_group=_current_group(),
        )
    )
    with pytest.raises(CredentialBackendError, match="key validation failed"):
        wrong_store.read("operator", ["netpass"])

    with closing(sqlite3.connect(settings.sqlite_database)) as connection:
        row = connection.execute(
            "SELECT ciphertext FROM credential_records WHERE service = ?",
            ("operator",),
        ).fetchone()
        changed = bytearray(row[0])
        changed[-1] ^= 1
        connection.execute(
            "UPDATE credential_records SET ciphertext = ? WHERE service = ?",
            (bytes(changed), "operator"),
        )
        connection.commit()

    with pytest.raises(CredentialBackendError, match="authentication failed"):
        store.read("operator", ["netpass"])


def test_generate_key_refuses_to_overwrite_existing_key(tmp_path: Path) -> None:
    key_file = tmp_path / "sqlite.key"
    first = generate_sqlite_key_file(key_file, group=_current_group())

    with pytest.raises(CredentialConfigurationError, match="already exists"):
        generate_sqlite_key_file(key_file, group=_current_group())

    assert key_file.read_text(encoding="ascii").strip() == first


def test_sqlite_store_supports_private_0600_files(tmp_path: Path) -> None:
    key_file = tmp_path / "private.key"
    generate_sqlite_key_file(key_file, mode=0o600, group=None)
    settings = CredentialSettings(
        sqlite_enabled=True,
        sqlite_database=tmp_path / "private.db",
        sqlite_key_file=key_file,
        sqlite_database_mode=0o600,
        sqlite_key_file_mode=0o600,
        sqlite_group=None,
    )

    store = SQLiteCredentialStore(settings)
    store.initialize()

    assert settings.sqlite_database.stat().st_mode & 0o777 == 0o600
    assert key_file.stat().st_mode & 0o777 == 0o600


def test_sqlite_store_repairs_permission_drift_when_enforced(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    store = SQLiteCredentialStore(settings)
    store.initialize()
    settings.sqlite_database.chmod(0o600)
    settings.sqlite_key_file.chmod(0o600)

    store.write("operator", {"netuser": "user", "netpass": "pass"})

    assert settings.sqlite_database.stat().st_mode & 0o777 == 0o660
    assert settings.sqlite_key_file.stat().st_mode & 0o777 == 0o640


def test_package_created_shared_directories_use_setgid(tmp_path: Path) -> None:
    group = _current_group()
    key_file = tmp_path / "shared-config" / "sqlite.key"
    database = tmp_path / "shared-state" / "credentials.db"
    generate_sqlite_key_file(key_file, group=group)
    settings = CredentialSettings(
        sqlite_enabled=True,
        sqlite_database=database,
        sqlite_key_file=key_file,
        sqlite_group=group,
    )

    SQLiteCredentialStore(settings).initialize()

    assert key_file.parent.stat().st_mode & 0o7777 == 0o2750
    assert database.parent.stat().st_mode & 0o7777 == 0o2770
    assert key_file.parent.stat().st_gid == os.getgid()
    assert database.parent.stat().st_gid == os.getgid()


def test_permission_enforcement_can_be_delegated_to_external_acl_policy(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    store = SQLiteCredentialStore(settings)
    store.initialize()
    store.create("operator", {"netuser": "user", "netpass": "pass"})
    settings.sqlite_database.chmod(0o600)
    settings.sqlite_key_file.chmod(0o600)

    externally_managed = CredentialSettings(
        sqlite_enabled=True,
        sqlite_database=settings.sqlite_database,
        sqlite_key_file=settings.sqlite_key_file,
        sqlite_group=settings.sqlite_group,
        sqlite_enforce_permissions=False,
    )

    assert SQLiteCredentialStore(externally_managed).read(
        "operator",
        ["netuser"],
    ) == {"netuser": "user"}
    assert settings.sqlite_database.stat().st_mode & 0o777 == 0o600
    assert settings.sqlite_key_file.stat().st_mode & 0o777 == 0o600


def test_key_generation_rejects_unknown_group_without_leaving_a_file(
    tmp_path: Path,
) -> None:
    key_file = tmp_path / "sqlite.key"

    with pytest.raises(CredentialConfigurationError, match="group does not exist"):
        generate_sqlite_key_file(
            key_file,
            group="axlib-group-that-must-not-exist",
        )

    assert key_file.exists() is False


def test_sqlite_rollback_journal_inherits_shared_mode_and_group(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    store = SQLiteCredentialStore(settings)
    store.initialize()
    store.create("operator", {"netuser": "user", "netpass": "pass"})
    journal = Path(f"{settings.sqlite_database}-journal")

    with closing(
        sqlite3.connect(settings.sqlite_database, isolation_level=None)
    ) as connection:
        connection.execute("PRAGMA journal_mode = DELETE")
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            "UPDATE credential_records SET updated_at = ? WHERE service = ?",
            ("journal-permission-test", "operator"),
        )

        assert journal.exists() is True
        assert journal.stat().st_mode & 0o777 == 0o660
        assert journal.stat().st_gid == os.getgid()
        connection.rollback()

    assert journal.exists() is False


def test_sqlite_initialize_rejects_unsupported_schema_without_migration(
    tmp_path: Path,
) -> None:
    """Existing databases with another schema version must never be upgraded."""
    settings = _settings(tmp_path)
    store = SQLiteCredentialStore(settings)
    store.initialize()

    with closing(sqlite3.connect(settings.sqlite_database)) as connection:
        connection.execute("PRAGMA user_version = 99")
        connection.commit()

    with pytest.raises(
        CredentialBackendError, match="Unsupported SQLite credential schema"
    ):
        store.initialize()


def test_sqlite_store_rotate_key_reencrypts_and_replaces_key_file(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    key_file = settings.sqlite_key_file
    assert key_file is not None
    store = SQLiteCredentialStore(settings)
    store.initialize()
    store.create("operator", {"netuser": "bob", "netpass": "s3cret"})
    store.create("other", {"netuser": "alice", "netpass": "hunter2"})
    old_key_text = key_file.read_text()

    new_key = os.urandom(32)
    assert store.rotate_key(new_key) == 2

    new_key_text = key_file.read_text()
    assert new_key_text != old_key_text
    staged = key_file.with_name(key_file.name + ".rotating")
    assert staged.exists() is False

    fresh_store = SQLiteCredentialStore(
        CredentialSettings(
            sqlite_enabled=True,
            sqlite_database=settings.sqlite_database,
            sqlite_key_file=key_file,
            sqlite_group=settings.sqlite_group,
        )
    )
    assert fresh_store.read("operator", ["netuser", "netpass"]) == {
        "netuser": "bob",
        "netpass": "s3cret",
    }
    assert fresh_store.read("other", ["netuser", "netpass"]) == {
        "netuser": "alice",
        "netpass": "hunter2",
    }

    old_key_store = SQLiteCredentialStore(
        CredentialSettings(
            sqlite_enabled=True,
            sqlite_database=settings.sqlite_database,
            sqlite_key=old_key_text.strip(),
            sqlite_group=settings.sqlite_group,
        )
    )
    with pytest.raises(CredentialBackendError, match="key validation failed"):
        old_key_store.read("operator", ["netuser"])


def test_sqlite_store_rotate_key_requires_key_file_not_environment(
    tmp_path: Path,
) -> None:
    key = generate_sqlite_key_file(tmp_path / "unused.key", group=_current_group())
    settings = CredentialSettings(
        sqlite_enabled=True,
        sqlite_database=tmp_path / "credentials.db",
        sqlite_key=key,
    )
    store = SQLiteCredentialStore(settings)
    store.initialize()
    with pytest.raises(CredentialConfigurationError, match="AXLIB_SQLITE_KEY"):
        store.rotate_key(os.urandom(32))


def test_sqlite_store_rotate_key_rejects_wrong_length_key(tmp_path: Path) -> None:
    store = SQLiteCredentialStore(_settings(tmp_path))
    store.initialize()
    with pytest.raises(CredentialConfigurationError, match="32 bytes"):
        store.rotate_key(os.urandom(16))


def test_sqlite_store_rotate_key_blocks_when_staging_file_remains(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    key_file = settings.sqlite_key_file
    assert key_file is not None
    store = SQLiteCredentialStore(settings)
    store.initialize()
    staged = key_file.with_name(key_file.name + ".rotating")
    staged.write_text("leftover-from-an-interrupted-rotation\n", encoding="ascii")
    with pytest.raises(CredentialConfigurationError, match="staging file"):
        store.rotate_key(os.urandom(32))
