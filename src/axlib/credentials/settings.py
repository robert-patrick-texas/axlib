# Copyright 2026 Robert Patrick
# SPDX-License-Identifier: Apache-2.0

"""Load encrypted-file, SQLite, and Redis settings without embedding secrets.

Network-automation repositories are often readable by many engineers, so this
module keeps encryption keys, Redis passwords, and account names outside the
Python files.  Encrypted text-file and SQLite paths plus non-secret policy may
live in TOML, while AES-256 keys themselves come only from the runtime
environment or protected files. Relative key-file paths in TOML are resolved
beside the selected configuration file, giving scheduled jobs stable locations
even when their working directory differs from an operator's shell.  Settings may come
from an explicitly selected TOML file and are then overridden by environment
variables.  This makes local development simple while allowing production
schedulers to inject secrets at runtime.

Only the Python standard library is required.  Python 3.11 or newer supplies
:mod:`tomllib` for reading TOML safely.

Example:
    >>> import os
    >>> from axlib.credentials.settings import load_settings
    >>> os.environ["AXLIB_SHARED_SERVICE"] = "network-automation-shared"
    >>> settings = load_settings(environ=os.environ)
    >>> settings.shared_service
    'network-automation-shared'
"""

from __future__ import annotations

import os
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .exceptions import CredentialConfigurationError


@dataclass(frozen=True, slots=True)
class CredentialSettings:
    """Immutable settings used by credential managers and backend providers.

    Attributes:
        shared_service: Service name used by the backwards-compatible fallback.
        credential_file_enabled: Whether the AES-256-GCM text store participates
            in durable credential lookup.
        credential_file: Path to the axlib encrypted text credential file.
        credential_file_key: URL-safe Base64 AES-256 key supplied by the runtime
            environment.
        credential_file_key_file: Protected file containing the Base64 AES key.
        credential_file_lock_timeout: Seconds to wait for the cross-process file
            lock before reporting an operational error.
        credential_file_mode: POSIX mode enforced on credential and lock files.
        credential_file_key_file_mode: POSIX mode enforced on the AES key file.
        credential_file_owner: Optional POSIX owner name enforced on text-store
            files.
        credential_file_group: Optional POSIX group name; ``netops`` supports a
            shared Network Operations server by default.
        credential_file_enforce_permissions: Whether existing text-store files
            are checked and corrected before use.
        sqlite_enabled: Whether encrypted SQLite lookup is enabled ahead of the
            encrypted text-file store.
        sqlite_database: Path to the SQLite credential database.
        sqlite_key: URL-safe Base64 AES-256 key supplied by the environment.
        sqlite_key_file: Protected file containing the Base64 AES-256 key.
        sqlite_timeout: Seconds SQLite may wait for a concurrent writer lock.
        sqlite_database_mode: POSIX permission mode enforced on the database.
        sqlite_key_file_mode: POSIX permission mode enforced on the key file.
        sqlite_owner: Optional POSIX owner name enforced on SQLite files.
        sqlite_group: Optional POSIX group name enforced on SQLite files.
        sqlite_enforce_permissions: Whether existing SQLite files are checked
            and corrected before use.
        redis_enabled: Whether plaintext credential caching is enabled.
        redis_host: Redis server hostname or management IP address.
        redis_port: Redis TCP port.
        redis_db: Redis logical database number.
        redis_username: Optional Redis ACL username.
        redis_password: Optional Redis ACL password.
        redis_tls: Whether the Redis connection uses TLS.
        redis_ca_certs: Optional CA bundle used to verify the Redis certificate.
        redis_certfile: Optional client certificate for mutual TLS.
        redis_keyfile: Optional private key for the client certificate.
        redis_cert_reqs: Certificate verification policy accepted by redis-py.
        redis_connect_timeout: Seconds allowed to establish a Redis connection.
        redis_socket_timeout: Seconds allowed for an individual Redis operation.
        redis_cache_ttl: Seconds before cached plaintext credentials expire.
        redis_key_prefix: Optional namespace placed before the service key.
    """

    shared_service: str | None = None
    credential_file_enabled: bool = False
    credential_file: Path | None = None
    credential_file_key: str | None = None
    credential_file_key_file: Path | None = None
    credential_file_lock_timeout: float = 5.0
    credential_file_mode: int = 0o660
    credential_file_key_file_mode: int = 0o640
    credential_file_owner: str | None = None
    credential_file_group: str | None = "netops"
    credential_file_enforce_permissions: bool = True
    sqlite_enabled: bool = False
    sqlite_database: Path | None = None
    sqlite_key: str | None = None
    sqlite_key_file: Path | None = None
    sqlite_timeout: float = 5.0
    sqlite_database_mode: int = 0o660
    sqlite_key_file_mode: int = 0o640
    sqlite_owner: str | None = None
    sqlite_group: str | None = "netops"
    sqlite_enforce_permissions: bool = True
    redis_enabled: bool = False
    redis_host: str = "localhost"
    redis_port: int = 6379
    redis_db: int = 15
    redis_username: str | None = None
    redis_password: str | None = None
    redis_tls: bool = False
    redis_ca_certs: Path | None = None
    redis_certfile: Path | None = None
    redis_keyfile: Path | None = None
    redis_cert_reqs: str = "required"
    redis_connect_timeout: float = 2.0
    redis_socket_timeout: float = 2.0
    redis_cache_ttl: int = 900
    redis_key_prefix: str = ""


