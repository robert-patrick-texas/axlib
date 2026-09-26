# Copyright 2026 Robert Patrick
# SPDX-License-Identifier: Apache-2.0

"""Coordinate credential precedence across overrides, cache, and stores.

A network script may receive some values from environment variables, find other
values in a fast Redis cache, and retrieve the remaining fields from durable
encrypted storage.  This module keeps the order explicit:

1. Caller-supplied non-``None`` values.
2. Redis hash fields, when caching is enabled.
3. The AES-256-GCM SQLite database, when enabled.
4. The axlib AES-256-GCM encrypted text file for fields still missing.
5. Newly decrypted durable values written back to Redis with an optional TTL.

SQLite is checked before the encrypted text file when both are enabled.
``axlib.getkeys()`` callers and the configured shared-account fallback remain
independent of the storage choice. Backend failures are reported without
exposing values, and lookup continues to the next configured durable source.

Example:
    >>> from axlib.credentials.manager import merge_missing
    >>> merge_missing({"netuser": None}, {"netuser": "ops-user"})
    {'netuser': 'ops-user'}
"""

from __future__ import annotations

import sys
from collections.abc import Callable, Mapping, MutableMapping, Sequence
from contextlib import ExitStack
from typing import Protocol, Self, TextIO

from .exceptions import CredentialError
from .file_store import CredentialFileStore
from .providers import RedisCredentialCache
from .settings import CredentialSettings
from .sqlite_store import SQLiteCredentialStore

Reporter = Callable[[str], None]


class CacheProvider(Protocol):
    """Structural interface used by the manager and lightweight test doubles."""

    def __enter__(self) -> Self:
        """Enter the cache context.

        Args:
            None: Implementations use their own settings.

        Returns:
            Self: Active provider.

        Raises:
            CredentialError: If the cache cannot be opened.
        """
        ...

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: object,
    ) -> bool:
        """Exit the cache context and release resources.

        Args:
            exc_type (type[BaseException] | None): Managed exception class.
            exc (BaseException | None): Managed exception instance.
            traceback (object): Managed traceback.

        Returns:
            bool: Whether the implementation suppresses an exception.

        Raises:
            None: Implementations should make cleanup best-effort.
        """
        ...

    def read(self, service: str, fields: Sequence[str]) -> dict[str, str]:
        """Read selected cached fields.

        Args:
            service (str): Normalized credential service name.
            fields (Sequence[str]): Requested field names.

        Returns:
            dict[str, str]: Cached values that exist.

        Raises:
            CredentialError: If the cache operation fails.
        """
        ...

    def write(self, service: str, values: Mapping[str, str]) -> None:
        """Write selected fields to the cache.

        Args:
            service (str): Normalized credential service name.
            values (Mapping[str, str]): Plaintext values to cache.

        Returns:
            None: The provider is modified in place.

        Raises:
            CredentialError: If the cache operation fails.
        """
        ...


class StoreProvider(Protocol):
    """Structural interface for encrypted stores and test doubles."""

    def read(self, service: str, fields: Sequence[str]) -> dict[str, str]:
        """Read selected durable credential fields.

        Args:
            service (str): Normalized credential service name.
            fields (Sequence[str]): Requested field names.

        Returns:
            dict[str, str]: Decrypted values that exist.

        Raises:
            CredentialError: If the durable store cannot be read.
        """
        ...


def _default_durable_stores(
    settings: CredentialSettings,
) -> list[tuple[str, StoreProvider]]:
    """Build the configured durable lookup chain in precedence order.

    Args:
        settings (CredentialSettings): Runtime configuration selecting SQLite
            and the AES-256-GCM encrypted-file path.

    Returns:
        list[tuple[str, StoreProvider]]: Human-readable backend labels paired
            with providers. SQLite appears first when enabled, followed by the
            encrypted text store when enabled.

    Raises:
        None: Providers defer path, dependency, and key validation until read.
    """
    stores: list[tuple[str, StoreProvider]] = []
    if settings.sqlite_enabled:
        stores.append(("SQLite", SQLiteCredentialStore(settings)))
    if settings.credential_file_enabled:
        stores.append(("Encrypted file", CredentialFileStore(settings)))
    return stores


def normalize_legacy_service_name(service: object = "default") -> str:
    """Apply the historical axlib service-name transformation.

    Existing encrypted files and Redis hashes were written after removing dots,
    dashes, and underscores from service names.  The transformation is retained
    so deployed scripts and data continue to work.  New deployments should use
    service names that remain unique after normalization.

    Args:
        service (object): Operator username or logical credential service.

    Returns:
        str: Legacy-compatible lookup key with ``.``, ``-``, and ``_`` removed.

    Raises:
        None: Any object is converted to text for backwards compatibility.
    """
    text = str(service if service is not None else "default")
    return text.replace(".", "").replace("-", "").replace("_", "")


