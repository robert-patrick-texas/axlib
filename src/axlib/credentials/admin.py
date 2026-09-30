# Copyright 2026 Robert Patrick
# SPDX-License-Identifier: Apache-2.0

"""Python API for administering axlib's encrypted credential stores.

``ax.getkeys()`` *reads* credentials.  This module *maintains* them: initialize
a store, add, update, or delete service records, rotate the AES key, and report
store health.  :class:`StoreAdmin` is the programming interface for that work;
both administration CLIs (``axlib credential-db`` and ``axlib credential-file``)
and the optional terminal UI are thin front ends over it.

Keeping the rules here rather than in each front end means a change applies
everywhere at once:

* service names are normalized exactly as ``ax.getkeys()`` will look them up;
* allowed and required fields come from :mod:`axlib.credentials.profiles`;
* the Redis cache entry for a service is cleared after every change, so an
  automation run never keeps using an old password from the cache;
* no method returns or prints a secret value.

Run the module directly for a health check suitable for cron or monitoring.
The exit status is ``0`` only when every checked store is ready::

    python -m axlib.credentials.admin --config /etc/axlib/axlib.toml
    python -m axlib.credentials.admin --json | jq '.[] | select(.ready | not)'

Dependencies:
    ``cryptography`` for both encrypted stores, and ``redis`` only when caching
    is enabled.

Example:
    >>> from axlib.credentials import StoreAdmin, StoreKind, load_settings
    >>> settings = load_settings("/etc/axlib/axlib.toml")  # doctest: +SKIP
    >>> admin = StoreAdmin(settings, StoreKind.SQLITE)  # doctest: +SKIP
    >>> values = {"netuser": "jsmith", "netpass": "example-only"}
    >>> admin.add("j.smith", values).service  # doctest: +SKIP
    'jsmith'
"""

from __future__ import annotations

import argparse
import json
import secrets
import sys
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import TextIO, TypeAlias

from .exceptions import (
    CredentialConfigurationError,
    CredentialError,
    CredentialRecordNotFoundError,
)
from .file_store import (
    CredentialFileRecord,
    CredentialFileStore,
    generate_credential_file_key_file,
    read_credential_file_key,
)
from .manager import normalize_legacy_service_name, normalize_service_for_write
from .profiles import (
    DEFAULT_PROFILE,
    RecordProfile,
    missing_required,
    profile_for_fields,
    validate_values,
)
from .providers import RedisCredentialCache
from .settings import CONFIG_OPTION_HELP, CredentialSettings, load_settings
from .sqlite_store import (
    KEY_LENGTH,
    SQLiteCredentialRecord,
    SQLiteCredentialStore,
    generate_sqlite_key_file,
    read_sqlite_key,
)

# The two store classes share the same method names, so code written against
# this union works with either one.  The aliases give the unions short names.
CredentialStore: TypeAlias = SQLiteCredentialStore | CredentialFileStore
StoreRecord: TypeAlias = SQLiteCredentialRecord | CredentialFileRecord


class StoreKind(StrEnum):
    """Identify one of axlib's two durable, encrypted credential stores.

    ``StrEnum`` members are also ordinary strings, so ``StoreKind("db")`` parses
    a command-line value and ``str(StoreKind.SQLITE)`` prints ``db`` again.
    """

    SQLITE = "db"
    FILE = "file"

    @property
    def label(self) -> str:
        """Return a short human-readable store name.

        Args:
            None: The label depends only on the enum member.

        Returns:
            str: ``"SQLite"`` or ``"text file"``.

        Raises:
            None: Comparing enum members cannot fail.
        """
        return "SQLite" if self is StoreKind.SQLITE else "text file"

    @property
    def command(self) -> str:
        """Return the axlib sub-command that administers this store.

        Args:
            None: The name depends only on the enum member.

        Returns:
            str: ``"credential-db"`` or ``"credential-file"``.

        Raises:
            None: String formatting cannot fail.
        """
        return f"credential-{self.value}"

    @property
    def config_section(self) -> str:
        """Return the TOML table that configures this store.

        Args:
            None: The name depends only on the enum member.

        Returns:
            str: ``"sqlite"`` or ``"credential_file"``.

        Raises:
            None: Comparing enum members cannot fail.
        """
        return "sqlite" if self is StoreKind.SQLITE else "credential_file"

    @property
    def key_env_var(self) -> str:
        """Return the environment variable that can inject this store's key.

        Args:
            None: The name depends only on the enum member.

        Returns:
            str: ``"AXLIB_SQLITE_KEY"`` or ``"AXLIB_CREDENTIAL_FILE_KEY"``.

        Raises:
            None: String formatting cannot fail.
        """
        return f"AXLIB_{self.config_section.upper()}_KEY"


