# Copyright 2026 Robert Patrick
# SPDX-License-Identifier: Apache-2.0

"""AES-256-GCM encrypted text-file credential storage for Network Operations.

This module provides axlib's native text credential store.  It is intended for
small and medium Network Operations environments that want a human-inspectable
container format without storing usernames, passwords, or enable secrets in
plaintext.  The file uses an INI-style structure for operational metadata, while
each service payload is serialized as JSON and encrypted with AES-256-GCM.

Unlike the historical ``keyrings.cryptfile`` backend, this format is owned by
axlib, supports only AES-256-GCM, and deliberately contains no migration or
legacy-cipher reader.  A file whose format/version does not exactly match this
implementation fails with a clear error.  That keeps greenfield deployments
simple and avoids carrying old cryptographic behavior forward indefinitely.

Shared Linux automation hosts default to mode ``0660`` for the credential file,
mode ``0640`` for the key file, and group ``netops``.  A sibling lock file is
used with :func:`fcntl.flock` so two operators cannot silently overwrite one
another's changes.  Updates are written to a temporary file, fsynced, and then
atomically replaced, which avoids leaving a partially written credential file if
a process or host fails during an update.

Dependencies:
    Python's standard library and the third-party ``cryptography`` package.  A
    URL-safe Base64 key that decodes to exactly 32 bytes is supplied either by
    ``AXLIB_CREDENTIAL_FILE_KEY`` or a protected key file selected by
    ``credential_file.key_file`` / ``AXLIB_CREDENTIAL_FILE_KEY_FILE``.

Example:
    >>> from pathlib import Path
    >>> from axlib.credentials import CredentialFileStore, CredentialSettings
    >>> settings = CredentialSettings(  # doctest: +SKIP
    ...     credential_file=Path("credentials.axc"),
    ...     credential_file_key="base64-encoded-32-byte-key",
    ...     credential_file_group=None,
    ... )
    >>> store = CredentialFileStore(settings)  # doctest: +SKIP
    >>> store.initialize()  # doctest: +SKIP
    >>> store.write("operator", {"netuser": "operator"})  # doctest: +SKIP
"""

from __future__ import annotations

import base64
import binascii
import configparser
import json
import os
import secrets
import stat
import tempfile
import time
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

try:
    import fcntl
except ImportError:  # pragma: no cover - supported deployment targets are POSIX.
    fcntl = None  # type: ignore[assignment]

try:
    import grp
    import pwd
except ImportError:  # pragma: no cover - POSIX ownership is a Linux feature.
    grp = None  # ty: ignore[invalid-assignment]
    pwd = None

from .exceptions import (
    CredentialBackendError,
    CredentialConfigurationError,
    CredentialDependencyError,
    CredentialRecordExistsError,
    CredentialRecordNotFoundError,
)
from .settings import CredentialSettings, parse_file_mode

FORMAT_NAME = "axlib-credential-file"
FORMAT_VERSION = 1
CIPHER_NAME = "AES-256-GCM"
KEY_LENGTH = 32
NONCE_LENGTH = 12
KEY_CHECK_PLAINTEXT = b"axlib-credential-file-key-check-v1"
KEY_CHECK_AAD = b"axlib:credential-file:key-check:v1"
RECORD_AAD_PREFIX = b"axlib:credential-file:record:v1\x00"
METADATA_SECTION = "axlib"
RECORD_SECTION_PREFIX = "credential:"
DEFAULT_FILE_MODE = 0o660
DEFAULT_KEY_FILE_MODE = 0o640
DEFAULT_GROUP = "netops"


@dataclass(frozen=True, slots=True)
class CredentialFileRecord:
    """Safe metadata describing one encrypted text-file service record.

    Attributes:
        service: Normalized service name used by :func:`axlib.getkeys`.
        fields: Sorted encrypted field names; secret values are never included.
        created_at: UTC timestamp recorded when the service was created.
        updated_at: UTC timestamp recorded after the latest change.
    """

    service: str
    fields: tuple[str, ...]
    created_at: str
    updated_at: str


def _load_aesgcm() -> tuple[type[Any], type[BaseException]]:
    """Load AES-GCM classes only when encrypted file storage is used.

    Args:
        None: The dependency is imported from the active Python environment.

    Returns:
        tuple[type[Any], type[BaseException]]: ``AESGCM`` and ``InvalidTag``
            classes from :mod:`cryptography`.

    Raises:
        CredentialDependencyError: If ``cryptography`` is not installed.
    """
    try:
        from cryptography.exceptions import InvalidTag
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    except ImportError as exc:
        raise CredentialDependencyError(
            "Credential-file encryption requires the 'cryptography' package."
        ) from exc
    return AESGCM, InvalidTag


def _utc_timestamp() -> str:
    """Return a compact UTC timestamp for non-secret audit metadata.

    Args:
        None: The current system clock is read directly.

    Returns:
        str: ISO 8601 UTC timestamp ending in ``Z``.

    Raises:
        None: :func:`datetime.now` and formatting are deterministic operations.
    """
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _b64encode(value: bytes) -> str:
    """Encode binary encryption material as URL-safe Base64 text.

    Args:
        value (bytes): Non-secret nonce or encrypted ciphertext bytes.

    Returns:
        str: ASCII Base64 text safe for an INI value.

    Raises:
        None: The standard-library encoder accepts arbitrary bytes.
    """
    return base64.urlsafe_b64encode(value).decode("ascii")


def _b64decode(value: str, *, label: str) -> bytes:
    """Decode one strict URL-safe Base64 value from the credential file.

    Args:
        value (str): Base64 text read from an INI field.
        label (str): Operator-facing field description used in errors.

    Returns:
        bytes: Decoded binary data.

    Raises:
        CredentialBackendError: If the stored text is not valid Base64.
    """
    try:
        return base64.b64decode(
            value.strip().encode("ascii"), altchars=b"-_", validate=True
        )
    except (UnicodeEncodeError, binascii.Error, ValueError) as exc:
        raise CredentialBackendError(
            f"Encrypted credential file contains invalid Base64 for {label}."
        ) from exc


def _decode_key_text(value: str, *, source: str) -> bytes:
    """Decode and validate one URL-safe Base64 AES-256 key.

    Args:
        value (str): Base64 key text from an environment variable or key file.
        source (str): Human-readable key source used in validation messages.

    Returns:
        bytes: Exactly 32 bytes suitable for AES-256-GCM.

    Raises:
        CredentialConfigurationError: If the value is invalid Base64 or does
            not decode to exactly 256 bits.
    """
    text = value.strip()
    if not text:
        raise CredentialConfigurationError(
            f"Credential-file encryption key is empty: {source}"
        )
    try:
        key = base64.b64decode(text.encode("ascii"), altchars=b"-_", validate=True)
    except (UnicodeEncodeError, binascii.Error, ValueError) as exc:
        raise CredentialConfigurationError(
            f"Credential-file encryption key from {source} is not valid "
            "URL-safe Base64."
        ) from exc
    if len(key) != KEY_LENGTH:
        raise CredentialConfigurationError(
            f"Credential-file encryption key from {source} must decode to "
            f"exactly {KEY_LENGTH} bytes for AES-256-GCM; received "
            f"{len(key)} bytes."
        )
    return key