def parse_boolean(value: object, *, default: bool = False) -> bool:
    """Convert a configuration value into a predictable Boolean.

    Args:
        value (object): Value read from TOML, an environment variable, or a
            caller.  Common strings such as ``"yes"`` and ``"0"`` are accepted.
        default (bool): Result used when ``value`` is ``None`` or an empty string.

    Returns:
        bool: ``True`` or ``False`` after normalization.

    Raises:
        CredentialConfigurationError: If the value cannot be interpreted as a
            Boolean.  Failing clearly is safer than silently enabling a
            plaintext credential cache.
    """
    if value is None:
        return default
    if isinstance(value, bool):
        return value

    normalized = str(value).strip().casefold()
    if not normalized:
        return default
    if normalized in {"1", "true", "yes", "on", "enable", "enabled"}:
        return True
    if normalized in {"0", "false", "no", "off", "disable", "disabled"}:
        return False

    raise CredentialConfigurationError(f"Expected a Boolean value, received {value!r}.")


def optional_text(value: object) -> str | None:
    """Return non-blank text while treating empty configuration as missing.

    Args:
        value (object): Candidate text from TOML or the process environment.

    Returns:
        str | None: The original string when it contains non-whitespace text;
            otherwise ``None``.

    Raises:
        None: This helper intentionally accepts any object that can be rendered
            as text.
    """
    if value is None:
        return None
    text = str(value)
    return text if text.strip() else None


def optional_path(value: object) -> Path | None:
    """Convert a non-blank setting into an expanded filesystem path.

    Args:
        value (object): Path-like value, commonly an encrypted credential-store location
            or a TLS certificate path on an automation host.

    Returns:
        pathlib.Path | None: User-expanded path or ``None`` when the setting is
            blank.

    Raises:
        TypeError: If ``value`` cannot be converted to text by Python.
    """
    text = optional_text(value)
    return Path(text).expanduser() if text is not None else None


