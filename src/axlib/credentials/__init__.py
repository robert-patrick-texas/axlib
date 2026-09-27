# Copyright 2026 Robert Patrick
# SPDX-License-Identifier: Apache-2.0

"""Credential settings and encrypted storage providers for axlib.

Network automation scripts often need the same credential lookup behavior across
SSH, HTTPS, and API workflows.  This package separates the stable
:func:`axlib.getkeys` interface from storage: optional Redis caching, preferred
AES-256-GCM SQLite storage, and an axlib-native AES-256-GCM encrypted text file.
Both durable stores expose parallel CRUD methods so engineers can move between
them without learning different programming patterns, and :class:`StoreAdmin`
(:mod:`axlib.credentials.admin`) wraps either store with the administration
rules shared by the CLIs and the optional TUI.

No connection or credential file is opened at import time.  Redis is imported
only when caching is enabled, while durable encryption uses ``cryptography``.
The administration names (``StoreAdmin``, profiles, and friends) are loaded on
first use; see :func:`__getattr__`.
Unsupported text-file or SQLite schema versions are rejected; axlib does not
include migration logic for older storage formats.

Example:
    >>> from axlib.credentials import CredentialSettings, lookup_values
    >>> settings = CredentialSettings()
    >>> lookup_values("operator", {"netuser": "demo"}, settings=settings)
    {'netuser': 'demo'}
"""

from importlib import import_module
from typing import TYPE_CHECKING

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
from .manager import (
    lookup_values,
    merge_missing,
    normalize_legacy_service_name,
    normalize_service_for_write,
)
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

if TYPE_CHECKING:
    # Type checkers and editors see these names as ordinary imports; at run
    # time they are provided lazily by __getattr__ below.
    from .admin import (
        AnnotatedRecord,
        ChangeResult,
        NoteKind,
        RecordNote,
        StoreAdmin,
        StoreKind,
        StoreStatus,
        configured_kinds,
        prepare_update,
    )
    from .profiles import (
        INFOBLOX_PROFILE,
        NETWORK_PROFILE,
        PROFILES,
        FieldSpec,
        RecordProfile,
        get_profile,
        validate_values,
    )

# Name -> submodule for exports that are imported on first use.  Loading them
# lazily keeps ``python -m axlib.credentials.admin`` (and ``.profiles``) clean:
# if this package imported those modules eagerly, running one as a script
# would load it twice -- once here and once as ``__main__`` -- and Python
# would warn that the two copies may behave unpredictably.
_LAZY_EXPORTS = {
    "AnnotatedRecord": "admin",
    "ChangeResult": "admin",
    "NoteKind": "admin",
    "RecordNote": "admin",
    "StoreAdmin": "admin",
    "StoreKind": "admin",
    "StoreStatus": "admin",
    "configured_kinds": "admin",
    "prepare_update": "admin",
    "INFOBLOX_PROFILE": "profiles",
    "NETWORK_PROFILE": "profiles",
    "PROFILES": "profiles",
    "FieldSpec": "profiles",
    "RecordProfile": "profiles",
    "get_profile": "profiles",
    "validate_values": "profiles",
}


def __getattr__(name: str) -> object:
    """Import an administration export the first time it is requested.

    Python calls a module-level ``__getattr__`` (PEP 562) only for names the
    module does not already define, which makes it a simple lazy-import hook.

    Args:
        name (str): Attribute requested from :mod:`axlib.credentials`.

    Returns:
        object: The class, function, or constant from its submodule.

    Raises:
        AttributeError: If ``name`` is not a known export.
    """
    module_name = _LAZY_EXPORTS.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(import_module(f".{module_name}", __name__), name)
    # Caching the value means __getattr__ runs only once per name.
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    """Include lazily exported names in interactive attribute listings.

    Args:
        None: Python calls this function without arguments.

    Returns:
        list[str]: Sorted module attributes including lazy exports.

    Raises:
        None: Set and list operations are deterministic.
    """
    return sorted(set(globals()) | set(__all__))


__all__ = [
    "INFOBLOX_PROFILE",
    "NETWORK_PROFILE",
    "PROFILES",
    "AnnotatedRecord",
    "ChangeResult",
    "CredentialBackendError",
    "CredentialConfigurationError",
    "CredentialDependencyError",
    "CredentialError",
    "CredentialFileRecord",
    "CredentialFileStore",
    "CredentialRecordExistsError",
    "CredentialRecordNotFoundError",
    "CredentialSettings",
    "FieldSpec",
    "NoteKind",
    "RecordNote",
    "RecordProfile",
    "RedisCredentialCache",
    "SQLiteCredentialRecord",
    "SQLiteCredentialStore",
    "StoreAdmin",
    "StoreKind",
    "StoreStatus",
    "configured_kinds",
    "generate_credential_file_key_file",
    "generate_sqlite_key_file",
    "get_profile",
    "load_settings",
    "lookup_values",
    "merge_missing",
    "normalize_legacy_service_name",
    "normalize_service_for_write",
    "parse_file_mode",
    "prepare_update",
    "read_credential_file_key",
    "read_sqlite_key",
    "validate_settings",
    "validate_values",
]