def _read_key_file(path: Path) -> str:
    """Read a Base64 AES key from a protected local file.

    Args:
        path (pathlib.Path): Key file selected by credential-file settings.

    Returns:
        str: Base64 key text with surrounding line whitespace removed.

    Raises:
        CredentialConfigurationError: If the file is unreadable or empty.
    """
    try:
        # The context manager closes this sensitive descriptor immediately rather
        # than leaving it to garbage collection in a long-running automation job.
        with path.open("r", encoding="ascii") as handle:
            value = handle.read().strip()
    except (OSError, UnicodeError) as exc:
        raise CredentialConfigurationError(
            f"Unable to read credential-file encryption key {path}: {exc}"
        ) from exc
    if not value:
        raise CredentialConfigurationError(
            f"Credential-file encryption key is empty: {path}"
        )
    return value


def _resolve_owner_id(owner: str | None) -> int | None:
    """Resolve an optional POSIX owner name to a numeric user ID.

    Args:
        owner (str | None): Account name to enforce, or ``None`` to preserve the
            current owner.

    Returns:
        int | None: Numeric user ID or ``None`` when owner enforcement is off.

    Raises:
        CredentialConfigurationError: If POSIX account lookup is unavailable or
            the configured owner does not exist.
    """
    if owner is None:
        return None
    if pwd is None:
        raise CredentialConfigurationError(
            "Credential-file owner enforcement requires a POSIX operating system."
        )
    try:
        return int(pwd.getpwnam(owner).pw_uid)
    except KeyError as exc:
        raise CredentialConfigurationError(
            f"Configured credential-file owner does not exist: {owner!r}"
        ) from exc


def _resolve_group_id(group: str | None) -> int | None:
    """Resolve an optional POSIX group name to a numeric group ID.

    Args:
        group (str | None): Group such as ``"netops"``, or ``None`` to preserve
            the current group.

    Returns:
        int | None: Numeric group ID or ``None`` when group enforcement is off.

    Raises:
        CredentialConfigurationError: If POSIX group lookup is unavailable or
            the configured group does not exist.
    """
    if group is None:
        return None
    if grp is None:
        raise CredentialConfigurationError(
            "Credential-file group enforcement requires a POSIX operating system."
        )
    try:
        return int(grp.getgrnam(group).gr_gid)
    except KeyError as exc:
        raise CredentialConfigurationError(
            f"Configured credential-file group does not exist: {group!r}"
        ) from exc


def _protect_regular_file(
    path: Path,
    *,
    mode: int,
    owner: str | None,
    group: str | None,
    enforce: bool,
    label: str,
) -> None:
    """Validate and optionally correct one credential-related regular file.

    Args:
        path (pathlib.Path): Existing credential, key, lock, or temporary file.
        mode (int): Exact POSIX mode expected for the file.
        owner (str | None): Optional owner name to enforce.
        group (str | None): Optional group name to enforce.
        enforce (bool): Correct metadata when it differs from policy.
        label (str): Operator-facing description used in errors.

    Returns:
        None: The path is verified and, when requested, protected in place.

    Raises:
        CredentialConfigurationError: If owner/group names are invalid.
        CredentialBackendError: If the path is a symlink, is not regular, or its
            metadata cannot be inspected or corrected.
    """
    try:
        info = path.lstat()
    except OSError as exc:
        raise CredentialBackendError(
            f"Unable to inspect {label} {path}: {exc}"
        ) from exc
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        raise CredentialBackendError(f"{label} is not a regular file: {path}")

    expected_uid = _resolve_owner_id(owner)
    expected_gid = _resolve_group_id(group)
    current_mode = stat.S_IMODE(info.st_mode)
    try:
        if enforce and current_mode != mode:
            # Path.chmod() only gained follow_symlinks in Python 3.13; this
            # project supports 3.11+, and follow_symlinks=False here is a
            # deliberate guard against a symlink swapped in after the lstat()
            # check above, so os.chmod() must stay.
            os.chmod(path, mode, follow_symlinks=False)  # noqa: PTH101
        uid = expected_uid if expected_uid is not None else info.st_uid
        gid = expected_gid if expected_gid is not None else info.st_gid
        if enforce and (uid != info.st_uid or gid != info.st_gid):
            os.chown(path, uid, gid, follow_symlinks=False)
    except OSError as exc:
        raise CredentialBackendError(
            f"Unable to protect {label} {path}: {exc}"
        ) from exc


def _prepare_parent_directory(
    path: Path,
    *,
    mode: int,
    owner: str | None,
    group: str | None,
) -> None:
    """Create a missing parent directory with safe shared-host protection.

    Existing directories are intentionally not chmod/chowned because production
    paths may be governed by ACLs, packages, or configuration management.

    Args:
        path (pathlib.Path): Child path whose parent must exist.
        mode (int): Mode used only when axlib creates the directory.
        owner (str | None): Optional owner to apply to a newly created parent.
        group (str | None): Optional group to apply to a newly created parent.

    Returns:
        None: The parent exists after a successful call.

    Raises:
        CredentialConfigurationError: If owner/group names are invalid.
        CredentialBackendError: If the parent cannot be created or protected.
    """
    parent = path.parent
    if parent.exists():
        if not parent.is_dir():
            raise CredentialBackendError(
                f"Credential-file parent path is not a directory: {parent}"
            )
        return
    try:
        parent.mkdir(parents=True, mode=mode)
        parent.chmod(mode)
        uid = _resolve_owner_id(owner)
        gid = _resolve_group_id(group)
        if uid is not None or gid is not None:
            info = parent.stat()
            os.chown(
                parent,
                info.st_uid if uid is None else uid,
                info.st_gid if gid is None else gid,
            )
    except OSError as exc:
        raise CredentialBackendError(
            f"Unable to create credential-file parent directory {parent}: {exc}"
        ) from exc


def read_credential_file_key(path: Path) -> bytes:
    """Read and validate a Base64 AES-256 key from a protected file.

    This is intended for administration workflows such as key rotation, where
    an operator supplies a new key already generated by a separate process
    (for example a KMS export) rather than a value from the running store's
    own settings.

    Args:
        path (pathlib.Path): File containing URL-safe Base64 key text.

    Returns:
        bytes: Exactly 32 bytes suitable for AES-256-GCM.

    Raises:
        CredentialConfigurationError: If the file is unreadable, empty, not
            valid Base64, or does not decode to exactly 32 bytes.
    """
    return _decode_key_text(_read_key_file(path), source=str(path))