def parse_file_mode(
    value: object,
    *,
    name: str,
    default: int,
    require_owner_write: bool,
) -> int:
    """Parse a protected POSIX file mode from TOML or the environment.

    Human-facing configuration normally uses strings such as ``"0660"``.
    Treating those strings as octal avoids the ambiguity of decimal TOML
    integers and makes the setting match familiar ``chmod`` notation.

    Args:
        value (object): Octal text such as ``"0600"`` or an integer mode.
        name (str): Operator-facing setting name used in validation errors.
        default (int): Mode used when ``value`` is missing or blank.
        require_owner_write (bool): Require owner write permission.  SQLite
            databases need it, while a deployed key file may be read-only.

    Returns:
        int: Validated POSIX mode between ``0o000`` and ``0o777``.

    Raises:
        CredentialConfigurationError: If the value is not octal, grants any
            access to ``other``, includes executable bits, or omits required
            owner permissions.
    """
    if value is None or (isinstance(value, str) and not value.strip()):
        mode = default
    elif isinstance(value, bool):
        raise CredentialConfigurationError(
            f"{name} must be an octal file mode such as '0660'."
        )
    elif isinstance(value, int):
        mode = value
    else:
        text = str(value).strip().casefold()
        text = text.removeprefix("0o")
        try:
            mode = int(text, 8)
        except ValueError as exc:
            raise CredentialConfigurationError(
                f"{name} must be an octal file mode such as '0660'; received {value!r}."
            ) from exc

    if not 0 <= mode <= 0o777:
        raise CredentialConfigurationError(
            f"{name} must be between 0000 and 0777; received {value!r}."
        )
    if mode & 0o111:
        raise CredentialConfigurationError(
            f"{name} must not grant executable permission."
        )
    if mode & 0o007:
        raise CredentialConfigurationError(
            f"{name} must not grant access to users outside the owner/group."
        )
    if not mode & 0o400:
        raise CredentialConfigurationError(f"{name} must grant owner read access.")
    if require_owner_write and not mode & 0o200:
        raise CredentialConfigurationError(
            f"{name} must grant owner write access for SQLite updates."
        )
    return mode


def _select_path(
    environ: Mapping[str, str],
    env_name: str,
    table: Mapping[str, Any],
    table_name: str,
    default: object,
    *,
    config_directory: Path | None,
) -> Path | None:
    """Select a path and resolve TOML-relative values beside the config file.

    Args:
        environ (Mapping[str, str]): Environment mapping supplied by the caller.
        env_name (str): Environment variable that overrides the TOML value.
        table (Mapping[str, Any]): TOML table containing the lower-priority path.
        table_name (str): Key name inside ``table``.
        default (object): Value used when neither source defines the path.
        config_directory (pathlib.Path | None): Directory containing the selected
            TOML file, used only for relative TOML values.

    Returns:
        pathlib.Path | None: Expanded path or ``None`` when unset.  Environment
            paths retain normal process-relative behavior; TOML paths are made
            deterministic relative to the configuration file.

    Raises:
        TypeError: If a configured value cannot be converted to text.
        OSError: If the configuration directory cannot be resolved by the
            operating system.
    """
    if env_name in environ:
        return optional_path(environ[env_name])
    if table_name in table:
        selected = optional_path(table[table_name])
        if (
            selected is not None
            and not selected.is_absolute()
            and config_directory is not None
        ):
            return (config_directory / selected).resolve(strict=False)
        return selected
    return optional_path(default)


def _select_value(
    environ: Mapping[str, str],
    env_name: str,
    table: Mapping[str, Any],
    table_name: str,
    *,
    default: object,
) -> object:
    """Select one setting using environment-over-TOML precedence.

    Args:
        environ (Mapping[str, str]): Environment mapping supplied by the caller.
        env_name (str): Environment variable that overrides the TOML value.
        table (Mapping[str, Any]): TOML table containing the lower-priority value.
        table_name (str): Key name inside ``table``.
        default (object): Value used when neither source defines the setting.

    Returns:
        object: The selected raw value.

    Raises:
        None: Mapping lookups and the supplied default are intentionally simple.
    """
    if env_name in environ:
        return environ[env_name]
    if table_name in table:
        return table[table_name]
    return default


