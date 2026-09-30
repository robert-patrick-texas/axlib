# Copyright 2026 Robert Patrick
# SPDX-License-Identifier: Apache-2.0

"""Tests for external credential configuration and Redis TLS settings."""

from __future__ import annotations

from pathlib import Path

import pytest

import axlib.config as legacy_config
from axlib.credentials import settings as settings_module
from axlib.credentials.exceptions import CredentialConfigurationError
from axlib.credentials.providers import RedisCredentialCache
from axlib.credentials.settings import (
    default_config_file,
    installed_config_file,
    load_settings,
    parse_boolean,
    parse_file_mode,
)


def test_toml_and_environment_precedence(tmp_path: Path) -> None:
    config_file = tmp_path / "axlib.toml"
    config_file.write_text(
        """
[credentials]
shared_service = "toml-shared"

[credential_file]
enabled = true
file = "/from/toml/credentials.axc"
key_file = "/from/toml/key"
key = "must-be-ignored"

[redis]
enabled = true
host = "redis.toml.example"
port = 6380
db = 4
tls = true
ca_certs = "/ca.pem"
cache_ttl = 300
key_prefix = "team:credentials"
""",
        encoding="utf-8",
    )
    environment = {
        "AXLIB_REDIS_HOST": "redis.env.example",
        "AXLIB_SHARED_SERVICE": "env-shared",
        "AXLIB_CREDENTIAL_FILE_KEY": "runtime-key",
        "AXLIB_REDIS_PASSWORD": "runtime-password",
    }

    settings = load_settings(config_file, environ=environment)

    assert settings.credential_file_enabled is True
    assert settings.credential_file == Path("/from/toml/credentials.axc")
    assert settings.credential_file_key_file == Path("/from/toml/key")
    assert settings.credential_file_key == "runtime-key"
    assert settings.shared_service == "env-shared"
    assert settings.redis_enabled is True
    assert settings.redis_host == "redis.env.example"
    assert settings.redis_tls is True
    assert settings.redis_password == "runtime-password"  # noqa: S105
    assert settings.redis_cache_ttl == 300
    assert settings.redis_key_prefix == "team:credentials"


def test_redis_tls_options_are_passed_to_client() -> None:
    settings = load_settings(
        environ={
            "AXLIB_REDIS_TLS": "true",
            "AXLIB_REDIS_CA_CERTS": "/ca.pem",
            "AXLIB_REDIS_CERTFILE": "/client.pem",
            "AXLIB_REDIS_KEYFILE": "/client.key",
            "AXLIB_REDIS_USERNAME": "acl-user",
            "AXLIB_REDIS_PASSWORD": "secret",
        }
    )
    options = RedisCredentialCache(settings)._client_options()  # noqa: SLF001
    assert options["ssl"] is True
    assert options["ssl_cert_reqs"] == "required"
    assert options["ssl_ca_certs"] == "/ca.pem"
    assert options["ssl_certfile"] == "/client.pem"
    assert options["ssl_keyfile"] == "/client.key"
    assert options["username"] == "acl-user"
    assert options["password"] == "secret"  # noqa: S105


@pytest.mark.parametrize(
    ("value", "expected"),
    [("yes", True), ("0", False), (True, True), (None, False)],
)
def test_parse_boolean(value: object, expected: bool) -> None:
    assert parse_boolean(value) is expected


def test_invalid_boolean_fails_closed() -> None:
    with pytest.raises(CredentialConfigurationError):
        parse_boolean("sometimes")


def test_selected_missing_toml_is_an_error(tmp_path: Path) -> None:
    with pytest.raises(CredentialConfigurationError):
        load_settings(tmp_path / "missing.toml", environ={})


def test_invalid_legacy_config_value_uses_credential_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(legacy_config, "redis_port", "not-a-port")
    with pytest.raises(CredentialConfigurationError):
        legacy_config.as_settings()


def test_sqlite_settings_and_runtime_key_precedence(tmp_path: Path) -> None:
    config_file = tmp_path / "axlib.toml"
    config_file.write_text(
        """
[sqlite]
enabled = true
database = "/var/lib/axlib/credentials.db"
key_file = "/etc/axlib/sqlite.key"
key = "must-be-ignored"
timeout = 3.5
database_mode = "0600"
key_file_mode = "0600"
group = ""
enforce_permissions = false
""",
        encoding="utf-8",
    )

    settings = load_settings(
        config_file,
        environ={"AXLIB_SQLITE_KEY": "runtime-base64-key"},
    )

    assert settings.sqlite_enabled is True
    assert settings.sqlite_database == Path("/var/lib/axlib/credentials.db")
    assert settings.sqlite_key_file == Path("/etc/axlib/sqlite.key")
    assert settings.sqlite_key == "runtime-base64-key"
    assert settings.sqlite_timeout == 3.5
    assert settings.sqlite_database_mode == 0o600
    assert settings.sqlite_key_file_mode == 0o600
    assert settings.sqlite_group is None
    assert settings.sqlite_enforce_permissions is False