def _write_key_file_atomic(
    path: Path,
    key_text: str,
    *,
    mode: int,
    owner: str | None,
    group: str | None,
) -> None:
    """Atomically create or replace one Base64 AES key file.

    Args:
        path (pathlib.Path): Destination key-file path.  An existing file at
            this path is replaced; use a distinct staging path when an
            existing key must be preserved until a paired change succeeds.
        key_text (str): URL-safe Base64 key text to store.
        mode (int): POSIX mode enforced on the new file.
        owner (str | None): Optional POSIX owner to enforce.
        group (str | None): Optional POSIX group to enforce.

    Returns:
        None: ``path`` contains ``key_text`` after a successful call.

    Raises:
        CredentialConfigurationError: If a configured owner or group is unknown.
        CredentialBackendError: If the temporary file cannot be written,
            protected, or renamed into place.
    """
    temp_path: Path | None = None
    try:
        descriptor, temp_name = tempfile.mkstemp(
            prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
        )
        temp_path = Path(temp_name)
        with os.fdopen(descriptor, "w", encoding="ascii", newline="\n") as handle:
            handle.write(f"{key_text}\n")
            handle.flush()
            os.fsync(handle.fileno())
        _protect_regular_file(
            temp_path,
            mode=mode,
            owner=owner,
            group=group,
            enforce=True,
            label="new credential-file encryption key",
        )
        temp_path.replace(path)
        temp_path = None
    except (CredentialConfigurationError, CredentialBackendError):
        raise
    except OSError as exc:
        raise CredentialBackendError(
            f"Unable to write credential-file encryption key {path}: {exc}"
        ) from exc
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)