# ax.getkeys() consults SQLite before the text file when both are enabled (see
# axlib.credentials.manager).  The order matters when the same service exists in
# both stores, so it is written down once and reused by StoreAdmin.annotate().
LOOKUP_ORDER: tuple[StoreKind, ...] = (StoreKind.SQLITE, StoreKind.FILE)


@dataclass(frozen=True, slots=True)
class StoreConfig:
    """Hold the settings that matter for one store, under uniform names.

    :class:`~axlib.credentials.settings.CredentialSettings` uses different
    attribute names per store (``sqlite_database`` versus ``credential_file``).
    Gathering them here once means no other code needs an ``if`` per store.

    Attributes:
        kind: Store these settings belong to.
        enabled: Whether ``ax.getkeys()`` consults this store.
        data_path: Database or encrypted text file, if configured.
        key_file: AES key file, if configured.
        key_in_environment: Whether the key is injected through the environment.
        key_file_mode: POSIX mode for newly generated key files.
        owner: Optional owner enforced on store files.
        group: Optional group enforced on store files.
    """

    kind: StoreKind
    enabled: bool
    data_path: Path | None
    key_file: Path | None
    key_in_environment: bool
    key_file_mode: int
    owner: str | None
    group: str | None

    @classmethod
    def from_settings(
        cls, settings: CredentialSettings, kind: StoreKind
    ) -> StoreConfig:
        """Extract one store's settings from the full settings object.

        Args:
            settings (CredentialSettings): Loaded axlib settings.
            kind (StoreKind): Store whose settings are wanted.

        Returns:
            StoreConfig: Uniformly named settings for ``kind``.

        Raises:
            None: Only attribute reads are performed.
        """
        if kind is StoreKind.SQLITE:
            return cls(
                kind=kind,
                enabled=settings.sqlite_enabled,
                data_path=settings.sqlite_database,
                key_file=settings.sqlite_key_file,
                key_in_environment=settings.sqlite_key is not None,
                key_file_mode=settings.sqlite_key_file_mode,
                owner=settings.sqlite_owner,
                group=settings.sqlite_group,
            )
        return cls(
            kind=kind,
            enabled=settings.credential_file_enabled,
            data_path=settings.credential_file,
            key_file=settings.credential_file_key_file,
            key_in_environment=settings.credential_file_key is not None,
            key_file_mode=settings.credential_file_key_file_mode,
            owner=settings.credential_file_owner,
            group=settings.credential_file_group,
        )

    @property
    def key_source(self) -> str:
        """Describe where the store's AES key comes from.

        Both stores prefer an environment key over a key file, so this follows
        the same order.

        Args:
            None: Derived from this object's fields.

        Returns:
            str: ``"environment"``, ``"file"``, or ``"none"``.

        Raises:
            None: Only attribute reads are performed.
        """
        if self.key_in_environment:
            return "environment"
        return "file" if self.key_file is not None else "none"


