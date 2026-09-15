# Copyright 2026 Robert Patrick
# SPDX-License-Identifier: Apache-2.0

"""Backwards-compatible credential configuration facade.

Older axlib scripts import module attributes such as ``redis_host`` and
encrypted text-file, SQLite, and Redis settings.  The facade sources these
values from :mod:`axlib.credentials.settings`; no encryption key, account name,
or site-specific path is embedded in the package.

New code should prefer ``axlib.credentials.load_settings()``.  Existing code may
continue to import and, where necessary for tests, monkeypatch these attributes.
Environment variables and an optional TOML file are documented in
``docs/CREDENTIALS.md``.

Dependencies:
    Python 3.11 or newer.  No Redis or cryptography package is imported here.

Example:
    >>> import axlib.config as config
    >>> config.redis_enable in {True, False}
    True
    >>> settings = config.as_settings()
    >>> settings.redis_host == config.redis_host
    True
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path

from .credentials.exceptions import CredentialConfigurationError
from .credentials.settings import (
    CredentialSettings,
    load_settings,
    optional_path,
    optional_text,
    parse_boolean,
    parse_file_mode,
    validate_settings,
)

shared_service: str | None
credential_file_enable: bool
credential_file: str | None
credential_file_key: str | None
credential_file_key_file: str | None
credential_file_lock_timeout: float
credential_file_mode: int
credential_file_key_file_mode: int
credential_file_owner: str | None
credential_file_group: str | None
credential_file_enforce_permissions: bool
sqlite_enable: bool
sqlite_database: str | None
sqlite_key: str | None
sqlite_key_file: str | None
sqlite_timeout: float
sqlite_database_mode: int
sqlite_key_file_mode: int
sqlite_owner: str | None
sqlite_group: str | None
sqlite_enforce_permissions: bool
redis_enable: bool
redis_host: str
redis_port: int
redis_db: int
redis_username: str | None
redis_password: str | None
redis_tls: bool
redis_ca_certs: str | None
redis_certfile: str | None
redis_keyfile: str | None
redis_cert_reqs: str
redis_connect_timeout: float
redis_socket_timeout: float
redis_cache_ttl: int
redis_key_prefix: str
_current_settings: CredentialSettings


def _path_text(path: Path | None) -> str | None:
    """Convert an optional path to the legacy string representation.

    Args:
        path (pathlib.Path | None): Path from modern credential settings.

    Returns:
        str | None: String path for older callers, or ``None`` when unset.

    Raises:
        None: ``Path.__str__`` is deterministic for an existing Path object.
    """
    return str(path) if path is not None else None


def _publish(settings: CredentialSettings) -> None:
    """Publish immutable settings through historical module-level names.

    Args:
        settings (CredentialSettings): Validated modern settings object.

    Returns:
        None: Module globals are updated in place for backwards compatibility.

    Raises:
        None: The function performs direct assignments only.
    """
    global _current_settings
    global shared_service
    global credential_file_enable, credential_file, credential_file_key
    global credential_file_key_file, credential_file_lock_timeout
    global credential_file_mode, credential_file_key_file_mode
    global credential_file_owner, credential_file_group
    global credential_file_enforce_permissions
    global sqlite_enable, sqlite_database, sqlite_key, sqlite_key_file
    global sqlite_timeout, sqlite_database_mode, sqlite_key_file_mode
    global sqlite_owner, sqlite_group, sqlite_enforce_permissions
    global redis_enable, redis_host, redis_port, redis_db
    global redis_username, redis_password, redis_tls
    global redis_ca_certs, redis_certfile, redis_keyfile, redis_cert_reqs
    global redis_connect_timeout, redis_socket_timeout
    global redis_cache_ttl, redis_key_prefix

    _current_settings = settings
    shared_service = settings.shared_service
    credential_file_enable = settings.credential_file_enabled
    credential_file = _path_text(settings.credential_file)
    credential_file_key = settings.credential_file_key
    credential_file_key_file = _path_text(settings.credential_file_key_file)
    credential_file_lock_timeout = settings.credential_file_lock_timeout
    credential_file_mode = settings.credential_file_mode
    credential_file_key_file_mode = settings.credential_file_key_file_mode
    credential_file_owner = settings.credential_file_owner
    credential_file_group = settings.credential_file_group
    credential_file_enforce_permissions = settings.credential_file_enforce_permissions
    sqlite_enable = settings.sqlite_enabled
    sqlite_database = _path_text(settings.sqlite_database)
    sqlite_key = settings.sqlite_key
    sqlite_key_file = _path_text(settings.sqlite_key_file)
    sqlite_timeout = settings.sqlite_timeout
    sqlite_database_mode = settings.sqlite_database_mode
    sqlite_key_file_mode = settings.sqlite_key_file_mode
    sqlite_owner = settings.sqlite_owner
    sqlite_group = settings.sqlite_group
    sqlite_enforce_permissions = settings.sqlite_enforce_permissions
    redis_enable = settings.redis_enabled
    redis_host = settings.redis_host
    redis_port = settings.redis_port
    redis_db = settings.redis_db
    redis_username = settings.redis_username
    redis_password = settings.redis_password
    redis_tls = settings.redis_tls
    redis_ca_certs = _path_text(settings.redis_ca_certs)
    redis_certfile = _path_text(settings.redis_certfile)
    redis_keyfile = _path_text(settings.redis_keyfile)
    redis_cert_reqs = settings.redis_cert_reqs
    redis_connect_timeout = settings.redis_connect_timeout
    redis_socket_timeout = settings.redis_socket_timeout
    redis_cache_ttl = settings.redis_cache_ttl
    redis_key_prefix = settings.redis_key_prefix


def refresh(
    config_file: str | os.PathLike[str] | None = None,
    *,
    environ: Mapping[str, str] | None = None,
) -> CredentialSettings:
    """Reload module attributes from TOML and environment variables.

    Args:
        config_file (str | os.PathLike[str] | None): Optional explicit TOML
            configuration path.
        environ (Mapping[str, str] | None): Environment mapping, useful for tests;
            defaults to the current process environment.

    Returns:
        CredentialSettings: Newly loaded immutable settings.

    Raises:
        CredentialConfigurationError: Propagated from ``load_settings`` when an
            operator supplies invalid configuration.
    """
    settings = load_settings(config_file, environ=environ)
    _publish(settings)
    return settings


def as_settings() -> CredentialSettings:
    """Build modern settings from the current legacy module attributes.

    This function intentionally reads the public module variables each time.
    Existing test suites and deployment wrappers sometimes monkeypatch
    ``axlib.config`` after import, and honoring those values preserves that
    behavior without making new code depend on mutable globals.

    Args:
        None: Values are read from this module's public attributes.

    Returns:
        CredentialSettings: Validated immutable snapshot.

    Raises:
        CredentialConfigurationError: If a monkeypatched value has an invalid
            file mode, port, timeout, TTL, or TLS policy.
    """
    try:
        settings = CredentialSettings(
            shared_service=optional_text(shared_service),
            credential_file_enabled=parse_boolean(credential_file_enable),
            credential_file=optional_path(credential_file),
            credential_file_key=optional_text(credential_file_key),
            credential_file_key_file=optional_path(credential_file_key_file),
            credential_file_lock_timeout=float(credential_file_lock_timeout),
            credential_file_mode=parse_file_mode(
                credential_file_mode,
                name="Credential-file mode",
                default=0o660,
                require_owner_write=True,
            ),
            credential_file_key_file_mode=parse_file_mode(
                credential_file_key_file_mode,
                name="Credential-file key-file mode",
                default=0o640,
                require_owner_write=False,
            ),
            credential_file_owner=optional_text(credential_file_owner),
            credential_file_group=optional_text(credential_file_group),
            credential_file_enforce_permissions=parse_boolean(
                credential_file_enforce_permissions,
                default=True,
            ),
            sqlite_enabled=parse_boolean(sqlite_enable),
            sqlite_database=optional_path(sqlite_database),
            sqlite_key=optional_text(sqlite_key),
            sqlite_key_file=optional_path(sqlite_key_file),
            sqlite_timeout=float(sqlite_timeout),
            sqlite_database_mode=parse_file_mode(
                sqlite_database_mode,
                name="SQLite database mode",
                default=0o660,
                require_owner_write=True,
            ),
            sqlite_key_file_mode=parse_file_mode(
                sqlite_key_file_mode,
                name="SQLite key-file mode",
                default=0o640,
                require_owner_write=False,
            ),
            sqlite_owner=optional_text(sqlite_owner),
            sqlite_group=optional_text(sqlite_group),
            sqlite_enforce_permissions=parse_boolean(
                sqlite_enforce_permissions,
                default=True,
            ),
            redis_enabled=parse_boolean(redis_enable),
            redis_host=str(redis_host).strip(),
            redis_port=int(redis_port),
            redis_db=int(redis_db),
            redis_username=optional_text(redis_username),
            redis_password=optional_text(redis_password),
            redis_tls=parse_boolean(redis_tls),
            redis_ca_certs=optional_path(redis_ca_certs),
            redis_certfile=optional_path(redis_certfile),
            redis_keyfile=optional_path(redis_keyfile),
            redis_cert_reqs=str(redis_cert_reqs).strip().casefold(),
            redis_connect_timeout=float(redis_connect_timeout),
            redis_socket_timeout=float(redis_socket_timeout),
            redis_cache_ttl=int(redis_cache_ttl),
            redis_key_prefix=str(redis_key_prefix).strip().strip(":"),
        )
    except (TypeError, ValueError) as exc:
        # Wrapping primitive conversion errors gives operators one stable axlib
        # exception type instead of leaking a low-level int() or float() message.
        raise CredentialConfigurationError(
            f"Invalid value in legacy axlib.config settings: {exc}"
        ) from exc
    return validate_settings(settings)


# Loading once at import preserves the simple attribute-based interface used by
# existing automation scripts, while refresh() remains available to test or
# reload a changed environment explicitly.
refresh()