def generate_credential_file_key_file(
    path: Path,
    *,
    mode: int = DEFAULT_KEY_FILE_MODE,
    owner: str | None = None,
    group: str | None = DEFAULT_GROUP,
) -> str:
    """Generate a protected URL-safe Base64 AES-256 key file.

    Args:
        path (pathlib.Path): New key-file path.  Existing files are never
            overwritten because doing so would make existing credentials
            unreadable.
        mode (int): POSIX protection for the new key file, normally ``0640`` on
            a shared host or ``0600`` for a private account.
        owner (str | None): Optional owner name to apply.
        group (str | None): Optional group name to apply, default ``netops``.

    Returns:
        str: Generated Base64 key text.  Callers should not print this value.

    Raises:
        CredentialConfigurationError: If the configured mode/ownership is invalid.
        CredentialBackendError: If the path exists or cannot be created safely.
    """
    parse_file_mode(
        mode,
        name="Credential-file key-file mode",
        default=DEFAULT_KEY_FILE_MODE,
        require_owner_write=False,
    )
    parent_mode = 0o2750 if group is not None else 0o700
    _prepare_parent_directory(path, mode=parent_mode, owner=owner, group=group)
    if path.exists() or path.is_symlink():
        raise CredentialBackendError(
            f"Credential-file encryption key already exists: {path}"
        )
    key_text = _b64encode(secrets.token_bytes(KEY_LENGTH))
    created = False
    try:
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        flags |= getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(path, flags, mode)
        created = True
        with os.fdopen(descriptor, "w", encoding="ascii") as handle:
            handle.write(key_text + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        _protect_regular_file(
            path,
            mode=mode,
            owner=owner,
            group=group,
            enforce=True,
            label="credential-file encryption key",
        )
    except (CredentialConfigurationError, CredentialBackendError):
        if created:
            path.unlink(missing_ok=True)
        raise
    except OSError as exc:
        if created:
            path.unlink(missing_ok=True)
        raise CredentialBackendError(
            f"Unable to create credential-file encryption key {path}: {exc}"
        ) from exc
    return key_text


class CredentialFileStore:
    """Store encrypted credential mappings in axlib's versioned text format."""

    def __init__(self, settings: CredentialSettings) -> None:
        """Create a provider without opening or creating the credential file.

        Args:
            settings (CredentialSettings): Text-file path, key source, locking,
                file-mode, owner, and group settings.

        Returns:
            None: Initializers configure the object in place.

        Raises:
            CredentialConfigurationError: If file modes or timeout settings are
                unsafe or malformed.
        """
        parse_file_mode(
            settings.credential_file_mode,
            name="Credential-file mode",
            default=DEFAULT_FILE_MODE,
            require_owner_write=True,
        )
        parse_file_mode(
            settings.credential_file_key_file_mode,
            name="Credential-file key-file mode",
            default=DEFAULT_KEY_FILE_MODE,
            require_owner_write=False,
        )
        if settings.credential_file_lock_timeout <= 0:
            raise CredentialConfigurationError(
                "Credential-file lock timeout must be greater than 0."
            )
        self.settings = settings

    def _file_path(self) -> Path:
        """Return the configured encrypted text-file path.

        Args:
            None: The path is read from this provider's settings.

        Returns:
            pathlib.Path: Configured encrypted credential file.

        Raises:
            CredentialConfigurationError: If the path is missing or is the same
                as the encryption key file.
        """
        if self.settings.credential_file is None:
            raise CredentialConfigurationError(
                "No encrypted credential file is configured. Set "
                "AXLIB_CREDENTIAL_FILE or credential_file.file."
            )
        path = self.settings.credential_file.expanduser()
        if self.settings.credential_file_key_file is not None:
            key_path = self.settings.credential_file_key_file.expanduser()
            if path.resolve(strict=False) == key_path.resolve(strict=False):
                raise CredentialConfigurationError(
                    "Credential file and encryption key file must be different "
                    f"paths: {path}"
                )
        return path

    def _lock_path(self) -> Path:
        """Return the persistent sibling lock-file path for this store.

        Args:
            None: The credential-file path determines the lock location.

        Returns:
            pathlib.Path: Path ending in ``.lock`` beside the credential file.

        Raises:
            CredentialConfigurationError: If the credential path is not set.
        """
        path = self._file_path()
        return path.with_name(path.name + ".lock")

    def _protect_data_file(self, path: Path, *, new_file: bool = False) -> None:
        """Apply configured protection to a credential or lock file.

        Args:
            path (pathlib.Path): Existing file to validate/protect.
            new_file (bool): Force correction for a newly created file even when
                ongoing permission enforcement is disabled.

        Returns:
            None: File metadata is validated in place.

        Raises:
            CredentialConfigurationError: If owner/group settings are invalid.
            CredentialBackendError: If the path is unsafe or cannot be protected.
        """
        _protect_regular_file(
            path,
            mode=self.settings.credential_file_mode,
            owner=self.settings.credential_file_owner,
            group=self.settings.credential_file_group,
            enforce=new_file or self.settings.credential_file_enforce_permissions,
            label="encrypted credential file",
        )

    def _protect_key_file(self, path: Path, *, new_file: bool = False) -> None:
        """Apply configured protection to the credential-file AES key.

        Args:
            path (pathlib.Path): Existing Base64 key file.
            new_file (bool): Force correction for a newly created key file.

        Returns:
            None: File metadata is validated in place.

        Raises:
            CredentialConfigurationError: If owner/group settings are invalid.
            CredentialBackendError: If the path is unsafe or cannot be protected.
        """
        _protect_regular_file(
            path,
            mode=self.settings.credential_file_key_file_mode,
            owner=self.settings.credential_file_owner,
            group=self.settings.credential_file_group,
            enforce=new_file or self.settings.credential_file_enforce_permissions,
            label="credential-file encryption key",
        )

    def _resolve_key(self) -> bytes:
        """Resolve the 256-bit AES key from environment or a protected file.

        Args:
            None: Key sources are read from this provider's settings.

        Returns:
            bytes: Validated 32-byte AES-256 key.

        Raises:
            CredentialConfigurationError: If no key is configured or the key is
                malformed/unreadable.
            CredentialBackendError: If key-file protection validation fails.
        """
        if self.settings.credential_file_key is not None:
            return _decode_key_text(
                self.settings.credential_file_key,
                source="AXLIB_CREDENTIAL_FILE_KEY",
            )
        if self.settings.credential_file_key_file is not None:
            key_path = self.settings.credential_file_key_file.expanduser()
            self._protect_key_file(key_path)
            return _decode_key_text(_read_key_file(key_path), source=str(key_path))
        raise CredentialConfigurationError(
            "No credential-file encryption key is configured. Set "
            "AXLIB_CREDENTIAL_FILE_KEY_FILE or inject "
            "AXLIB_CREDENTIAL_FILE_KEY at runtime."
        )

    def _aesgcm(self) -> Any:
        """Build an AES-GCM object using the configured 256-bit key.

        Args:
            None: The key is resolved from provider settings.

        Returns:
            Any: :class:`cryptography.hazmat.primitives.ciphers.aead.AESGCM`.

        Raises:
            CredentialConfigurationError: If the key is absent or invalid.
            CredentialDependencyError: If ``cryptography`` is unavailable.
        """
        aesgcm_class, _ = _load_aesgcm()
        return aesgcm_class(self._resolve_key())

    @staticmethod
    def _validate_service(service: str) -> str:
        """Reject a blank service before using it as encrypted-record identity.

        Args:
            service (str): Normalized operator or automation service name.

        Returns:
            str: The original non-empty service text.

        Raises:
            CredentialConfigurationError: If the service is blank.
        """
        if not isinstance(service, str) or not service:
            raise CredentialConfigurationError(
                "Credential-file service name cannot be blank."
            )
        return service

    @staticmethod
    def _validate_values(values: Mapping[str, str]) -> dict[str, str]:
        """Validate a credential mapping without altering secret text.

        Args:
            values (Mapping[str, str]): Field names and plaintext credential
                values such as ``netuser``, ``netpass``, and ``netenable``.

        Returns:
            dict[str, str]: A plain dictionary preserving each value exactly.

        Raises:
            CredentialConfigurationError: If the mapping is empty or contains
                blank/non-string field names or non-string values.
        """
        copied = dict(values)
        if not copied:
            raise CredentialConfigurationError(
                "Credential-file write requires at least one field."
            )
        if not all(
            isinstance(field, str) and bool(field) and isinstance(value, str)
            for field, value in copied.items()
        ):
            raise CredentialConfigurationError(
                "Credential-file field names must be non-empty strings and "
                "credential values must be strings."
            )
        return copied

    @staticmethod
    def _record_aad(service: str) -> bytes:
        """Bind AES-GCM record integrity to its normalized service name.

        Args:
            service (str): Service whose encrypted payload is authenticated.

        Returns:
            bytes: Associated authenticated data passed to AES-GCM.

        Raises:
            UnicodeEncodeError: If Python cannot encode the service as UTF-8.
        """
        return RECORD_AAD_PREFIX + service.encode("utf-8")

    @staticmethod
    def _section_name(service: str) -> str:
        """Encode a service into a parser-safe INI section name.

        Args:
            service (str): Normalized credential service.

        Returns:
            str: ``credential:`` followed by URL-safe Base64 service text.

        Raises:
            UnicodeEncodeError: If Python cannot encode the service as UTF-8.
        """
        encoded = base64.urlsafe_b64encode(service.encode("utf-8")).decode("ascii")
        return RECORD_SECTION_PREFIX + encoded.rstrip("=")

    @staticmethod
    def _new_parser() -> configparser.ConfigParser:
        """Build a strict parser for axlib's versioned credential-file format.

        Args:
            None: Parser policy is fixed by format version 1.

        Returns:
            configparser.ConfigParser: Parser with interpolation disabled so
                password ciphertext is never interpreted as substitution syntax.

        Raises:
            None: Parser construction performs no I/O.
        """
        parser = configparser.ConfigParser(
            interpolation=None,
            strict=True,
            empty_lines_in_values=False,
        )
        # The standard-library-documented way to disable ConfigParser's
        # default option-name lowercasing; ty models optionxform as a bound
        # method, so it flags this idiom even though configparser expects it.
        parser.optionxform = str  # ty: ignore[invalid-assignment]
        return parser

    def _encrypt_values(
        self, service: str, values: Mapping[str, str]
    ) -> tuple[bytes, bytes]:
        """Serialize and encrypt one service mapping with a fresh nonce.

        Args:
            service (str): Service name authenticated with the ciphertext.
            values (Mapping[str, str]): Plaintext credential fields.

        Returns:
            tuple[bytes, bytes]: Random 12-byte nonce and AES-GCM ciphertext.

        Raises:
            CredentialConfigurationError: If values/key configuration is invalid.
            CredentialDependencyError: If ``cryptography`` is unavailable.
        """
        payload = self._validate_values(values)
        plaintext = json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
        nonce = secrets.token_bytes(NONCE_LENGTH)
        ciphertext = self._aesgcm().encrypt(nonce, plaintext, self._record_aad(service))
        return nonce, ciphertext

    def _decrypt_values(
        self, service: str, nonce: bytes, ciphertext: bytes
    ) -> dict[str, str]:
        """Authenticate, decrypt, and validate one service payload.

        Args:
            service (str): Service name used as AES-GCM associated data.
            nonce (bytes): Stored 12-byte nonce.
            ciphertext (bytes): Encrypted JSON and GCM authentication tag.

        Returns:
            dict[str, str]: Decrypted credential mapping.

        Raises:
            CredentialBackendError: If the key is wrong, data was modified, or
                plaintext structure is invalid.
            CredentialConfigurationError: If the configured key is invalid.
            CredentialDependencyError: If ``cryptography`` is unavailable.
        """
        if len(nonce) != NONCE_LENGTH:
            raise CredentialBackendError(
                f"Credential-file nonce is invalid for service {service!r}."
            )
        _, invalid_tag_class = _load_aesgcm()
        try:
            plaintext = self._aesgcm().decrypt(
                nonce, ciphertext, self._record_aad(service)
            )
            decoded = json.loads(plaintext.decode("utf-8"))
        except invalid_tag_class as exc:
            raise CredentialBackendError(
                f"Credential-file authentication failed for service {service!r}; "
                "the key is wrong or the record was modified."
            ) from exc
        except (CredentialConfigurationError, CredentialDependencyError):
            raise
        except (UnicodeError, json.JSONDecodeError, TypeError, ValueError) as exc:
            raise CredentialBackendError(
                f"Credential-file payload for service {service!r} is invalid."
            ) from exc
        if not isinstance(decoded, dict) or not all(
            isinstance(field, str) and bool(field) and isinstance(value, str)
            for field, value in decoded.items()
        ):
            raise CredentialBackendError(
                f"Credential-file payload for service {service!r} has an "
                "unsupported structure."
            )
        return dict(decoded)

    def _verify_key_check(self, nonce: bytes, ciphertext: bytes) -> None:
        """Confirm that the configured key authenticates the file header marker.

        Args:
            nonce (bytes): Nonce stored in the ``[axlib]`` metadata section.
            ciphertext (bytes): Encrypted key-check marker.

        Returns:
            None: Successful decryption proves the key matches the file.

        Raises:
            CredentialBackendError: If the marker cannot be authenticated.
            CredentialConfigurationError: If key settings are invalid.
            CredentialDependencyError: If ``cryptography`` is unavailable.
        """
        _, invalid_tag_class = _load_aesgcm()
        try:
            plaintext = self._aesgcm().decrypt(nonce, ciphertext, KEY_CHECK_AAD)
        except invalid_tag_class as exc:
            raise CredentialBackendError(
                "Credential-file key verification failed; the configured key is "
                "wrong or file metadata was modified."
            ) from exc
        if plaintext != KEY_CHECK_PLAINTEXT:
            raise CredentialBackendError(
                "Credential-file key verification marker is invalid."
            )

    def _verify_parser(self, parser: configparser.ConfigParser) -> None:
        """Validate format identity/version and authenticate the key marker.

        Args:
            parser (configparser.ConfigParser): Parsed credential file.

        Returns:
            None: The parser is safe to use with this implementation.

        Raises:
            CredentialBackendError: If the file is malformed, has an unsupported
                version/cipher, or the key-check marker fails.
        """
        if not parser.has_section(METADATA_SECTION):
            raise CredentialBackendError(
                "Encrypted credential file is missing the [axlib] metadata section."
            )
        metadata = parser[METADATA_SECTION]
        try:
            format_name = metadata["format"]
            version = int(metadata["version"])
            cipher = metadata["cipher"]
            nonce = _b64decode(metadata["key_check_nonce"], label="key_check_nonce")
            ciphertext = _b64decode(
                metadata["key_check_ciphertext"], label="key_check_ciphertext"
            )
        except KeyError as exc:
            raise CredentialBackendError(
                f"Encrypted credential file metadata is missing {exc.args[0]!r}."
            ) from exc
        except ValueError as exc:
            raise CredentialBackendError(
                "Encrypted credential file version is not an integer."
            ) from exc
        if format_name != FORMAT_NAME:
            raise CredentialBackendError(
                f"Unsupported credential-file format {format_name!r}; expected "
                f"{FORMAT_NAME!r}."
            )
        if version != FORMAT_VERSION:
            raise CredentialBackendError(
                f"Unsupported credential-file version {version}; expected "
                f"{FORMAT_VERSION}. No automatic migration is performed."
            )
        if cipher != CIPHER_NAME:
            raise CredentialBackendError(
                f"Unsupported credential-file cipher {cipher!r}; expected "
                f"{CIPHER_NAME!r}."
            )
        if len(nonce) != NONCE_LENGTH:
            raise CredentialBackendError(
                "Credential-file key-check nonce has an invalid length."
            )
        self._verify_key_check(nonce, ciphertext)

    def _load_parser(self) -> configparser.ConfigParser:
        """Read and verify the initialized encrypted credential file.

        Args:
            None: File location and key come from provider settings.

        Returns:
            configparser.ConfigParser: Verified in-memory representation.

        Raises:
            CredentialBackendError: If the file is missing, malformed, unsafe,
                or uses an unsupported version.
            CredentialConfigurationError: If key/path policy is invalid.
        """
        path = self._file_path()
        if not path.exists():
            raise CredentialBackendError(
                f"Encrypted credential file does not exist: {path}. Initialize it "
                "with 'axlib credential-file init'."
            )
        self._protect_data_file(path)
        parser = self._new_parser()
        try:
            with path.open("r", encoding="utf-8") as handle:
                parser.read_file(handle)
        except (OSError, UnicodeError, configparser.Error) as exc:
            raise CredentialBackendError(
                f"Unable to read encrypted credential file {path}: {exc}"
            ) from exc
        self._verify_parser(parser)
        return parser

    @contextmanager
    def _locked(self, *, exclusive: bool) -> Iterator[None]:
        """Hold a shared or exclusive cross-process lock for one file operation.

        Args:
            exclusive (bool): Use an exclusive lock for writes; reads use a
                shared lock so several automation processes may look up secrets
                concurrently.

        Returns:
            Iterator[None]: Context manager that releases the lock on exit.

        Raises:
            CredentialDependencyError: If POSIX ``fcntl`` locking is unavailable.
            CredentialBackendError: If the lock file is missing/unopenable or the
                configured timeout expires.
        """
        if fcntl is None:
            raise CredentialDependencyError(
                "Credential-file locking requires POSIX fcntl support."
            )
        lock_path = self._lock_path()
        if not lock_path.exists():
            raise CredentialBackendError(
                f"Credential-file lock does not exist: {lock_path}. Initialize "
                "the store with 'axlib credential-file init'."
            )
        self._protect_data_file(lock_path)
        try:
            handle = lock_path.open("r+")
        except OSError as exc:
            raise CredentialBackendError(
                f"Unable to open credential-file lock {lock_path}: {exc}"
            ) from exc
        operation = fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH
        deadline = time.monotonic() + self.settings.credential_file_lock_timeout
        try:
            while True:
                try:
                    fcntl.flock(handle.fileno(), operation | fcntl.LOCK_NB)
                    break
                except BlockingIOError as exc:
                    if time.monotonic() >= deadline:
                        raise CredentialBackendError(
                            "Timed out waiting for encrypted credential-file lock "
                            f"after {self.settings.credential_file_lock_timeout:g} "
                            "seconds."
                        ) from exc
                    time.sleep(0.05)
            yield
        finally:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            finally:
                handle.close()

    def _write_parser(self, parser: configparser.ConfigParser) -> None:
        """Atomically replace the credential file with an in-memory parser.

        Args:
            parser (configparser.ConfigParser): Complete verified credential
                document to persist.

        Returns:
            None: The file is replaced atomically and its parent is fsynced.

        Raises:
            CredentialBackendError: If a temporary file cannot be written,
                protected, replaced, or synchronized.
            CredentialConfigurationError: If ownership policy is invalid.
        """
        path = self._file_path()
        temp_path: Path | None = None
        try:
            descriptor, temp_name = tempfile.mkstemp(
                prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
            )
            temp_path = Path(temp_name)
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
                parser.write(handle, space_around_delimiters=True)
                handle.flush()
                os.fsync(handle.fileno())
            _protect_regular_file(
                temp_path,
                mode=self.settings.credential_file_mode,
                owner=self.settings.credential_file_owner,
                group=self.settings.credential_file_group,
                enforce=True,
                label="temporary encrypted credential file",
            )
            temp_path.replace(path)
            temp_path = None
            self._protect_data_file(path, new_file=True)
            try:
                directory_fd = os.open(path.parent, os.O_RDONLY)
                try:
                    os.fsync(directory_fd)
                finally:
                    os.close(directory_fd)
            except OSError:
                # Some filesystems/platforms do not permit directory fsync.  The
                # file itself has already been fsynced and atomically replaced.
                pass
        except (CredentialBackendError, CredentialConfigurationError):
            raise
        except OSError as exc:
            raise CredentialBackendError(
                f"Unable to atomically update encrypted credential file {path}: {exc}"
            ) from exc
        finally:
            if temp_path is not None:
                temp_path.unlink(missing_ok=True)

    def _read_record(
        self, parser: configparser.ConfigParser, service: str
    ) -> tuple[dict[str, str], str, str] | None:
        """Decrypt one record from an already verified parser.

        Args:
            parser (configparser.ConfigParser): Verified credential document.
            service (str): Normalized service name.

        Returns:
            tuple[dict[str, str], str, str] | None: Values, creation timestamp,
                and update timestamp; ``None`` when the service is absent.

        Raises:
            CredentialBackendError: If record metadata or ciphertext is invalid.
        """
        section_name = self._section_name(service)
        if not parser.has_section(section_name):
            return None
        section = parser[section_name]
        try:
            stored_service = section["service"]
            nonce = _b64decode(section["nonce"], label=f"{service} nonce")
            ciphertext = _b64decode(
                section["ciphertext"], label=f"{service} ciphertext"
            )
            created_at = section["created_at"]
            updated_at = section["updated_at"]
        except KeyError as exc:
            raise CredentialBackendError(
                f"Credential-file record {service!r} is missing {exc.args[0]!r}."
            ) from exc
        if stored_service != service:
            raise CredentialBackendError(
                f"Credential-file section identity mismatch for service {service!r}."
            )
        return self._decrypt_values(service, nonce, ciphertext), created_at, updated_at

    def _set_record(
        self,
        parser: configparser.ConfigParser,
        service: str,
        values: Mapping[str, str],
        *,
        created_at: str,
        updated_at: str,
    ) -> None:
        """Encrypt and place one complete service record into a parser.

        Args:
            parser (configparser.ConfigParser): Document modified in memory.
            service (str): Normalized service identity.
            values (Mapping[str, str]): Complete plaintext mapping to encrypt.
            created_at (str): Original creation timestamp.
            updated_at (str): Timestamp for this change.

        Returns:
            None: The parser is updated; disk I/O is performed separately.

        Raises:
            CredentialConfigurationError: If values or key settings are invalid.
            CredentialDependencyError: If ``cryptography`` is unavailable.
        """
        nonce, ciphertext = self._encrypt_values(service, values)
        section_name = self._section_name(service)
        if not parser.has_section(section_name):
            parser.add_section(section_name)
        parser[section_name].clear()
        parser[section_name].update(
            {
                "service": service,
                "nonce": _b64encode(nonce),
                "ciphertext": _b64encode(ciphertext),
                "created_at": created_at,
                "updated_at": updated_at,
            }
        )

    def initialize(self) -> None:
        """Create a new AES-256-GCM text store or verify an existing one.

        Existing stores are **not** migrated.  An unsupported format, version,
        or cipher fails immediately.  Running ``init`` on a valid existing store
        is therefore a verification/protection operation, mirroring
        :meth:`SQLiteCredentialStore.initialize`.

        Args:
            None: File path, key, permissions, and ownership come from settings.

        Returns:
            None: A new file/lock pair is created or an existing pair verified.

        Raises:
            CredentialConfigurationError: If path/key/policy is invalid.
            CredentialDependencyError: If AES-GCM or POSIX locking is unavailable.
            CredentialBackendError: If creation, verification, or protection fails.
        """
        path = self._file_path()
        self._resolve_key()
        if self.settings.credential_file_group is not None and (
            self.settings.credential_file_mode & 0o020
        ):
            parent_mode = 0o2770
        elif self.settings.credential_file_group is not None and (
            self.settings.credential_file_mode & 0o040
        ):
            parent_mode = 0o2750
        else:
            parent_mode = 0o700
        _prepare_parent_directory(
            path,
            mode=parent_mode,
            owner=self.settings.credential_file_owner,
            group=self.settings.credential_file_group,
        )
        lock_path = self._lock_path()
        if not lock_path.exists():
            try:
                flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
                flags |= getattr(os, "O_NOFOLLOW", 0)
                descriptor = os.open(
                    lock_path, flags, self.settings.credential_file_mode
                )
                os.close(descriptor)
                self._protect_data_file(lock_path, new_file=True)
            except FileExistsError:
                pass
            except OSError as exc:
                raise CredentialBackendError(
                    f"Unable to create credential-file lock {lock_path}: {exc}"
                ) from exc
        else:
            self._protect_data_file(lock_path)

        with self._locked(exclusive=True):
            if path.exists() or path.is_symlink():
                # This verifies exact format/version/cipher and key.  No legacy
                # parser or migration path is intentionally invoked here.
                self._load_parser()
                return

            parser = self._new_parser()
            parser.add_section(METADATA_SECTION)
            nonce = secrets.token_bytes(NONCE_LENGTH)
            ciphertext = self._aesgcm().encrypt(
                nonce, KEY_CHECK_PLAINTEXT, KEY_CHECK_AAD
            )
            parser[METADATA_SECTION].update(
                {
                    "format": FORMAT_NAME,
                    "version": str(FORMAT_VERSION),
                    "cipher": CIPHER_NAME,
                    "created_at": _utc_timestamp(),
                    "key_check_nonce": _b64encode(nonce),
                    "key_check_ciphertext": _b64encode(ciphertext),
                }
            )
            self._write_parser(parser)

    def read(self, service: str, fields: Sequence[str]) -> dict[str, str]:
        """Read selected fields from one encrypted text-file service record.

        Args:
            service (str): Normalized operator or shared-service lookup key.
            fields (Sequence[str]): Requested fields such as ``netuser``,
                ``netpass``, and ``netenable``.

        Returns:
            dict[str, str]: Requested fields present in the record, or an empty
                dictionary if the service does not exist.

        Raises:
            CredentialBackendError: If file/lock/format/ciphertext checks fail.
            CredentialConfigurationError: If path/key settings are invalid.
            CredentialDependencyError: If AES-GCM or POSIX locking is unavailable.
        """
        service = self._validate_service(service)
        if not fields:
            return {}
        with self._locked(exclusive=False):
            parser = self._load_parser()
            record = self._read_record(parser, service)
        if record is None:
            return {}
        values, _, _ = record
        return {field: values[field] for field in fields if field in values}

    def create(self, service: str, values: Mapping[str, str]) -> None:
        """Create a new encrypted service record and reject duplicates.

        Args:
            service (str): Normalized operator or automation service name.
            values (Mapping[str, str]): Complete initial credential mapping.

        Returns:
            None: One encrypted record is added to the text file.

        Raises:
            CredentialRecordExistsError: If the service already exists.
            CredentialBackendError: If file/locking/encryption fails.
            CredentialConfigurationError: If path/key/service/values are invalid.
            CredentialDependencyError: If AES-GCM or POSIX locking is unavailable.
        """
        service = self._validate_service(service)
        values = self._validate_values(values)
        with self._locked(exclusive=True):
            parser = self._load_parser()
            if self._read_record(parser, service) is not None:
                raise CredentialRecordExistsError(
                    f"Credential-file service already exists: {service!r}"
                )
            timestamp = _utc_timestamp()
            self._set_record(
                parser,
                service,
                values,
                created_at=timestamp,
                updated_at=timestamp,
            )
            self._write_parser(parser)

    def update(self, service: str, values: Mapping[str, str]) -> None:
        """Merge fields into an existing encrypted service record.

        Args:
            service (str): Normalized service that must already exist.
            values (Mapping[str, str]): Fields to add or replace.  Secret text is
                preserved exactly, including leading/trailing spaces.

        Returns:
            None: The complete record is re-encrypted with a fresh nonce.

        Raises:
            CredentialRecordNotFoundError: If the service does not exist.
            CredentialBackendError: If file/locking/encryption fails.
            CredentialConfigurationError: If path/key/service/values are invalid.
            CredentialDependencyError: If AES-GCM or POSIX locking is unavailable.
        """
        service = self._validate_service(service)
        updates = self._validate_values(values)
        with self._locked(exclusive=True):
            parser = self._load_parser()
            record = self._read_record(parser, service)
            if record is None:
                raise CredentialRecordNotFoundError(
                    f"Credential-file service does not exist: {service!r}"
                )
            current, created_at, _ = record
            current.update(updates)
            self._set_record(
                parser,
                service,
                current,
                created_at=created_at,
                updated_at=_utc_timestamp(),
            )
            self._write_parser(parser)

    def write(self, service: str, values: Mapping[str, str]) -> None:
        """Atomically create a service or merge fields when it already exists.

        Args:
            service (str): Normalized service name used by axlib lookup.
            values (Mapping[str, str]): Fields to create or update.

        Returns:
            None: The encrypted file contains the supplied field values.

        Raises:
            CredentialBackendError: If file/locking/encryption fails.
            CredentialConfigurationError: If path/key/service/values are invalid.
            CredentialDependencyError: If AES-GCM or POSIX locking is unavailable.
        """
        service = self._validate_service(service)
        updates = self._validate_values(values)
        with self._locked(exclusive=True):
            parser = self._load_parser()
            record = self._read_record(parser, service)
            timestamp = _utc_timestamp()
            if record is None:
                merged = updates
                created_at = timestamp
            else:
                merged, created_at, _ = record
                merged.update(updates)
            self._set_record(
                parser,
                service,
                merged,
                created_at=created_at,
                updated_at=timestamp,
            )
            self._write_parser(parser)

    def delete_service(self, service: str) -> bool:
        """Delete one complete encrypted service record.

        Args:
            service (str): Normalized service to remove.

        Returns:
            bool: ``True`` when a record was removed, otherwise ``False``.

        Raises:
            CredentialBackendError: If file/locking/format checks fail.
            CredentialConfigurationError: If path/key/service is invalid.
            CredentialDependencyError: If AES-GCM or POSIX locking is unavailable.
        """
        service = self._validate_service(service)
        with self._locked(exclusive=True):
            parser = self._load_parser()
            section_name = self._section_name(service)
            if not parser.has_section(section_name):
                return False
            # Decrypt before deletion so corrupt data or a wrong key cannot be
            # mistaken for a successful administrative action.
            self._read_record(parser, service)
            parser.remove_section(section_name)
            self._write_parser(parser)
            return True

    def delete(self, service: str, fields: Sequence[str]) -> None:
        """Remove selected fields and delete the record if none remain.

        Args:
            service (str): Normalized service whose encrypted payload is changed.
            fields (Sequence[str]): Field names to remove.

        Returns:
            None: The record is re-encrypted or removed in place.

        Raises:
            CredentialRecordNotFoundError: If the service does not exist.
            CredentialBackendError: If file/locking/encryption fails.
            CredentialConfigurationError: If path/key/service is invalid.
            CredentialDependencyError: If AES-GCM or POSIX locking is unavailable.
        """
        service = self._validate_service(service)
        if not fields:
            return
        with self._locked(exclusive=True):
            parser = self._load_parser()
            record = self._read_record(parser, service)
            if record is None:
                raise CredentialRecordNotFoundError(
                    f"Credential-file service does not exist: {service!r}"
                )
            values, created_at, _ = record
            for field in fields:
                values.pop(field, None)
            if not values:
                parser.remove_section(self._section_name(service))
            else:
                self._set_record(
                    parser,
                    service,
                    values,
                    created_at=created_at,
                    updated_at=_utc_timestamp(),
                )
            self._write_parser(parser)

    def get_password(self, service: str, field: str) -> str | None:
        """Return one field using a familiar encrypted-keyring calling style.

        This convenience method lets greenfield scripts replace a simple
        ``get_password(service, field)`` pattern without importing ``keyring``.
        The standardized :meth:`read` method remains the preferred axlib API
        when a workflow needs more than one credential field.

        Args:
            service (str): Normalized operator or automation service name.
            field (str): Credential field such as ``netpass`` or ``netenable``.

        Returns:
            str | None: Decrypted field value, or ``None`` when the service or
                field does not exist.

        Raises:
            CredentialBackendError: If file, locking, format, or integrity checks
                fail.
            CredentialConfigurationError: If path/key/service settings are invalid.
            CredentialDependencyError: If AES-GCM or POSIX locking is unavailable.
        """
        return self.read(service, (field,)).get(field)

    def set_password(self, service: str, field: str, value: str) -> None:
        """Create or replace one field using a familiar keyring-style method.

        Args:
            service (str): Normalized operator or automation service name.
            field (str): Credential field to create or replace.
            value (str): Exact plaintext secret value to encrypt.

        Returns:
            None: The service is created or updated through :meth:`write`.

        Raises:
            CredentialBackendError: If file, locking, or encryption fails.
            CredentialConfigurationError: If path/key/service/field data is invalid.
            CredentialDependencyError: If AES-GCM or POSIX locking is unavailable.
        """
        self.write(service, {field: value})

    def delete_password(self, service: str, field: str) -> None:
        """Delete one field using a familiar keyring-style method name.

        Args:
            service (str): Normalized operator or automation service name.
            field (str): Credential field to remove.

        Returns:
            None: The field is removed; an empty service record is removed too.

        Raises:
            CredentialRecordNotFoundError: If the service does not exist.
            CredentialBackendError: If file, locking, or encryption fails.
            CredentialConfigurationError: If path/key/service settings are invalid.
            CredentialDependencyError: If AES-GCM or POSIX locking is unavailable.
        """
        self.delete(service, (field,))

    def list_records(self) -> list[CredentialFileRecord]:
        """List service metadata without returning credential values.

        Args:
            None: Every encrypted service record is inspected.

        Returns:
            list[CredentialFileRecord]: Service names, field names, and audit
                timestamps ordered case-insensitively by service.

        Raises:
            CredentialBackendError: If file/format/key/record validation fails.
            CredentialConfigurationError: If path/key settings are invalid.
            CredentialDependencyError: If AES-GCM or POSIX locking is unavailable.
        """
        records: list[CredentialFileRecord] = []
        with self._locked(exclusive=False):
            parser = self._load_parser()
            for section_name in parser.sections():
                if section_name == METADATA_SECTION:
                    continue
                if not section_name.startswith(RECORD_SECTION_PREFIX):
                    raise CredentialBackendError(
                        f"Unsupported section in encrypted credential file: "
                        f"{section_name!r}."
                    )
                section = parser[section_name]
                try:
                    service = section["service"]
                except KeyError as exc:
                    raise CredentialBackendError(
                        f"Credential-file section {section_name!r} is missing "
                        "its service identity."
                    ) from exc
                expected_section = self._section_name(service)
                if expected_section != section_name:
                    raise CredentialBackendError(
                        f"Credential-file section identity is malformed for "
                        f"service {service!r}."
                    )
                record = self._read_record(parser, service)
                if record is None:  # defensive; section was just observed.
                    continue
                values, created_at, updated_at = record
                records.append(
                    CredentialFileRecord(
                        service=service,
                        fields=tuple(sorted(values)),
                        created_at=created_at,
                        updated_at=updated_at,
                    )
                )
        return sorted(records, key=lambda record: record.service.casefold())

    def rotate_key(self, new_key: bytes) -> int:
        """Re-encrypt every record and the key-check marker under a new key.

        Every service record is decrypted with the currently configured key,
        then re-encrypted with fresh nonces under ``new_key`` in one exclusive
        lock, and the credential file is atomically replaced exactly as
        :meth:`update` replaces it.  Rotation requires
        ``credential_file.key_file`` / ``AXLIB_CREDENTIAL_FILE_KEY_FILE``
        because axlib cannot update the calling shell's environment, so a key
        supplied only through ``AXLIB_CREDENTIAL_FILE_KEY`` cannot be rotated
        in place.

        The key file is replaced only after the credential file has already
        been atomically replaced with the new ciphertext, which is the
        earliest point both changes can be made durable together.  If the
        process is interrupted between those two steps, the new key remains
        recoverable at ``<key_file>.rotating`` beside the configured key file.

        Args:
            new_key (bytes): Freshly generated or externally supplied 32-byte
                AES-256 key that will replace the current one.

        Returns:
            int: Number of service records re-encrypted.

        Raises:
            CredentialConfigurationError: If ``new_key`` is not 32 bytes, no
                key file is configured, the key is configured only through the
                environment, or a previous rotation's staging file was left in
                place and must be resolved first.
            CredentialBackendError: If file, locking, format, or key-file
                replacement fails.
            CredentialDependencyError: If AES-GCM or POSIX locking is
                unavailable.
        """
        if len(new_key) != KEY_LENGTH:
            raise CredentialConfigurationError(
                "New credential-file encryption key must be exactly "
                f"{KEY_LENGTH} bytes for AES-256-GCM; received "
                f"{len(new_key)} bytes."
            )
        if self.settings.credential_file_key is not None:
            raise CredentialConfigurationError(
                "Credential-file key rotation requires a configured key file; "
                "AXLIB_CREDENTIAL_FILE_KEY cannot be rotated in place because "
                "axlib cannot update the calling shell's environment."
            )
        if self.settings.credential_file_key_file is None:
            raise CredentialConfigurationError(
                "Credential-file key rotation requires credential_file.key_file "
                "or AXLIB_CREDENTIAL_FILE_KEY_FILE."
            )
        key_file = self.settings.credential_file_key_file.expanduser()
        staged_key_path = key_file.with_name(key_file.name + ".rotating")
        if staged_key_path.exists():
            raise CredentialConfigurationError(
                "A previous credential-file key rotation may not have "
                f"completed: found leftover staging file {staged_key_path}. "
                "Verify it, then either move it into place as the key file or "
                "remove it before retrying."
            )

        aesgcm_class, _ = _load_aesgcm()
        new_aesgcm = aesgcm_class(new_key)

        with self._locked(exclusive=True):
            parser = self._load_parser()  # verifies format/version/key
            rotated: list[tuple[str, dict[str, str]]] = []
            for section_name in parser.sections():
                if section_name == METADATA_SECTION:
                    continue
                service = parser[section_name]["service"]
                record = self._read_record(parser, service)
                if record is None:  # defensive; section was just observed.
                    continue
                values, _, _ = record
                rotated.append((service, values))

            key_nonce = secrets.token_bytes(NONCE_LENGTH)
            key_ciphertext = new_aesgcm.encrypt(
                key_nonce, KEY_CHECK_PLAINTEXT, KEY_CHECK_AAD
            )
            parser[METADATA_SECTION]["key_check_nonce"] = _b64encode(key_nonce)
            parser[METADATA_SECTION]["key_check_ciphertext"] = _b64encode(
                key_ciphertext
            )

            for service, values in rotated:
                payload = json.dumps(
                    values,
                    sort_keys=True,
                    separators=(",", ":"),
                    ensure_ascii=False,
                ).encode("utf-8")
                nonce = secrets.token_bytes(NONCE_LENGTH)
                ciphertext = new_aesgcm.encrypt(
                    nonce, payload, self._record_aad(service)
                )
                section_name = self._section_name(service)
                parser[section_name]["nonce"] = _b64encode(nonce)
                parser[section_name]["ciphertext"] = _b64encode(ciphertext)

            try:
                _write_key_file_atomic(
                    staged_key_path,
                    _b64encode(new_key),
                    mode=self.settings.credential_file_key_file_mode,
                    owner=self.settings.credential_file_owner,
                    group=self.settings.credential_file_group,
                )
                # Point of no return: the credential file now requires the new
                # key.  Everything above only touched an in-memory parser and a
                # staging file that has not replaced the real key file yet.
                self._write_parser(parser)
            except (CredentialBackendError, CredentialConfigurationError):
                staged_key_path.unlink(missing_ok=True)
                raise

            try:
                staged_key_path.replace(key_file)
                self._protect_key_file(key_file, new_file=True)
            except OSError as exc:
                raise CredentialBackendError(
                    "The encrypted credential file was rotated to the new key, "
                    f"but replacing the key file failed: {exc}. Recover by "
                    f"moving {staged_key_path} to {key_file}."
                ) from exc

        return len(rotated)