@dataclass(frozen=True, slots=True)
class StoreStatus:
    """Summarize a store's health without exposing any credential value.

    Attributes:
        kind: Store that was checked.
        enabled: Whether ``ax.getkeys()`` consults this store.
        data_path: Configured database or text-file path, if any.
        data_exists: Whether ``data_path`` exists on disk.
        key_source: ``"environment"``, ``"file"``, or ``"none"``.
        key_file: Configured key-file path, if any.
        key_file_exists: Whether ``key_file`` exists on disk.
        record_count: Number of services, or ``None`` when unreadable.
        error: Operator-facing problem description, or ``None``.
    """

    kind: StoreKind
    enabled: bool
    data_path: Path | None
    data_exists: bool
    key_source: str
    key_file: Path | None
    key_file_exists: bool
    record_count: int | None
    error: str | None

    @property
    def ready(self) -> bool:
        """Report whether records can be listed and changed right now.

        Args:
            None: Derived from this object's fields.

        Returns:
            bool: ``True`` when the store opened and decrypted successfully.

        Raises:
            None: Only attribute reads are performed.
        """
        return self.record_count is not None

    @property
    def state(self) -> str:
        """Return a one-word summary suitable for tables and monitoring.

        Args:
            None: Derived from this object's fields.

        Returns:
            str: ``"ready"``, ``"not-configured"``, ``"not-initialized"``, or
                ``"error"``.

        Raises:
            None: Only attribute reads are performed.
        """
        if self.ready:
            return "ready"
        if self.data_path is None:
            return "not-configured"
        return "error" if self.error else "not-initialized"

    @property
    def can_initialize(self) -> bool:
        """Report whether offering "initialize" makes sense.

        Initialization never overwrites keys or migrates data -- it creates a
        missing store or verifies an existing one -- so it is safe to offer
        whenever a path is configured and the store is not ready.

        Args:
            None: Derived from this object's fields.

        Returns:
            bool: ``True`` when a path is configured and the store is not ready.

        Raises:
            None: Only attribute reads are performed.
        """
        return self.data_path is not None and not self.ready

    @property
    def can_generate_key(self) -> bool:
        """Report whether a new key file could be generated safely.

        Args:
            None: Derived from this object's fields.

        Returns:
            bool: ``True`` when a key file is configured, does not exist yet,
                and no environment key would take precedence over it.

        Raises:
            None: Only attribute reads are performed.
        """
        return self.key_source == "file" and not self.key_file_exists

    def to_dict(self) -> dict[str, object]:
        """Convert the status into JSON-friendly types.

        Args:
            None: Values come from this object.

        Returns:
            dict[str, object]: Plain values, with paths converted to strings.

        Raises:
            None: Only attribute reads and string conversion are performed.
        """
        return {
            "store": self.kind.value,
            "label": self.kind.label,
            "enabled": self.enabled,
            "state": self.state,
            "ready": self.ready,
            "records": self.record_count,
            "data_path": None if self.data_path is None else str(self.data_path),
            "key_source": self.key_source,
            "key_file": None if self.key_file is None else str(self.key_file),
            "key_file_exists": self.key_file_exists,
            "error": self.error,
        }


class NoteKind(StrEnum):
    """Categories of advice attached to a listed record."""

    OPERATOR = "operator"
    SHARED = "shared"
    INCOMPLETE = "incomplete"
    OVERRIDDEN = "overridden"


@dataclass(frozen=True, slots=True)
class RecordNote:
    """One piece of advice about how ``ax.getkeys()`` will treat a record.

    Attributes:
        kind: Category, which a front end may use to choose a color.
        text: Short operator-facing wording, such as ``"missing netpass"``.
    """

    kind: NoteKind
    text: str


@dataclass(frozen=True, slots=True)
class AnnotatedRecord:
    """A listed record together with its likely profile and advice notes.

    Attributes:
        record: Safe metadata returned by the store (no secret values).
        profile: Profile that best matches the record's fields.
        notes: Advice such as "shared fallback" or "missing netpass".
    """

    record: StoreRecord
    profile: RecordProfile
    notes: tuple[RecordNote, ...]


@dataclass(frozen=True, slots=True)
class ChangeResult:
    """Describe a completed change without including any secret value.

    Attributes:
        service: Normalized service name that was changed.
        action: ``"added"``, ``"updated"``, or ``"deleted"``.
        fields: Field names that were written.
        removed: Field names that were removed.
        cache_error: Why the Redis cache could not be cleared, or ``None``.
            The store change itself succeeded either way.
    """

    service: str
    action: str
    fields: tuple[str, ...] = ()
    removed: tuple[str, ...] = ()
    cache_error: str | None = None


def open_store(settings: CredentialSettings, kind: StoreKind) -> CredentialStore:
    """Create the store object for ``kind`` without touching the disk.

    Args:
        settings (CredentialSettings): Loaded axlib settings.
        kind (StoreKind): Store to open.

    Returns:
        CredentialStore: A :class:`SQLiteCredentialStore` or
            :class:`CredentialFileStore`.

    Raises:
        CredentialConfigurationError: If a configured file mode is malformed.
    """
    if kind is StoreKind.SQLITE:
        return SQLiteCredentialStore(settings)
    return CredentialFileStore(settings)


