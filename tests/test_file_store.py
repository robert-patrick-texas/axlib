"""Tests for axlib's AES-256-GCM encrypted text credential store."""

from __future__ import annotations

import base64
import os
from pathlib import Path

import pytest

from axlib.credentials import (
    CredentialBackendError,
    CredentialConfigurationError,
    CredentialFileStore,
    CredentialRecordExistsError,
    CredentialRecordNotFoundError,
    CredentialSettings,
    generate_credential_file_key_file,
)


def _settings(tmp_path: Path, *, key: str | None = None) -> CredentialSettings:
    value = key or base64.urlsafe_b64encode(os.urandom(32)).decode("ascii")
    return CredentialSettings(
        credential_file_enabled=True,
        credential_file=tmp_path / "credentials.axc",
        credential_file_key=value,
        credential_file_mode=0o600,
        credential_file_key_file_mode=0o600,
        credential_file_group=None,
    )


def test_file_store_full_crud_and_plaintext_is_absent(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    store = CredentialFileStore(settings)
    store.initialize()
    store.create(
        "operator",
        {
            "netuser": "operator-login",
            "netpass": "login-password-unique",
            "netenable": "enable-password-unique",
        },
    )

    assert store.read("operator", ("netuser", "netpass", "netenable")) == {
        "netuser": "operator-login",
        "netpass": "login-password-unique",
        "netenable": "enable-password-unique",
    }
    raw = settings.credential_file.read_bytes()  # type: ignore[union-attr]
    assert b"operator-login" not in raw
    assert b"login-password-unique" not in raw
    assert b"enable-password-unique" not in raw

    store.update("operator", {"netpass": "rotated-password"})
    store.write("operator", {"netenable": "rotated-enable"})
    assert store.read("operator", ("netpass", "netenable")) == {
        "netpass": "rotated-password",
        "netenable": "rotated-enable",
    }

    records = store.list_records()
    assert len(records) == 1
    assert records[0].service == "operator"
    assert records[0].fields == ("netenable", "netpass", "netuser")

    store.delete("operator", ("netenable",))
    assert store.read("operator", ("netenable",)) == {}
    assert store.delete_service("operator") is True
    assert store.delete_service("operator") is False


def test_file_store_strict_create_and_update(tmp_path: Path) -> None:
    store = CredentialFileStore(_settings(tmp_path))
    store.initialize()
    store.create("operator", {"netuser": "one"})
    with pytest.raises(CredentialRecordExistsError):
        store.create("operator", {"netuser": "two"})
    with pytest.raises(CredentialRecordNotFoundError):
        store.update("missing", {"netpass": "value"})


def test_file_store_wrong_key_is_rejected(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    CredentialFileStore(settings).initialize()
    wrong = base64.urlsafe_b64encode(os.urandom(32)).decode("ascii")
    wrong_settings = _settings(tmp_path, key=wrong)
    with pytest.raises(CredentialBackendError, match="key verification failed"):
        CredentialFileStore(wrong_settings).initialize()


def test_file_store_unsupported_version_is_not_migrated(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    store = CredentialFileStore(settings)
    store.initialize()
    path = settings.credential_file
    assert path is not None
    text = path.read_text(encoding="utf-8").replace("version = 1", "version = 99")
    path.write_text(text, encoding="utf-8")
    os.chmod(path, 0o600)
    with pytest.raises(CredentialBackendError, match="No automatic migration"):
        store.initialize()


def test_file_store_keyring_style_convenience_methods(tmp_path: Path) -> None:
    """Greenfield callers can use familiar password methods without keyring."""
    store = CredentialFileStore(_settings(tmp_path))
    store.initialize()
    store.set_password("operator", "netpass", "secret")
    assert store.get_password("operator", "netpass") == "secret"
    assert store.get_password("operator", "missing") is None
    store.delete_password("operator", "netpass")
    assert store.get_password("operator", "netpass") is None


def _key_file_settings(tmp_path: Path) -> CredentialSettings:
    """Build settings backed by a key file so the key can be rotated in place."""
    key_file = tmp_path / "credentials.key"
    generate_credential_file_key_file(key_file, group=None, mode=0o600)
    return CredentialSettings(
        credential_file_enabled=True,
        credential_file=tmp_path / "credentials.axc",
        credential_file_key_file=key_file,
        credential_file_mode=0o600,
        credential_file_key_file_mode=0o600,
        credential_file_group=None,
    )


def test_file_store_rotate_key_reencrypts_and_replaces_key_file(
    tmp_path: Path,
) -> None:
    settings = _key_file_settings(tmp_path)
    key_file = settings.credential_file_key_file
    assert key_file is not None
    store = CredentialFileStore(settings)
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

    # A fresh store instance re-reads the replaced key file and finds the data.
    fresh_store = CredentialFileStore(
        CredentialSettings(
            credential_file_enabled=True,
            credential_file=settings.credential_file,
            credential_file_key_file=key_file,
            credential_file_mode=0o600,
            credential_file_key_file_mode=0o600,
            credential_file_group=None,
        )
    )
    assert fresh_store.read("operator", ("netuser", "netpass")) == {
        "netuser": "bob",
        "netpass": "s3cret",
    }
    assert fresh_store.read("other", ("netuser", "netpass")) == {
        "netuser": "alice",
        "netpass": "hunter2",
    }

    # The old key can no longer authenticate the rotated file.
    old_key_store = CredentialFileStore(
        CredentialSettings(
            credential_file_enabled=True,
            credential_file=settings.credential_file,
            credential_file_key=old_key_text.strip(),
            credential_file_mode=0o600,
            credential_file_key_file_mode=0o600,
            credential_file_group=None,
        )
    )
    with pytest.raises(CredentialBackendError, match="key verification failed"):
        old_key_store.initialize()


def test_file_store_rotate_key_requires_key_file_not_environment(
    tmp_path: Path,
) -> None:
    settings = CredentialSettings(
        credential_file_enabled=True,
        credential_file=tmp_path / "credentials.axc",
        credential_file_key=base64.urlsafe_b64encode(os.urandom(32)).decode("ascii"),
        credential_file_mode=0o600,
        credential_file_group=None,
    )
    store = CredentialFileStore(settings)
    store.initialize()
    with pytest.raises(CredentialConfigurationError, match="AXLIB_CREDENTIAL_FILE_KEY"):
        store.rotate_key(os.urandom(32))


def test_file_store_rotate_key_rejects_wrong_length_key(tmp_path: Path) -> None:
    settings = _key_file_settings(tmp_path)
    store = CredentialFileStore(settings)
    store.initialize()
    with pytest.raises(CredentialConfigurationError, match="32 bytes"):
        store.rotate_key(os.urandom(16))


def test_file_store_rotate_key_blocks_when_staging_file_remains(
    tmp_path: Path,
) -> None:
    settings = _key_file_settings(tmp_path)
    store = CredentialFileStore(settings)
    store.initialize()
    key_file = settings.credential_file_key_file
    assert key_file is not None
    staged = key_file.with_name(key_file.name + ".rotating")
    staged.write_text("leftover-from-an-interrupted-rotation\n", encoding="ascii")
    with pytest.raises(CredentialConfigurationError, match="staging file"):
        store.rotate_key(os.urandom(32))