def _read_toml(path: Path | None, *, required: bool) -> dict[str, Any]:
    """Read a TOML configuration file when one has been selected.

    Args:
        path (pathlib.Path | None): File to read, or ``None`` when no file is in
            use.
        required (bool): Whether a missing selected file is an error.

    Returns:
        dict[str, Any]: Parsed TOML data, or an empty dictionary when no optional
            file exists.

    Raises:
        CredentialConfigurationError: If a required file is missing, unreadable,
            or invalid TOML.
    """
    if path is None:
        return {}
    if not path.exists():
        if required:
            raise CredentialConfigurationError(
                f"Credential configuration file does not exist: {path}"
            )
        return {}

    try:
        # A context manager guarantees that the descriptor is closed even when
        # TOML parsing fails, which matters on long-running automation workers.
        with path.open("rb") as handle:
            return tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise CredentialConfigurationError(
            f"Unable to read credential configuration file {path}: {exc}"
        ) from exc


def _mapping_table(data: Mapping[str, Any], name: str) -> Mapping[str, Any]:
    """Return a named TOML table after checking its basic type.

    Args:
        data (Mapping[str, Any]): Parsed top-level TOML mapping.
        name (str): Table name such as ``"credentials"`` or ``"redis"``.

    Returns:
        Mapping[str, Any]: The selected table, or an empty mapping if omitted.

    Raises:
        CredentialConfigurationError: If the TOML key exists but is not a table.
    """
    value = data.get(name, {})
    if not isinstance(value, Mapping):
        raise CredentialConfigurationError(
            f"TOML entry [{name}] must be a table, not {type(value).__name__}."
        )
    return value


def _as_int(value: object, *, name: str) -> int:
    """Convert a setting to an integer with an operator-friendly error.

    Args:
        value (object): Raw TOML or environment value.
        name (str): Setting name shown in an error message.

    Returns:
        int: Parsed integer.

    Raises:
        CredentialConfigurationError: If Python cannot parse the value as an
            integer.
    """
    try:
        # `value` is deliberately untyped raw TOML/environment input; int()
        # rejects unsupported types at runtime and the except clause below
        # turns that into a clear configuration error.
        return int(value)  # ty: ignore[invalid-argument-type]
    except (TypeError, ValueError) as exc:
        raise CredentialConfigurationError(
            f"{name} must be an integer, received {value!r}."
        ) from exc


def _as_float(value: object, *, name: str) -> float:
    """Convert a timeout setting to a floating-point number.

    Args:
        value (object): Raw TOML or environment value.
        name (str): Setting name shown in an error message.

    Returns:
        float: Parsed number of seconds.

    Raises:
        CredentialConfigurationError: If Python cannot parse the value as a
            number.
    """
    try:
        # `value` is deliberately untyped raw TOML/environment input; float()
        # rejects unsupported types at runtime and the except clause below
        # turns that into a clear configuration error.
        return float(value)  # ty: ignore[invalid-argument-type]
    except (TypeError, ValueError) as exc:
        raise CredentialConfigurationError(
            f"{name} must be a number, received {value!r}."
        ) from exc