def configured_kinds(settings: CredentialSettings) -> tuple[StoreKind, ...]:
    """List the stores that have a data path configured, in lookup order.

    Args:
        settings (CredentialSettings): Loaded axlib settings.

    Returns:
        tuple[StoreKind, ...]: Configured stores; SQLite first when both are.

    Raises:
        None: Only attribute reads are performed.
    """
    return tuple(
        kind
        for kind in LOOKUP_ORDER
        if StoreConfig.from_settings(settings, kind).data_path is not None
    )


def prepare_update(
    values: Mapping[str, str] | None,
    remove: Iterable[str],
    profile: RecordProfile,
) -> tuple[dict[str, str], tuple[str, ...]]:
    """Validate an update request without touching any store.

    :meth:`StoreAdmin.update` calls this before writing, and front ends call it
    directly to report problems during a dry run or before closing a form.

    Args:
        values (Mapping[str, str] | None): Fields to add or replace.
        remove (Iterable[str]): Field names to delete from the record.
        profile (RecordProfile): Profile deciding allowed/required fields.

    Returns:
        tuple[dict[str, str], tuple[str, ...]]: Validated values and the sorted,
            de-duplicated field names to remove.

    Raises:
        ValueError: If nothing would change, a field is not allowed by
            ``profile``, a required field would be removed, or one field is
            both set and removed.
    """
    prepared = validate_values(values, profile, creating=False) if values else {}
    removing = tuple(sorted(set(remove)))
    if not prepared and not removing:
        raise ValueError("Specify at least one field to set or remove.")

    required = [name for name in removing if name in profile.required_names]
    if required:
        raise ValueError(
            f"Cannot remove required {profile.label} field(s) "
            f"{', '.join(required)}; delete the whole service instead."
        )
    conflicting = sorted(set(prepared).intersection(removing))
    if conflicting:
        raise ValueError(
            f"Cannot both set and remove field(s): {', '.join(conflicting)}"
        )
    return prepared, removing


def _lookup_key(name: str | None) -> str | None:
    """Normalize an optional service name the way ``ax.getkeys()`` does.

    Args:
        name (str | None): Service or operator name, possibly blank.

    Returns:
        str | None: Normalized key, or ``None`` for a missing or blank name.

    Raises:
        None: Normalization accepts any text.
    """
    if name is None or not name.strip():
        return None
    return normalize_legacy_service_name(name.strip())