def test_enabled_sqlite_requires_database_and_key_source(tmp_path: Path) -> None:
    config_file = tmp_path / "axlib.toml"
    config_file.write_text("[sqlite]\nenabled = true\n", encoding="utf-8")

    with pytest.raises(CredentialConfigurationError, match="no database"):
        load_settings(config_file, environ={})


def test_relative_sqlite_paths_are_resolved_from_configuration_directory(
    tmp_path: Path,
) -> None:
    config_directory = tmp_path / "etc" / "axlib"
    config_directory.mkdir(parents=True)
    config_file = config_directory / "axlib.toml"
    config_file.write_text(
        """
[sqlite]
enabled = true
database = "../state/credentials.db"
key_file = "sqlite-aes256.key"
""",
        encoding="utf-8",
    )

    settings = load_settings(config_file, environ={})

    expected_database = (config_directory / "../state/credentials.db").resolve()
    assert settings.sqlite_database == expected_database
    assert settings.sqlite_key_file == config_directory / "sqlite-aes256.key"
    assert settings.sqlite_database_mode == 0o660
    assert settings.sqlite_key_file_mode == 0o640
    assert settings.sqlite_group == "netops"


@pytest.mark.parametrize(
    ("value", "expected"),
    [("0660", 0o660), ("0o600", 0o600), (0o640, 0o640)],
)
def test_parse_file_mode_accepts_protected_octal_values(
    value: object,
    expected: int,
) -> None:
    assert (
        parse_file_mode(
            value,
            name="test mode",
            default=0o600,
            require_owner_write=False,
        )
        == expected
    )


@pytest.mark.parametrize("value", ["0666", "0770", "not-octal"])
def test_parse_file_mode_rejects_world_or_executable_access(value: object) -> None:
    with pytest.raises(CredentialConfigurationError):
        parse_file_mode(
            value,
            name="test mode",
            default=0o600,
            require_owner_write=True,
        )


def _write_record(directory: Path, body: str) -> Path:
    """Write an install.sh-style record and return its path."""
    record = directory / "install.env"
    record.write_text(body, encoding="utf-8")
    return record


def test_installed_config_file_reads_the_shell_record(tmp_path: Path) -> None:
    record = _write_record(
        tmp_path,
        "# Written by axlib install.sh\n"
        "PREFIX='/opt/shared/axlib'\n"
        "CONFIG_FILE='/srv/axlib old/axlib.toml'\n"
        "CONFIG_FILE='/srv/axlib/axlib.toml'  # the last assignment wins\n",
    )

    assert installed_config_file(record) == Path("/srv/axlib/axlib.toml")


@pytest.mark.parametrize("body", ["PREFIX='/opt/shared/axlib'\n", "CONFIG_FILE=''\n"])
def test_installed_config_file_without_a_value_is_none(
    tmp_path: Path, body: str
) -> None:
    assert installed_config_file(_write_record(tmp_path, body)) is None


def test_missing_or_unreadable_record_is_none(tmp_path: Path) -> None:
    assert installed_config_file(tmp_path / "missing.env") is None
    assert installed_config_file(tmp_path) is None  # a directory cannot be read


def test_malformed_record_is_a_configuration_error(tmp_path: Path) -> None:
    record = _write_record(tmp_path, "CONFIG_FILE='/etc/axlib/axlib.toml\n")

    with pytest.raises(CredentialConfigurationError, match="install record"):
        installed_config_file(record)


def test_config_file_lookup_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    recorded = tmp_path / "recorded.toml"
    recorded.write_text('[credentials]\nshared_service = "recorded"\n')
    from_env = tmp_path / "from-env.toml"
    from_env.write_text('[credentials]\nshared_service = "from-env"\n')
    explicit = tmp_path / "explicit.toml"
    explicit.write_text('[credentials]\nshared_service = "explicit"\n')
    record = _write_record(tmp_path, f"CONFIG_FILE='{recorded}'\n")
    monkeypatch.setattr(settings_module, "INSTALL_RECORD", record)
    environment = {"AXLIB_CONFIG_FILE": str(from_env)}

    assert default_config_file({}) == recorded
    assert default_config_file(environment) == from_env
    assert load_settings(environ={}).shared_service == "recorded"
    assert load_settings(environ=environment).shared_service == "from-env"
    assert load_settings(explicit, environ=environment).shared_service == "explicit"


def test_recorded_config_file_that_is_missing_is_an_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    record = _write_record(tmp_path, f"CONFIG_FILE='{tmp_path / 'gone.toml'}'\n")
    monkeypatch.setattr(settings_module, "INSTALL_RECORD", record)

    with pytest.raises(CredentialConfigurationError):
        load_settings(environ={})