def validate_settings(settings: CredentialSettings) -> CredentialSettings:
    """Validate ranges that could otherwise cause unsafe or confusing behavior.

    Args:
        settings (CredentialSettings): Candidate settings assembled from TOML,
            environment variables, or the backwards-compatible config module.

    Returns:
        CredentialSettings: The same immutable object after validation.

    Raises:
        CredentialConfigurationError: If encrypted-file, SQLite, or Redis settings
            are missing, unsafe, or outside their accepted ranges.
    """
    if settings.credential_file_lock_timeout <= 0:
        raise CredentialConfigurationError(
            "Credential-file lock timeout must be greater than 0."
        )
    parse_file_mode(
        settings.credential_file_mode,
        name="Credential-file mode",
        default=0o660,
        require_owner_write=True,
    )
    parse_file_mode(
        settings.credential_file_key_file_mode,
        name="Credential-file key-file mode",
        default=0o640,
        require_owner_write=False,
    )
    if settings.credential_file_enabled:
        if settings.credential_file is None:
            raise CredentialConfigurationError(
                "Credential-file lookup is enabled but no file is configured."
            )
        if (
            settings.credential_file_key is None
            and settings.credential_file_key_file is None
        ):
            raise CredentialConfigurationError(
                "Credential-file lookup is enabled but no encryption key source "
                "is configured."
            )

    if settings.sqlite_timeout <= 0:
        raise CredentialConfigurationError(
            "SQLite lock timeout must be greater than 0."
        )
    parse_file_mode(
        settings.sqlite_database_mode,
        name="SQLite database mode",
        default=0o660,
        require_owner_write=True,
    )
    parse_file_mode(
        settings.sqlite_key_file_mode,
        name="SQLite key-file mode",
        default=0o640,
        require_owner_write=False,
    )
    if settings.sqlite_enabled:
        if settings.sqlite_database is None:
            raise CredentialConfigurationError(
                "SQLite credential lookup is enabled but no database is configured."
            )
        if settings.sqlite_key is None and settings.sqlite_key_file is None:
            raise CredentialConfigurationError(
                "SQLite credential lookup is enabled but no encryption key source "
                "is configured."
            )

    if not settings.redis_host:
        raise CredentialConfigurationError("Redis host cannot be blank.")
    if not 1 <= settings.redis_port <= 65535:
        raise CredentialConfigurationError("Redis port must be between 1 and 65535.")
    if settings.redis_db < 0:
        raise CredentialConfigurationError("Redis database number must be >= 0.")
    if settings.redis_connect_timeout <= 0 or settings.redis_socket_timeout <= 0:
        raise CredentialConfigurationError("Redis timeouts must be greater than 0.")
    if settings.redis_cache_ttl < 0:
        raise CredentialConfigurationError("Redis cache TTL must be >= 0.")
    if settings.redis_cert_reqs not in {"required", "optional", "none"}:
        raise CredentialConfigurationError(
            "Redis TLS certificate policy must be required, optional, or none."
        )
    return settings