def normalize_service_for_write(service: str) -> str:
    """Validate and normalize a service before changing credential storage.

    Lookups are forgiving (``ax.getkeys("first.last")`` simply normalizes the
    name), but writes should be strict: a stored service name must be one that a
    later lookup will find again.  Every administration path -- the Python
    :class:`axlib.credentials.admin.StoreAdmin` API, both CLIs, and the TUI --
    calls this one function so they cannot disagree about what a valid name is.

    Args:
        service (str): Operator name or logical service, such as ``first.last``
            or ``network-shared``.

    Returns:
        str: Lookup-compatible service key with dots, dashes, and underscores
            removed.

    Raises:
        ValueError: If the service is blank, contains whitespace, or becomes
            blank after normalization.
    """
    candidate = service.strip()
    if not candidate:
        raise ValueError("Service name cannot be blank.")
    if any(character.isspace() for character in candidate):
        raise ValueError("Service name cannot contain whitespace.")

    # Keeping one normalization rule across Redis, SQLite, and encrypted-file
    # storage prevents a script from looking up a different record simply
    # because the durable backend changed.
    normalized = normalize_legacy_service_name(candidate)
    if not normalized:
        raise ValueError(
            "Service name must contain characters other than '.', '-', or '_'."
        )
    return normalized


def merge_missing(
    destination: MutableMapping[str, str | None],
    source: Mapping[str, str | None],
) -> MutableMapping[str, str | None]:
    """Fill only ``None`` values in a destination mapping.

    Args:
        destination (MutableMapping[str, str | None]): Higher-priority values,
            modified in place.
        source (Mapping[str, str | None]): Lower-priority candidate values.

    Returns:
        MutableMapping[str, str | None]: The same destination mapping after
            missing fields are filled.

    Raises:
        None: Standard mapping operations are used without coercing values.
    """
    for field, value in source.items():
        if field in destination and destination[field] is None and value is not None:
            destination[field] = value
    return destination


def _missing_fields(values: Mapping[str, str | None]) -> list[str]:
    """Return field names whose values still need to be resolved.

    Args:
        values (Mapping[str, str | None]): Current credential values.

    Returns:
        list[str]: Field names mapped to ``None``.

    Raises:
        None: The function performs a mapping comprehension only.
    """
    return [field for field, value in values.items() if value is None]


def stderr_reporter(
    message: str,
    *,
    stream: TextIO | None = None,
) -> None:
    """Write an axlib credential warning to standard error.

    Args:
        message (str): Operator-facing error text that must not include secret
            values.
        stream (TextIO | None): Destination stream, injectable for tests;
            defaults to the current standard error stream.

    Returns:
        None: Text is written for the invoking operator.

    Raises:
        OSError: If the output stream cannot be written.
    """
    print(f"***AX: {message}", file=sys.stderr if stream is None else stream)


def lookup_values(
    service: object = "default",
    values: Mapping[str, str | None] | None = None,
    *,
    settings: CredentialSettings,
    cache: CacheProvider | None = None,
    store: StoreProvider | None = None,
    reporter: Reporter = stderr_reporter,
) -> dict[str, str | None]:
    """Resolve fields through overrides, Redis, SQLite, and encrypted-file lookup.

    Args:
        service (object): Operator username or logical credential service.
        values (Mapping[str, str | None] | None): Requested fields and any
            caller-provided overrides.  A default ``secret`` field is used when
            the mapping is empty.
        settings (CredentialSettings): Runtime credential and Redis settings.
        cache (CacheProvider | None): Optional cache implementation for tests or
            custom integrations.  Defaults to Redis when enabled.
        store (StoreProvider | None): Optional single durable provider for
            tests or custom integrations.  When omitted, configured SQLite is
            tried before :class:`CredentialFileStore` when both are enabled.
        reporter (Reporter): Function receiving non-secret operational warnings.

    Returns:
        dict[str, str | None]: All requested fields, preserving unresolved values
            as ``None`` for backwards compatibility.

    Raises:
        None: Backend failures are reported and treated as misses so an existing
            automation script can continue to a lower-priority source.
    """
    service_key = normalize_legacy_service_name(service)
    resolved: dict[str, str | None] = dict(values or {"secret": None})
    durable_stores = (
        [("Custom", store)] if store is not None else _default_durable_stores(settings)
    )
    active_cache: CacheProvider | None = None

    # ExitStack gives the manager one unconditional cleanup path even when the
    # Redis read, durable-store read, or Redis write fails midway through a lookup.
    with ExitStack() as resources:
        if settings.redis_enabled:
            candidate_cache = cache or RedisCredentialCache(settings)
            try:
                active_cache = resources.enter_context(candidate_cache)
                cached = active_cache.read(service_key, _missing_fields(resolved))
                merge_missing(resolved, cached)
            except CredentialError as exc:
                reporter(f"Redis credential cache unavailable: {exc}")
                active_cache = None

        from_durable: dict[str, str] = {}
        for label, durable_store in durable_stores:
            needed = _missing_fields(resolved)
            if not needed:
                break
            try:
                found = durable_store.read(service_key, needed)
                merge_missing(resolved, found)
                from_durable.update(found)
            except CredentialError as exc:
                reporter(f"{label} credential lookup failed: {exc}")

        if active_cache is not None and from_durable:
            try:
                active_cache.write(service_key, from_durable)
            except CredentialError as exc:
                reporter(f"Redis credential cache update failed: {exc}")

    return resolved