class StoreAdmin:
    """Administer one durable credential store through a safe, uniform API.

    The raw store classes perform encryption, locking, and file protection.
    This class adds the policy that every front end needs on top of them:
    name normalization, profile validation, Redis cache invalidation, and
    status reporting.  No method returns a secret value.
    """

    def __init__(self, settings: CredentialSettings, kind: StoreKind) -> None:
        """Prepare to administer the store selected by ``kind``.

        Constructing the object performs no disk or network access, so it is
        cheap to create one per request.

        Args:
            settings (CredentialSettings): Loaded axlib settings.
            kind (StoreKind): Store to administer.

        Returns:
            None: Initializers configure the object in place.

        Raises:
            CredentialConfigurationError: If a configured file mode is malformed.
        """
        self.settings = settings
        self.kind = kind
        self.config = StoreConfig.from_settings(settings, kind)
        self.store: CredentialStore = open_store(settings, kind)

    def status(self) -> StoreStatus:
        """Check whether the store exists and can be opened with its key.

        Args:
            None: Paths and key sources come from the settings.

        Returns:
            StoreStatus: Safe summary; problems are reported in ``error``
                rather than raised.

        Raises:
            None: Credential errors are captured into the returned status.
        """
        config = self.config
        data_path = None if config.data_path is None else config.data_path.expanduser()
        key_file = None if config.key_file is None else config.key_file.expanduser()
        data_exists = data_path is not None and data_path.exists()

        record_count: int | None = None
        error: str | None = None
        # A configured-but-missing store is simply "not initialized yet".
        # Otherwise, listing records checks path, permissions, format, and key
        # in one step, and reuses the stores' own clear error messages.
        if data_path is None or data_exists:
            try:
                record_count = len(self.store.list_records())
            except CredentialError as exc:
                error = str(exc)

        return StoreStatus(
            kind=self.kind,
            enabled=config.enabled,
            data_path=data_path,
            data_exists=data_exists,
            key_source=config.key_source,
            key_file=key_file,
            key_file_exists=key_file is not None and key_file.exists(),
            record_count=record_count,
            error=error,
        )

    def initialize(self, *, generate_key: bool = False) -> None:
        """Create the store (and optionally its key file) or verify it.

        Args:
            generate_key (bool): Create the configured key file first.  An
                existing key file is never overwritten.

        Returns:
            None: The store exists and matches the current format afterwards.

        Raises:
            CredentialConfigurationError: If ``generate_key`` is requested while
                an environment key is set or no key file is configured.
            CredentialError: If the key or store cannot be created or verified.
        """
        if generate_key:
            config = self.config
            if config.key_in_environment:
                raise CredentialConfigurationError(
                    f"Cannot generate a key file while {self.kind.key_env_var} is "
                    "set; the environment key would take precedence over the "
                    "new file."
                )
            if config.key_file is None:
                raise CredentialConfigurationError(
                    f"Generating a key requires {self.kind.config_section}.key_file "
                    f"or {self.kind.key_env_var}_FILE."
                )
            generate = (
                generate_sqlite_key_file
                if self.kind is StoreKind.SQLITE
                else generate_credential_file_key_file
            )
            generate(
                config.key_file,
                mode=config.key_file_mode,
                owner=config.owner,
                group=config.group,
            )
        self.store.initialize()

    def list_records(self) -> list[StoreRecord]:
        """List every service with its field names and timestamps.

        Args:
            None: The configured store is read.

        Returns:
            list[StoreRecord]: Safe metadata ordered by service name.

        Raises:
            CredentialError: If the store cannot be opened or decrypted.
        """
        return list(self.store.list_records())

    def annotate(
        self,
        records: Iterable[StoreRecord],
        *,
        operator: str | None = None,
    ) -> list[AnnotatedRecord]:
        """Attach advice explaining how ``ax.getkeys()`` will use each record.

        Args:
            records (Iterable[StoreRecord]): Records from :meth:`list_records`.
            operator (str | None): Login name of the person administering the
                store (normally ``$USER``), used to mark their own record.

        Returns:
            list[AnnotatedRecord]: Records in the same order, with notes.

        Raises:
            None: A higher-precedence store that cannot be read is skipped,
                because these notes are advisory.
        """
        shared_key = _lookup_key(self.settings.shared_service)
        operator_key = _lookup_key(operator)
        overriding = self._higher_precedence_fields()

        annotated: list[AnnotatedRecord] = []
        for record in records:
            profile = profile_for_fields(record.fields)
            notes: list[RecordNote] = []
            if record.service == operator_key:
                notes.append(RecordNote(NoteKind.OPERATOR, "you"))
            if record.service == shared_key:
                notes.append(RecordNote(NoteKind.SHARED, "shared fallback"))
            missing = missing_required(record.fields, profile)
            if missing:
                notes.append(
                    RecordNote(NoteKind.INCOMPLETE, f"missing {','.join(missing)}")
                )
            for kind, fields_by_service in overriding.items():
                # ax.getkeys() fills each field from the first store that has
                # it, so only fields present in *both* stores are overridden.
                hidden = sorted(
                    fields_by_service.get(record.service, frozenset()).intersection(
                        record.fields
                    )
                )
                if hidden:
                    notes.append(
                        RecordNote(
                            NoteKind.OVERRIDDEN,
                            f"{kind.label} overrides {','.join(hidden)}",
                        )
                    )
            annotated.append(AnnotatedRecord(record, profile, tuple(notes)))
        return annotated

    def read_non_secret(self, service: str, profile: RecordProfile) -> dict[str, str]:
        """Read only the fields that a profile marks as safe to display.

        Front ends use this to pre-fill values such as a username so that an
        operator can spot a typo.  Secret fields are never read.

        Args:
            service (str): Service name; it is normalized before use.
            profile (RecordProfile): Profile that decides which fields are
                non-secret.

        Returns:
            dict[str, str]: Non-secret fields that exist in the record.

        Raises:
            ValueError: If the service name is invalid.
            CredentialError: If the store cannot be read.
        """
        visible = [spec.name for spec in profile.fields if not spec.secret]
        if not visible:
            return {}
        return self.store.read(normalize_service_for_write(service), visible)

    def add(
        self,
        service: str,
        values: Mapping[str, str],
        *,
        profile: RecordProfile = DEFAULT_PROFILE,
    ) -> ChangeResult:
        """Create a new service record.

        Args:
            service (str): Service name; it is normalized before use.
            values (Mapping[str, str]): Plaintext field values to encrypt.
            profile (RecordProfile): Profile deciding allowed/required fields.

        Returns:
            ChangeResult: What was stored, and whether the cache was cleared.

        Raises:
            ValueError: If the name or fields are invalid for ``profile``.
            CredentialRecordExistsError: If the service already exists.
            CredentialError: If the store cannot be written.
        """
        service_key = normalize_service_for_write(service)
        prepared = validate_values(values, profile, creating=True)
        self.store.create(service_key, prepared)
        return ChangeResult(
            service=service_key,
            action="added",
            fields=tuple(sorted(prepared)),
            cache_error=self._clear_cache(service_key),
        )

    def update(
        self,
        service: str,
        values: Mapping[str, str] | None = None,
        *,
        remove: Iterable[str] = (),
        profile: RecordProfile = DEFAULT_PROFILE,
    ) -> ChangeResult:
        """Change or remove fields in an existing service record.

        New values are written first, then fields are removed.  The two steps
        are separate store operations, so a failure between them can leave the
        new values written but the old fields still present.

        Args:
            service (str): Service name; it is normalized before use.
            values (Mapping[str, str] | None): Fields to add or replace.
            remove (Iterable[str]): Field names to delete from the record.
                Required fields of ``profile`` cannot be removed.
            profile (RecordProfile): Profile deciding allowed/required fields.

        Returns:
            ChangeResult: What changed, and whether the cache was cleared.

        Raises:
            ValueError: If nothing would change, the name or fields are
                invalid, a required field would be removed, or one field is
                both set and removed.
            CredentialRecordNotFoundError: If the service does not exist.
            CredentialError: If the store cannot be written.
        """
        service_key = normalize_service_for_write(service)
        prepared, removing = prepare_update(values, remove, profile)
        if prepared:
            self.store.update(service_key, prepared)
        if removing:
            self.store.delete(service_key, removing)
        return ChangeResult(
            service=service_key,
            action="updated",
            fields=tuple(sorted(prepared)),
            removed=removing,
            cache_error=self._clear_cache(service_key),
        )

    def delete(self, service: str) -> ChangeResult:
        """Delete a complete service record.

        Args:
            service (str): Service name; it is normalized before use.

        Returns:
            ChangeResult: The deleted service, and whether the cache was cleared.

        Raises:
            ValueError: If the service name is invalid.
            CredentialRecordNotFoundError: If the service does not exist.
            CredentialError: If the store cannot be written.
        """
        service_key = normalize_service_for_write(service)
        if not self.store.delete_service(service_key):
            raise CredentialRecordNotFoundError(
                f"{self.kind.label} credential service does not exist: {service_key!r}"
            )
        return ChangeResult(
            service=service_key,
            action="deleted",
            cache_error=self._clear_cache(service_key),
        )

    def rotate_key(self, new_key: bytes | None = None) -> int:
        """Re-encrypt every record under a new AES-256 key.

        Args:
            new_key (bytes | None): 32-byte replacement key; ``None`` generates
                a random one with :func:`secrets.token_bytes`.

        Returns:
            int: Number of records re-encrypted.

        Raises:
            CredentialConfigurationError: If the key comes from the environment,
                no key file is configured, or a previous rotation is unfinished.
            CredentialError: If re-encryption or key replacement fails.
        """
        # The secrets module draws from the operating system's cryptographic
        # random source; the similarly named random module must never be used
        # for keys because its output is predictable.
        key = secrets.token_bytes(KEY_LENGTH) if new_key is None else new_key
        return self.store.rotate_key(key)

    def load_key_file(self, path: Path) -> bytes:
        """Read and validate a replacement key from a protected file.

        Args:
            path (pathlib.Path): File containing a URL-safe Base64 AES-256 key.

        Returns:
            bytes: The decoded 32-byte key.

        Raises:
            CredentialError: If the file is unreadable or not a valid key.
        """
        reader = (
            read_sqlite_key
            if self.kind is StoreKind.SQLITE
            else read_credential_file_key
        )
        return reader(path)

    def _clear_cache(self, service: str) -> str | None:
        """Delete a service's Redis hash so stale plaintext is not reused.

        Args:
            service (str): Normalized service that just changed.

        Returns:
            str | None: ``None`` on success or when Redis is disabled; otherwise
                an explanation that includes how long stale values may last.

        Raises:
            None: Redis problems are returned rather than raised, because the
                durable change has already succeeded.
        """
        if not self.settings.redis_enabled:
            return None
        try:
            # Deleting the whole hash (not only the changed fields) also clears
            # fields that were just removed from the durable record.
            with RedisCredentialCache(self.settings) as cache:
                cache.delete(service)
        except CredentialError as exc:
            return (
                f"{exc} Cached values may be used for up to "
                f"{self.settings.redis_cache_ttl} seconds."
            )
        return None

    def _higher_precedence_fields(self) -> dict[StoreKind, dict[str, frozenset[str]]]:
        """Collect field names held by stores that ``ax.getkeys()`` checks first.

        Args:
            None: The settings decide which stores are enabled.

        Returns:
            dict[StoreKind, dict[str, frozenset[str]]]: For each enabled store
                earlier in :data:`LOOKUP_ORDER`, its services and field names.

        Raises:
            None: An unreadable store is skipped; its own status reports why.
        """
        found: dict[StoreKind, dict[str, frozenset[str]]] = {}
        for kind in LOOKUP_ORDER[: LOOKUP_ORDER.index(self.kind)]:
            if not StoreConfig.from_settings(self.settings, kind).enabled:
                continue
            try:
                records = open_store(self.settings, kind).list_records()
            except CredentialError:
                continue
            found[kind] = {
                record.service: frozenset(record.fields) for record in records
            }
        return found