def load_settings(
    config_file: str | os.PathLike[str] | None = None,
    *,
    environ: Mapping[str, str] | None = None,
) -> CredentialSettings:
    """Load credential settings from TOML and environment variables.

    Environment variables take precedence over TOML.  A config file is used only
    when passed directly or named by ``AXLIB_CONFIG_FILE``; axlib does not scan
    arbitrary directories because deterministic startup is important for
    scheduled network jobs.

    Args:
        config_file (str | os.PathLike[str] | None): Optional explicit TOML file.
        environ (Mapping[str, str] | None): Environment mapping, primarily useful
            for tests.  Defaults to :data:`os.environ`.

    Returns:
        CredentialSettings: Validated immutable runtime settings.

    Raises:
        CredentialConfigurationError: If TOML is invalid or a setting has an
            unsupported type or range.
    """
    environment = os.environ if environ is None else environ
    selected_from_env = optional_text(environment.get("AXLIB_CONFIG_FILE"))
    selected_path = optional_path(config_file or selected_from_env)
    data = _read_toml(selected_path, required=selected_path is not None)
    config_directory = (
        selected_path.expanduser().resolve(strict=False).parent
        if selected_path is not None
        else None
    )
    credential_table = _mapping_table(data, "credentials")
    credential_file_table = _mapping_table(data, "credential_file")
    sqlite_table = _mapping_table(data, "sqlite")
    redis_table = _mapping_table(data, "redis")

    settings = CredentialSettings(
        shared_service=optional_text(
            _select_value(
                environment,
                "AXLIB_SHARED_SERVICE",
                credential_table,
                "shared_service",
                default=None,
            )
        ),
        credential_file_enabled=parse_boolean(
            _select_value(
                environment,
                "AXLIB_CREDENTIAL_FILE_ENABLE",
                credential_file_table,
                "enabled",
                default=False,
            )
        ),
        credential_file=_select_path(
            environment,
            "AXLIB_CREDENTIAL_FILE",
            credential_file_table,
            "file",
            None,
            config_directory=config_directory,
        ),
        # Direct AES keys are environment-only. Reusable TOML may point at a
        # protected key file but should not contain secret key material.
        credential_file_key=optional_text(environment.get("AXLIB_CREDENTIAL_FILE_KEY")),
        credential_file_key_file=_select_path(
            environment,
            "AXLIB_CREDENTIAL_FILE_KEY_FILE",
            credential_file_table,
            "key_file",
            None,
            config_directory=config_directory,
        ),
        credential_file_lock_timeout=_as_float(
            _select_value(
                environment,
                "AXLIB_CREDENTIAL_FILE_LOCK_TIMEOUT",
                credential_file_table,
                "lock_timeout",
                default=5.0,
            ),
            name="Credential-file lock timeout",
        ),
        credential_file_mode=parse_file_mode(
            _select_value(
                environment,
                "AXLIB_CREDENTIAL_FILE_MODE",
                credential_file_table,
                "file_mode",
                default="0660",
            ),
            name="Credential-file mode",
            default=0o660,
            require_owner_write=True,
        ),
        credential_file_key_file_mode=parse_file_mode(
            _select_value(
                environment,
                "AXLIB_CREDENTIAL_FILE_KEY_FILE_MODE",
                credential_file_table,
                "key_file_mode",
                default="0640",
            ),
            name="Credential-file key-file mode",
            default=0o640,
            require_owner_write=False,
        ),
        credential_file_owner=optional_text(
            _select_value(
                environment,
                "AXLIB_CREDENTIAL_FILE_OWNER",
                credential_file_table,
                "owner",
                default=None,
            )
        ),
        credential_file_group=optional_text(
            _select_value(
                environment,
                "AXLIB_CREDENTIAL_FILE_GROUP",
                credential_file_table,
                "group",
                default="netops",
            )
        ),
        credential_file_enforce_permissions=parse_boolean(
            _select_value(
                environment,
                "AXLIB_CREDENTIAL_FILE_ENFORCE_PERMISSIONS",
                credential_file_table,
                "enforce_permissions",
                default=True,
            ),
            default=True,
        ),
        sqlite_enabled=parse_boolean(
            _select_value(
                environment,
                "AXLIB_SQLITE_ENABLE",
                sqlite_table,
                "enabled",
                default=False,
            )
        ),
        sqlite_database=_select_path(
            environment,
            "AXLIB_SQLITE_DATABASE",
            sqlite_table,
            "database",
            None,
            config_directory=config_directory,
        ),
        # The direct AES key is intentionally environment-only.  A reusable TOML
        # file may identify a protected key file but must not contain the key.
        sqlite_key=optional_text(environment.get("AXLIB_SQLITE_KEY")),
        sqlite_key_file=_select_path(
            environment,
            "AXLIB_SQLITE_KEY_FILE",
            sqlite_table,
            "key_file",
            None,
            config_directory=config_directory,
        ),
        sqlite_timeout=_as_float(
            _select_value(
                environment,
                "AXLIB_SQLITE_TIMEOUT",
                sqlite_table,
                "timeout",
                default=5.0,
            ),
            name="SQLite lock timeout",
        ),
        sqlite_database_mode=parse_file_mode(
            _select_value(
                environment,
                "AXLIB_SQLITE_DATABASE_MODE",
                sqlite_table,
                "database_mode",
                default="0660",
            ),
            name="SQLite database mode",
            default=0o660,
            require_owner_write=True,
        ),
        sqlite_key_file_mode=parse_file_mode(
            _select_value(
                environment,
                "AXLIB_SQLITE_KEY_FILE_MODE",
                sqlite_table,
                "key_file_mode",
                default="0640",
            ),
            name="SQLite key-file mode",
            default=0o640,
            require_owner_write=False,
        ),
        sqlite_owner=optional_text(
            _select_value(
                environment,
                "AXLIB_SQLITE_OWNER",
                sqlite_table,
                "owner",
                default=None,
            )
        ),
        sqlite_group=optional_text(
            _select_value(
                environment,
                "AXLIB_SQLITE_GROUP",
                sqlite_table,
                "group",
                default="netops",
            )
        ),
        sqlite_enforce_permissions=parse_boolean(
            _select_value(
                environment,
                "AXLIB_SQLITE_ENFORCE_PERMISSIONS",
                sqlite_table,
                "enforce_permissions",
                default=True,
            ),
            default=True,
        ),
        redis_enabled=parse_boolean(
            _select_value(
                environment,
                "AXLIB_REDIS_ENABLE",
                redis_table,
                "enabled",
                default=False,
            )
        ),
        redis_host=str(
            _select_value(
                environment,
                "AXLIB_REDIS_HOST",
                redis_table,
                "host",
                default="localhost",
            )
        ).strip(),
        redis_port=_as_int(
            _select_value(
                environment,
                "AXLIB_REDIS_PORT",
                redis_table,
                "port",
                default=6379,
            ),
            name="Redis port",
        ),
        redis_db=_as_int(
            _select_value(
                environment,
                "AXLIB_REDIS_DB",
                redis_table,
                "db",
                default=15,
            ),
            name="Redis database",
        ),
        redis_username=optional_text(
            _select_value(
                environment,
                "AXLIB_REDIS_USERNAME",
                redis_table,
                "username",
                default=None,
            )
        ),
        # Like AES credential keys, a Redis password is accepted only from the
        # environment to keep reusable configuration files non-secret.
        redis_password=optional_text(environment.get("AXLIB_REDIS_PASSWORD")),
        redis_tls=parse_boolean(
            _select_value(
                environment,
                "AXLIB_REDIS_TLS",
                redis_table,
                "tls",
                default=False,
            )
        ),
        redis_ca_certs=optional_path(
            _select_value(
                environment,
                "AXLIB_REDIS_CA_CERTS",
                redis_table,
                "ca_certs",
                default=None,
            )
        ),
        redis_certfile=optional_path(
            _select_value(
                environment,
                "AXLIB_REDIS_CERTFILE",
                redis_table,
                "certfile",
                default=None,
            )
        ),
        redis_keyfile=optional_path(
            _select_value(
                environment,
                "AXLIB_REDIS_KEYFILE",
                redis_table,
                "keyfile",
                default=None,
            )
        ),
        redis_cert_reqs=str(
            _select_value(
                environment,
                "AXLIB_REDIS_CERT_REQS",
                redis_table,
                "cert_reqs",
                default="required",
            )
        )
        .strip()
        .casefold(),
        redis_connect_timeout=_as_float(
            _select_value(
                environment,
                "AXLIB_REDIS_CONNECT_TIMEOUT",
                redis_table,
                "connect_timeout",
                default=2.0,
            ),
            name="Redis connect timeout",
        ),
        redis_socket_timeout=_as_float(
            _select_value(
                environment,
                "AXLIB_REDIS_SOCKET_TIMEOUT",
                redis_table,
                "socket_timeout",
                default=2.0,
            ),
            name="Redis socket timeout",
        ),
        redis_cache_ttl=_as_int(
            _select_value(
                environment,
                "AXLIB_REDIS_CACHE_TTL",
                redis_table,
                "cache_ttl",
                default=900,
            ),
            name="Redis cache TTL",
        ),
        redis_key_prefix=str(
            _select_value(
                environment,
                "AXLIB_REDIS_KEY_PREFIX",
                redis_table,
                "key_prefix",
                default="",
            )
        )
        .strip()
        .strip(":"),
    )
    return validate_settings(settings)
