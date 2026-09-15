"""Credential settings and encrypted storage providers for axlib.

Network automation scripts often need the same credential lookup behavior across
SSH, HTTPS, and API workflows.  This package separates the stable
:func:`axlib.getkeys` interface from storage: optional Redis caching, preferred
AES-256-GCM SQLite storage, and an axlib-native AES-256-GCM encrypted text file.
Both durable stores expose parallel CRUD methods so engineers can move between
them without learning different programming patterns.

No connection or credential file is opened at import time.  Redis is imported
only when caching is enabled, while durable encryption uses ``cryptography``.
Unsupported text-file or SQLite schema versions are rejected; axlib 1.0.0 does
not include migration logic for older storage formats.

Example:
    >>> from axlib.credentials import CredentialSettings, lookup_values
    >>> settings = CredentialSettings()
    >>> lookup_values("operator", {"netuser": "demo"}, settings=settings)
    {'netuser': 'demo'}
"""

from .exceptions import (
    CredentialBackendError,
    CredentialConfigurationError,
    CredentialDependencyError,
    CredentialError,
    CredentialRecordExistsError,
    CredentialRecordNotFoundError,
)
from .file_store import (
    CredentialFileRecord,
    CredentialFileStore,
    generate_credential_file_key_file,
    read_credential_file_key,
)
from .manager import lookup_values, merge_missing, normalize_legacy_service_name
from .providers import RedisCredentialCache
from .settings import (
    CredentialSettings,
    load_settings,
    parse_file_mode,
    validate_settings,
)
from .sqlite_store import (
    SQLiteCredentialRecord,
    SQLiteCredentialStore,
    generate_sqlite_key_file,
    read_sqlite_key,
)

__all__ = [
    "CredentialBackendError",
    "CredentialConfigurationError",
    "CredentialDependencyError",
    "CredentialError",
    "CredentialFileRecord",
    "CredentialFileStore",
    "CredentialRecordExistsError",
    "CredentialRecordNotFoundError",
    "CredentialSettings",
    "RedisCredentialCache",
    "SQLiteCredentialRecord",
    "SQLiteCredentialStore",
    "generate_credential_file_key_file",
    "generate_sqlite_key_file",
    "load_settings",
    "lookup_values",
    "merge_missing",
    "normalize_legacy_service_name",
    "parse_file_mode",
    "read_credential_file_key",
    "read_sqlite_key",
    "validate_settings",
]