def build_arg_parser() -> argparse.ArgumentParser:
    """Build the parser for the standalone store health check.

    Args:
        None: Options are fixed by this module.

    Returns:
        argparse.ArgumentParser: Parser for ``--config``, ``--store``, ``--json``.

    Raises:
        None: Constructing argparse objects has no side effects.
    """
    parser = argparse.ArgumentParser(
        prog="python -m axlib.credentials.admin",
        description=(
            "Report the health of axlib's encrypted credential stores without "
            "showing any credential value. Exit status 0 means every checked "
            "store is ready."
        ),
    )
    parser.add_argument("--config", type=Path, help=CONFIG_OPTION_HELP)
    parser.add_argument(
        "--store",
        type=StoreKind,
        choices=list(StoreKind),
        help="Check only this store (default: every enabled store).",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Write a JSON array instead of a text table.",
    )
    return parser


def main(argv: Sequence[str] | None = None, *, stream: TextIO | None = None) -> int:
    """Print the status of the selected credential stores.

    Args:
        argv (Sequence[str] | None): Arguments excluding the program name.
        stream (TextIO | None): Destination; defaults to standard output.

    Returns:
        int: ``0`` when every checked store is ready, otherwise ``1``.

    Raises:
        SystemExit: If :mod:`argparse` rejects the arguments.
    """
    args = build_arg_parser().parse_args(argv)
    output = sys.stdout if stream is None else stream
    try:
        settings = load_settings(args.config)
        kinds = (args.store,) if args.store else LOOKUP_ORDER
        statuses = [StoreAdmin(settings, kind).status() for kind in kinds]
    except CredentialError as exc:
        print(f"axlib-credential-status: {exc}", file=sys.stderr)
        return 1

    if args.json:
        print(
            json.dumps([status.to_dict() for status in statuses], indent=2), file=output
        )
    else:
        # Imported here: table rendering is only needed for this CLI path.
        from .cli_common import render_table

        rows = [
            (
                status.kind.value,
                "yes" if status.enabled else "no",
                status.state,
                "-" if status.record_count is None else str(status.record_count),
                status.key_source,
                "-" if status.data_path is None else str(status.data_path),
            )
            for status in statuses
        ]
        render_table(
            ("STORE", "ENABLED", "STATE", "RECORDS", "KEY", "PATH"),
            rows,
            stream=output,
        )
        for status in statuses:
            if status.error:
                print(f"{status.kind.value}: {status.error}", file=sys.stderr)

    # An explicitly requested store is checked even when disabled; otherwise
    # only enabled stores count, and "nothing enabled" is itself a failure.
    checked = [status for status in statuses if args.store or status.enabled]
    return 0 if checked and all(status.ready for status in checked) else 1


if __name__ == "__main__":
    raise SystemExit(main())
