"""Encrypted SQLite credential storage for Network Operations automation.

This module adds a local, serverless credential backend for operators who need
a structured local store but do not want network
passwords stored as SQLite plaintext.  Each service record is serialized as JSON
and encrypted with AES-256-GCM before it is written to SQLite.  AES-GCM provides
both confidentiality and an authentication tag, so a changed ciphertext, wrong
key, or record moved to a different service name is rejected during decryption.

The service name and timestamps remain visible so administrators can safely list
records without revealing usernames or passwords.  The credential payload may
contain fields such as ``netuser``, ``netpass``, and ``netenable``.  The module
uses parameterized SQL, explicit transactions, short lock timeouts, configurable
owner/group protection, and context-managed database connections.  Shared Linux
hosts default to a ``0660`` database owned by group ``netops``; private hosts can
select ``0600`` and omit the group.  These are important guardrails for
automation hosts where several scripts may run at once.

Dependencies:
    Python's :mod:`sqlite3` module and the third-party ``cryptography`` package.
    A 32-byte key, encoded with URL-safe Base64, must be supplied through
    ``AXLIB_SQLITE_KEY`` or a protected file selected by
    ``AXLIB_SQLITE_KEY_FILE`` / ``sqlite.key_file``.

Example:
    >>> from pathlib import Path
    >>> from axlib.credentials.settings import CredentialSettings
    >>> from axlib.credentials.sqlite_store import SQLiteCredentialStore
    >>> settings = CredentialSettings(  # doctest: +SKIP
    ...     sqlite_database=Path("credentials.db"),
    ...     sqlite_key="base64-encoded-32-byte-key",
    ... )
    >>> store = SQLiteCredentialStore(settings)  # doctest: +SKIP
    >>> store.initialize()  # doctest: +SKIP
"""

from __future__ import annotations

import base64
import binascii
import json
import os
import secrets
import sqlite3
import stat
import tempfile
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

try:
    import grp
    import pwd
except ImportError:  # pragma: no cover - POSIX ownership is a Linux feature.
    grp = None  # type: ignore[assignment]
    pwd = None  # type: ignore[assignment]

from .exceptions import (
    CredentialBackendError,
    CredentialConfigurationError,
    CredentialDependencyError,
    CredentialRecordExistsError,
    CredentialRecordNotFoundError,
)
from .settings import CredentialSettings, parse_file_mode

APPLICATION_ID = 0x41584C49  # ASCII-like marker for "AXLI" within SQLite limits.
SCHEMA_VERSION = 1
NONCE_LENGTH = 12
KEY_LENGTH = 32
KEY_CHECK_PLAINTEXT = b"axlib-sqlite-key-check-v1"
KEY_CHECK_AAD = b"axlib:sqlite:key-check:v1"
RECORD_AAD_PREFIX = b"axlib:sqlite:credential:v1\x00"
DEFAULT_DATABASE_MODE = 0o660
DEFAULT_KEY_FILE_MODE = 0o640
DEFAULT_SQLITE_GROUP = "netops"


@dataclass(frozen=True, slots=True)
class SQLiteCredentialRecord:
    """Safe metadata describing one encrypted SQLite service record.

    Attributes:
        service: Normalized lookup name used by ``axlib.getkeys()``.
        fields: Sorted encrypted field names; secret values are never included.
        created_at: UTC timestamp recorded when the service was added.
        updated_at: UTC timestamp recorded after the latest update.
    """

    service: str
    fields: tuple[str, ...]
    created_at: str
    updated_at: str


def _load_aesgcm() -> tuple[type[Any], type[BaseException]]:
    """Load AES-GCM classes only when encrypted SQLite storage is used.

    Args:
        None: The dependency is imported from the active Python environment.

    Returns:
        tuple[type[Any], type[BaseException]]: ``AESGCM`` and ``InvalidTag``
            classes from ``cryptography``.

    Raises:
        CredentialDependencyError: If the ``cryptography`` package is not
            installed on the automation host.
    """
    try:
        from cryptography.exceptions import InvalidTag
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    except ImportError as exc:
        raise CredentialDependencyError(
            "SQLite credential encryption requires the 'cryptography' package."
        ) from exc
    return AESGCM, InvalidTag


def _utc_timestamp() -> str:
    """Return a compact UTC timestamp for credential-audit metadata.

    Args:
        None: The current system clock is read directly.

    Returns:
        str: ISO 8601 UTC timestamp ending in ``Z``.

    Raises:
        None: :func:`datetime.now` and formatting are deterministic operations.
    """
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _decode_key_text(value: str, *, source: str) -> bytes:
    """Decode and validate one URL-safe Base64 AES-256 key.

    Args:
        value (str): Base64 text from the environment or a protected key file.
        source (str): Operator-facing source name used in validation errors.

    Returns:
        bytes: Exactly 32 bytes suitable for AES-256-GCM.

    Raises:
        CredentialConfigurationError: If the text is not valid Base64 or does
            not decode to a 256-bit key.
    """
    text = value.strip()
    if not text:
        raise CredentialConfigurationError(f"SQLite encryption key is empty: {source}")

    try:
        key = base64.b64decode(
            text.encode("ascii"),
            altchars=b"-_",
            validate=True,
        )
    except (UnicodeEncodeError, binascii.Error, ValueError) as exc:
        raise CredentialConfigurationError(
            f"SQLite encryption key from {source} is not valid URL-safe Base64."
        ) from exc

    if len(key) != KEY_LENGTH:
        raise CredentialConfigurationError(
            f"SQLite encryption key from {source} must decode to exactly "
            f"{KEY_LENGTH} bytes for AES-256-GCM; received {len(key)} bytes."
        )
    return key


def _read_key_file(path: Path) -> str:
    """Read Base64 key text from a protected local file.

    Args:
        path (pathlib.Path): Key file selected by credential configuration.

    Returns:
        str: Key text with surrounding line whitespace removed.

    Raises:
        CredentialConfigurationError: If the file is missing, unreadable, or
            contains no key text.
    """
    try:
        # A context manager closes the sensitive descriptor immediately, which
        # is preferable on long-running automation workers and jump hosts.
        with path.open("r", encoding="ascii") as handle:
            value = handle.read().strip()
    except (OSError, UnicodeError) as exc:
        raise CredentialConfigurationError(
            f"Unable to read SQLite encryption key file {path}: {exc}"
        ) from exc
    if not value:
        raise CredentialConfigurationError(
            f"SQLite encryption key file is empty: {path}"
        )
    return value


def _format_mode(mode: int) -> str:
    """Render a POSIX permission mode using familiar four-digit octal text.

    Args:
        mode (int): Permission bits such as ``0o660`` for a shared credential
            database.

    Returns:
        str: Four-digit representation such as ``"0660"``.

    Raises:
        None: Python renders the supplied integer deterministically.
    """
    return f"{mode:04o}"


def _resolve_owner_id(owner: str | None) -> int | None:
    """Resolve an optional POSIX user name to its numeric user ID.

    Args:
        owner (str | None): Configured owner name, or ``None`` to preserve the
            operating system's current file owner.

    Returns:
        int | None: Numeric user ID, or ``None`` when no owner is enforced.

    Raises:
        CredentialConfigurationError: If POSIX ownership is unavailable or the
            configured account does not exist on the automation host.
    """
    if owner is None:
        return None
    if pwd is None:
        raise CredentialConfigurationError(
            "SQLite owner enforcement requires a POSIX operating system."
        )
    try:
        return int(pwd.getpwnam(owner).pw_uid)
    except KeyError as exc:
        raise CredentialConfigurationError(
            f"Configured SQLite owner does not exist: {owner!r}"
        ) from exc


def _resolve_group_id(group: str | None) -> int | None:
    """Resolve an optional POSIX group name to its numeric group ID.

    Args:
        group (str | None): Configured group such as ``"netops"``, or ``None``
            to preserve the file's current group.

    Returns:
        int | None: Numeric group ID, or ``None`` when no group is enforced.

    Raises:
        CredentialConfigurationError: If POSIX group lookup is unavailable or
            the configured group does not exist on the automation host.
    """
    if group is None:
        return None
    if grp is None:
        raise CredentialConfigurationError(
            "SQLite group enforcement requires a POSIX operating system."
        )
    try:
        return int(grp.getgrnam(group).gr_gid)
    except KeyError as exc:
        raise CredentialConfigurationError(
            f"Configured SQLite group does not exist: {group!r}"
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
    """Validate and optionally correct one credential file's POSIX protection.

    The function changes a mode or owner only when the current metadata differs.
    That detail allows a second member of ``netops`` to use a correctly protected
    shared database without trying an unnecessary ``chmod`` that only the owner
    or root could perform.

    Args:
        path (pathlib.Path): Existing database or encryption-key file.
        mode (int): Exact POSIX permission bits to enforce.
        owner (str | None): Optional expected owner account.
        group (str | None): Optional expected group account.
        enforce (bool): Correct mismatched metadata when ``True``.  Symlinks and
            non-regular files are rejected even when correction is disabled.
        label (str): Human-readable file role used in error messages.

    Returns:
        None: The path is a regular file and, when enabled, has the requested
            owner, group, and mode.

    Raises:
        CredentialConfigurationError: If a configured owner or group is unknown.
        CredentialBackendError: If the path is unsafe or metadata cannot be read,
            changed, or verified.
    """
    try:
        current = path.lstat()
    except OSError as exc:
        raise CredentialBackendError(
            f"Unable to inspect {label} {path}: {exc}"
        ) from exc

    if stat.S_ISLNK(current.st_mode):
        raise CredentialBackendError(
            f"{label.capitalize()} must not be a symlink: {path}"
        )
    if not stat.S_ISREG(current.st_mode):
        raise CredentialBackendError(
            f"{label.capitalize()} is not a regular file: {path}"
        )
    if not enforce:
        return

    expected_uid = _resolve_owner_id(owner)
    expected_gid = _resolve_group_id(group)
    requested_uid = expected_uid if expected_uid != current.st_uid else None
    requested_gid = expected_gid if expected_gid != current.st_gid else None

    try:
        if requested_uid is not None or requested_gid is not None:
            # Passing -1 preserves an identity component that is not configured.
            # A normal operator can change a file to a group they belong to; an
            # explicit owner change generally requires administrative privilege.
            os.chown(
                path,
                -1 if requested_uid is None else requested_uid,
                -1 if requested_gid is None else requested_gid,
                follow_symlinks=False,
            )
        if stat.S_IMODE(current.st_mode) != mode:
            os.chmod(path, mode, follow_symlinks=False)
        verified = path.lstat()
    except OSError as exc:
        raise CredentialBackendError(
            f"Unable to enforce {_format_mode(mode)} permissions and configured "
            f"ownership on {label} {path}: {exc}"
        ) from exc

    if stat.S_IMODE(verified.st_mode) != mode:
        raise CredentialBackendError(
            f"Unable to verify {_format_mode(mode)} mode on {label} {path}."
        )
    if expected_uid is not None and verified.st_uid != expected_uid:
        raise CredentialBackendError(f"Unable to verify owner on {label} {path}.")
    if expected_gid is not None and verified.st_gid != expected_gid:
        raise CredentialBackendError(f"Unable to verify group on {label} {path}.")


def _prepare_parent_directory(
    path: Path,
    *,
    mode: int,
    owner: str | None,
    group: str | None,
) -> None:
    """Create a missing parent directory with suitable shared-host protection.

    Existing directories are intentionally left unchanged because production
    paths such as ``/etc/axlib`` may be managed by package policy, ACLs, or a
    configuration-management system.  Administrators should pre-create those
    directories when site-specific access rules are required.

    Args:
        path (pathlib.Path): File whose immediate parent must exist.
        mode (int): Mode applied only when this function creates the directory.
        owner (str | None): Optional owner for a newly created directory.
        group (str | None): Optional group for a newly created directory.

    Returns:
        None: The parent exists and is a directory.

    Raises:
        CredentialConfigurationError: If a configured owner or group is unknown.
        CredentialBackendError: If the parent cannot be created or protected.
    """
    parent = path.parent
    if parent.exists():
        if not parent.is_dir():
            raise CredentialBackendError(
                f"Credential file parent is not a directory: {parent}"
            )
        return

    try:
        parent.mkdir(parents=True, exist_ok=True, mode=mode)
        uid = _resolve_owner_id(owner)
        gid = _resolve_group_id(group)
        if uid is not None or gid is not None:
            os.chown(parent, -1 if uid is None else uid, -1 if gid is None else gid)
        os.chmod(parent, mode)
    except (CredentialConfigurationError, CredentialBackendError):
        raise
    except OSError as exc:
        raise CredentialBackendError(
            f"Unable to create credential directory {parent}: {exc}"
        ) from exc


def generate_sqlite_key_file(
    path: Path,
    *,
    overwrite: bool = False,
    mode: int = DEFAULT_KEY_FILE_MODE,
    owner: str | None = None,
    group: str | None = DEFAULT_SQLITE_GROUP,
) -> str:
    """Generate a random AES-256 key and save it with protected ownership.

    Args:
        path (pathlib.Path): Destination file used later by
            ``sqlite.key_file`` or ``AXLIB_SQLITE_KEY_FILE``.
        overwrite (bool): Whether an existing key file may be replaced.  The
            default is ``False`` because replacing a key makes existing records
            undecryptable.
        mode (int): Exact POSIX file mode.  The shared-host default is ``0640``
            so ``netops`` members can read but not casually modify the key.
        owner (str | None): Optional POSIX owner account.  ``None`` retains the
            user creating the file.
        group (str | None): Optional POSIX group account.  The default is
            ``netops``; set ``None`` with mode ``0600`` for a private store.

    Returns:
        str: URL-safe Base64 key text that was written to ``path``.  Callers
            should not print this value in terminal or CI logs.

    Raises:
        CredentialConfigurationError: If the destination exists without
            ``overwrite=True`` or a configured owner/group is unknown.
        CredentialDependencyError: If ``cryptography`` is unavailable.
        CredentialBackendError: If the key file cannot be created or protected.
    """
    mode = parse_file_mode(
        mode,
        name="SQLite key-file mode",
        default=DEFAULT_KEY_FILE_MODE,
        require_owner_write=False,
    )
    aesgcm_class, _ = _load_aesgcm()
    key_text = base64.urlsafe_b64encode(
        aesgcm_class.generate_key(bit_length=256)
    ).decode("ascii")

    path = path.expanduser()
    existed_before = path.exists() or path.is_symlink()
    # Resolve identities before creating/truncating the only key capable of
    # opening the database.  A misspelled group should fail without touching it.
    _resolve_owner_id(owner)
    _resolve_group_id(group)
    # The set-group-ID bit makes any future file created in a package-created
    # directory inherit the configured group instead of an operator's primary
    # group.  This is especially important for SQLite rollback-journal files.
    parent_mode = 0o2750 if group is not None else 0o700
    _prepare_parent_directory(
        path,
        mode=parent_mode,
        owner=owner,
        group=group,
    )

    try:
        # O_EXCL prevents an initialization typo from silently replacing the one
        # key capable of decrypting every existing service record.
        flags = os.O_WRONLY | os.O_CREAT
        flags |= os.O_TRUNC if overwrite else os.O_EXCL
        flags |= getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(path, flags, mode)
        with os.fdopen(descriptor, "w", encoding="ascii", newline="\n") as handle:
            handle.write(f"{key_text}\n")
        _protect_regular_file(
            path,
            mode=mode,
            owner=owner,
            group=group,
            enforce=True,
            label="SQLite encryption key file",
        )
    except FileExistsError as exc:
        raise CredentialConfigurationError(
            f"SQLite key file already exists and was not replaced: {path}"
        ) from exc
    except (CredentialConfigurationError, CredentialBackendError):
        if not existed_before:
            path.unlink(missing_ok=True)
        raise
    except OSError as exc:
        if not existed_before:
            path.unlink(missing_ok=True)
        raise CredentialBackendError(
            f"Unable to create SQLite key file {path}: {exc}"
        ) from exc
    return key_text


def read_sqlite_key(path: Path) -> bytes:
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
            label="new SQLite encryption key",
        )
        os.replace(temp_path, path)
        temp_path = None
    except (CredentialConfigurationError, CredentialBackendError):
        raise
    except OSError as exc:
        raise CredentialBackendError(
            f"Unable to write SQLite encryption key file {path}: {exc}"
        ) from exc
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)


class SQLiteCredentialStore:
    """Store encrypted credential mappings in a local SQLite database."""

    def __init__(self, settings: CredentialSettings) -> None:
        """Create a SQLite provider without opening the database yet.

        Args:
            settings (CredentialSettings): Database path, AES key source, and
                SQLite lock timeout used by this provider.

        Returns:
            None: Initializers configure the object in place.

        Raises:
            CredentialConfigurationError: If a configured database or key-file
                mode is unsafe or malformed.  Path, key, and schema checks are
                otherwise deferred until an operation.
        """
        parse_file_mode(
            settings.sqlite_database_mode,
            name="SQLite database mode",
            default=DEFAULT_DATABASE_MODE,
            require_owner_write=True,
        )
        parse_file_mode(
            settings.sqlite_key_file_mode,
            name="SQLite key-file mode",
            default=DEFAULT_KEY_FILE_MODE,
            require_owner_write=False,
        )
        self.settings = settings

    def _protect_database_file(self, path: Path, *, new_file: bool = False) -> None:
        """Apply configured mode and ownership to the SQLite database file.

        Args:
            path (pathlib.Path): Existing SQLite database path.
            new_file (bool): Force protection for a newly created file even when
                ongoing permission enforcement is disabled.

        Returns:
            None: The database is safe to open under the configured policy.

        Raises:
            CredentialConfigurationError: If a configured owner/group is unknown.
            CredentialBackendError: If the file is unsafe or cannot be protected.
        """
        _protect_regular_file(
            path,
            mode=self.settings.sqlite_database_mode,
            owner=self.settings.sqlite_owner,
            group=self.settings.sqlite_group,
            enforce=new_file or self.settings.sqlite_enforce_permissions,
            label="SQLite credential database",
        )

    def _protect_key_file(self, path: Path, *, new_file: bool = False) -> None:
        """Apply configured mode and ownership to the AES-256 key file.

        Args:
            path (pathlib.Path): Existing file containing the Base64 key.
            new_file (bool): Force protection for a newly created file even when
                ongoing permission enforcement is disabled.

        Returns:
            None: The key file is safe to read under the configured policy.

        Raises:
            CredentialConfigurationError: If a configured owner/group is unknown.
            CredentialBackendError: If the file is unsafe or cannot be protected.
        """
        _protect_regular_file(
            path,
            mode=self.settings.sqlite_key_file_mode,
            owner=self.settings.sqlite_owner,
            group=self.settings.sqlite_group,
            enforce=new_file or self.settings.sqlite_enforce_permissions,
            label="SQLite encryption key file",
        )

    def _database_path(self) -> Path:
        """Return the configured database path or raise a clear setup error.

        Args:
            None: The path is read from this provider's settings.

        Returns:
            pathlib.Path: Configured SQLite database location.

        Raises:
            CredentialConfigurationError: If no SQLite database path is set.
        """
        if self.settings.sqlite_database is None:
            raise CredentialConfigurationError(
                "No SQLite credential database is configured. Set "
                "AXLIB_SQLITE_DATABASE or sqlite.database."
            )
        database_path = self.settings.sqlite_database.expanduser()
        if self.settings.sqlite_key_file is not None:
            # Keeping the key and database in distinct files prevents a simple
            # configuration typo from replacing one security boundary with the
            # other during database initialization or key generation.
            database_resolved = database_path.expanduser().resolve(strict=False)
            key_resolved = self.settings.sqlite_key_file.expanduser().resolve(
                strict=False
            )
            if database_resolved == key_resolved:
                raise CredentialConfigurationError(
                    "SQLite database and encryption key file must be different "
                    f"paths: {database_path}"
                )
        return database_path

    def _resolve_key(self) -> bytes:
        """Resolve the AES-256 key from runtime settings or a protected file.

        Args:
            None: Key sources are read from this provider's settings.

        Returns:
            bytes: Validated 32-byte AES-256 key.

        Raises:
            CredentialConfigurationError: If neither key source is configured,
                the file is unreadable, or the key has the wrong format.
            CredentialBackendError: If the key file is unsafe or its configured
                ownership and mode cannot be enforced.
        """
        if self.settings.sqlite_key is not None:
            return _decode_key_text(
                self.settings.sqlite_key,
                source="AXLIB_SQLITE_KEY",
            )
        if self.settings.sqlite_key_file is not None:
            key_path = self.settings.sqlite_key_file.expanduser()
            self._protect_key_file(key_path)
            key_text = _read_key_file(key_path)
            return _decode_key_text(
                key_text,
                source=str(key_path),
            )
        raise CredentialConfigurationError(
            "No SQLite encryption key is configured. Set AXLIB_SQLITE_KEY_FILE "
            "or inject AXLIB_SQLITE_KEY at runtime."
        )

    @contextmanager
    def _connect(
        self,
        *,
        read_only: bool,
        allow_create: bool = False,
    ) -> Iterator[sqlite3.Connection]:
        """Open and always close one SQLite connection.

        Args:
            read_only (bool): Open with SQLite ``mode=ro`` when the operation
                should never change data.
            allow_create (bool): Permit SQLite to open a newly created database
                file.  Only :meth:`initialize` should set this to ``True``;
                CRUD operations require initialization first.

        Returns:
            Iterator[sqlite3.Connection]: Context-managed database connection.

        Raises:
            CredentialBackendError: If the database is missing when creation is
                disallowed, file protection fails, or SQLite cannot open the
                selected path.
            CredentialConfigurationError: If the database path, owner, group, or
                permission settings are invalid.
        """
        path = self._database_path()
        if path.exists() or path.is_symlink():
            self._protect_database_file(path)
        if not path.is_file() and not allow_create:
            raise CredentialBackendError(
                f"SQLite credential database does not exist: {path}. "
                "Initialize it with 'axlib credential-db init'."
            )

        database: str
        uri = False
        if read_only:
            # SQLite's read-only URI prevents a lookup path from accidentally
            # creating or modifying a database because of a caller typo.
            database = f"{path.resolve().as_uri()}?mode=ro"
            uri = True
        else:
            database = str(path)

        try:
            connection = sqlite3.connect(
                database,
                timeout=self.settings.sqlite_timeout,
                isolation_level=None,
                uri=uri,
            )
        except sqlite3.Error as exc:
            raise CredentialBackendError(
                f"Unable to open SQLite credential database {path}: {exc}"
            ) from exc

        try:
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute(
                f"PRAGMA busy_timeout = {int(self.settings.sqlite_timeout * 1000)}"
            )
            if read_only:
                connection.execute("PRAGMA query_only = ON")
            else:
                # secure_delete overwrites deleted SQLite cells where practical.
                # Ciphertext is stored rather than plaintext, but this additional
                # cleanup is still useful when a record is rotated or removed.
                connection.execute("PRAGMA secure_delete = ON")
            yield connection
        finally:
            connection.close()

    def _aesgcm(self) -> Any:
        """Build an AES-GCM object using the configured 256-bit key.

        Args:
            None: The key is resolved from provider settings.

        Returns:
            Any: ``cryptography`` AESGCM instance.

        Raises:
            CredentialConfigurationError: If the key is absent or invalid.
            CredentialDependencyError: If ``cryptography`` is unavailable.
        """
        aesgcm_class, _ = _load_aesgcm()
        return aesgcm_class(self._resolve_key())

    @staticmethod
    def _record_aad(service: str) -> bytes:
        """Bind encrypted payload integrity to its service name.

        Args:
            service (str): Normalized operator or automation service key.

        Returns:
            bytes: Associated authenticated data passed to AES-GCM.

        Raises:
            UnicodeEncodeError: If Python cannot encode the service as UTF-8.
        """
        return RECORD_AAD_PREFIX + service.encode("utf-8")

    @staticmethod
    def _validate_service(service: str) -> str:
        """Reject blank service keys before SQL or encryption work begins.

        Args:
            service (str): Normalized service selected by the credential manager.

        Returns:
            str: Unchanged non-blank service key.

        Raises:
            CredentialConfigurationError: If the service is blank.
        """
        if not service:
            raise CredentialConfigurationError(
                "SQLite credential service name cannot be blank."
            )
        return service

    @staticmethod
    def _validate_values(values: Mapping[str, str]) -> dict[str, str]:
        """Copy and validate a plaintext field mapping before encryption.

        Args:
            values (Mapping[str, str]): Fields such as ``netuser``, ``netpass``,
                and ``netenable``.

        Returns:
            dict[str, str]: Plain dictionary safe to serialize as JSON.

        Raises:
            CredentialConfigurationError: If the mapping is empty or contains a
                blank field name or non-string value.
        """
        prepared = dict(values)
        if not prepared:
            raise CredentialConfigurationError(
                "At least one credential field is required."
            )
        for field, value in prepared.items():
            if not isinstance(field, str) or not field:
                raise CredentialConfigurationError(
                    "SQLite credential field names must be non-blank strings."
                )
            if not isinstance(value, str):
                raise CredentialConfigurationError(
                    f"SQLite credential field {field!r} must contain text."
                )
        return prepared

    def _encrypt_values(
        self,
        service: str,
        values: Mapping[str, str],
    ) -> tuple[bytes, bytes]:
        """Serialize and encrypt a complete service payload.

        Args:
            service (str): Normalized service key bound into AES-GCM
                authenticated data.
            values (Mapping[str, str]): Complete field mapping to protect.

        Returns:
            tuple[bytes, bytes]: Fresh 12-byte nonce and authenticated
                ciphertext containing the GCM tag.

        Raises:
            CredentialConfigurationError: If values cannot be validated or the
                configured key is invalid.
            CredentialDependencyError: If ``cryptography`` is unavailable.
            CredentialBackendError: If JSON serialization or encryption fails.
        """
        prepared = self._validate_values(values)
        try:
            plaintext = json.dumps(
                prepared,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
            # A new random 96-bit nonce is mandatory for every AES-GCM write.
            # Reusing a nonce with the same key would compromise confidentiality.
            nonce = secrets.token_bytes(NONCE_LENGTH)
            ciphertext = self._aesgcm().encrypt(
                nonce,
                plaintext,
                self._record_aad(service),
            )
            return nonce, ciphertext
        except (CredentialConfigurationError, CredentialDependencyError):
            raise
        except Exception as exc:
            raise CredentialBackendError(
                f"Unable to encrypt SQLite credentials for service {service!r}: "
                f"{exc}"
            ) from exc

    def _decrypt_values(
        self,
        service: str,
        nonce: bytes,
        ciphertext: bytes,
    ) -> dict[str, str]:
        """Authenticate, decrypt, and validate one service payload.

        Args:
            service (str): Service name used as AES-GCM authenticated data.
            nonce (bytes): 12-byte nonce stored with this record.
            ciphertext (bytes): Encrypted JSON plus its GCM authentication tag.

        Returns:
            dict[str, str]: Decrypted credential fields.

        Raises:
            CredentialBackendError: If integrity validation fails or plaintext
                does not contain a string-to-string JSON object.
            CredentialConfigurationError: If the configured key is invalid.
            CredentialDependencyError: If ``cryptography`` is unavailable.
        """
        _, invalid_tag_class = _load_aesgcm()
        try:
            plaintext = self._aesgcm().decrypt(
                bytes(nonce),
                bytes(ciphertext),
                self._record_aad(service),
            )
            decoded = json.loads(plaintext.decode("utf-8"))
        except invalid_tag_class as exc:
            raise CredentialBackendError(
                f"SQLite credential authentication failed for service {service!r}; "
                "the key is wrong or the record was modified."
            ) from exc
        except (CredentialConfigurationError, CredentialDependencyError):
            raise
        except (UnicodeError, json.JSONDecodeError, TypeError, ValueError) as exc:
            raise CredentialBackendError(
                f"SQLite credential payload for service {service!r} is invalid."
            ) from exc
        except Exception as exc:
            raise CredentialBackendError(
                f"Unable to decrypt SQLite credentials for service {service!r}: "
                f"{exc}"
            ) from exc

        if not isinstance(decoded, dict) or not all(
            isinstance(field, str) and field and isinstance(value, str)
            for field, value in decoded.items()
        ):
            raise CredentialBackendError(
                f"SQLite credential payload for service {service!r} has an "
                "unsupported structure."
            )
        return dict(decoded)

    def _verify_key_check(self, nonce: bytes, ciphertext: bytes) -> None:
        """Confirm that the configured key opens the database key marker.

        Args:
            nonce (bytes): Nonce stored in the singleton metadata row.
            ciphertext (bytes): Encrypted key-check marker.

        Returns:
            None: Successful authentication proves the configured key matches.

        Raises:
            CredentialBackendError: If the key is wrong or metadata was changed.
            CredentialConfigurationError: If the configured key is malformed.
            CredentialDependencyError: If ``cryptography`` is unavailable.
        """
        _, invalid_tag_class = _load_aesgcm()
        try:
            plaintext = self._aesgcm().decrypt(
                bytes(nonce),
                bytes(ciphertext),
                KEY_CHECK_AAD,
            )
        except invalid_tag_class as exc:
            raise CredentialBackendError(
                "SQLite credential database key validation failed; the configured "
                "key does not match this database or metadata was modified."
            ) from exc
        except (CredentialConfigurationError, CredentialDependencyError):
            raise
        except Exception as exc:
            raise CredentialBackendError(
                "SQLite credential database key-check metadata is invalid."
            ) from exc
        if plaintext != KEY_CHECK_PLAINTEXT:
            raise CredentialBackendError(
                "SQLite credential database key-check marker is invalid."
            )

    def _verify_database(self, connection: sqlite3.Connection) -> None:
        """Verify SQLite identity, schema version, and encryption key.

        Args:
            connection (sqlite3.Connection): Open database connection.

        Returns:
            None: The database is ready for encrypted credential operations.

        Raises:
            CredentialBackendError: If the path is not an initialized axlib
                database, the schema version is unsupported, or the key fails.
        """
        try:
            application_id = int(
                connection.execute("PRAGMA application_id").fetchone()[0]
            )
            user_version = int(
                connection.execute("PRAGMA user_version").fetchone()[0]
            )
            if application_id != APPLICATION_ID:
                raise CredentialBackendError(
                    "Selected SQLite file is not an axlib credential database."
                )
            if user_version != SCHEMA_VERSION:
                raise CredentialBackendError(
                    "Unsupported SQLite credential schema version "
                    f"{user_version}; expected {SCHEMA_VERSION}."
                )
            row = connection.execute(
                "SELECT schema_version, key_check_nonce, key_check_ciphertext "
                "FROM axlib_metadata WHERE singleton = 1"
            ).fetchone()
        except sqlite3.Error as exc:
            raise CredentialBackendError(
                f"Unable to verify SQLite credential database schema: {exc}"
            ) from exc

        try:
            if row is None or int(row["schema_version"]) != SCHEMA_VERSION:
                raise CredentialBackendError(
                    "SQLite credential database metadata is missing or incompatible."
                )
            nonce = bytes(row["key_check_nonce"])
            ciphertext = bytes(row["key_check_ciphertext"])
        except CredentialBackendError:
            raise
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise CredentialBackendError(
                "SQLite credential database metadata is malformed."
            ) from exc
        self._verify_key_check(nonce, ciphertext)

    def initialize(self) -> None:
        """Create a new SQLite store or verify the exact supported schema.

        Existing databases are never migrated. A pre-existing file must already
        carry the current axlib application ID, schema version, metadata row, and
        matching AES key. Only a database file created by this call is allowed to
        begin at SQLite version zero before the current schema is installed.

        Args:
            None: Database and key locations come from provider settings.

        Returns:
            None: A missing database is created; an existing current database is
                verified and its configured file protection is enforced.

        Raises:
            CredentialConfigurationError: If path or AES key configuration is
                missing or invalid.
            CredentialDependencyError: If ``cryptography`` is unavailable.
            CredentialBackendError: If SQLite cannot create/verify the schema,
                an existing database version is unsupported, or the key is wrong.
        """
        path = self._database_path()
        self._resolve_key()
        if self.settings.sqlite_group is not None and (
            self.settings.sqlite_database_mode & 0o020
        ):
            parent_mode = 0o2770
        elif self.settings.sqlite_group is not None and (
            self.settings.sqlite_database_mode & 0o040
        ):
            parent_mode = 0o2750
        else:
            parent_mode = 0o700
        _prepare_parent_directory(
            path,
            mode=parent_mode,
            owner=self.settings.sqlite_owner,
            group=self.settings.sqlite_group,
        )

        created_file = False
        if not path.exists():
            try:
                flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
                flags |= getattr(os, "O_NOFOLLOW", 0)
                descriptor = os.open(path, flags, self.settings.sqlite_database_mode)
                os.close(descriptor)
                created_file = True
                self._protect_database_file(path, new_file=True)
            except (CredentialConfigurationError, CredentialBackendError):
                if created_file:
                    path.unlink(missing_ok=True)
                raise
            except OSError as exc:
                if created_file:
                    path.unlink(missing_ok=True)
                raise CredentialBackendError(
                    f"Unable to create SQLite credential database {path}: {exc}"
                ) from exc
        elif not path.is_file():
            raise CredentialBackendError(
                f"SQLite credential database path is not a regular file: {path}"
            )

        try:
            with self._connect(read_only=False, allow_create=created_file) as connection:
                connection.execute("PRAGMA journal_mode = DELETE")
                if not created_file:
                    # Verification-only behavior is intentional. A schema from an
                    # older axlib release must be recreated by the operator rather
                    # than silently modified by application startup.
                    self._verify_database(connection)
                    self._protect_database_file(path)
                    return

                connection.execute("BEGIN IMMEDIATE")
                try:
                    connection.execute(
                        "CREATE TABLE axlib_metadata ("
                        "singleton INTEGER PRIMARY KEY CHECK (singleton = 1), "
                        "schema_version INTEGER NOT NULL, "
                        "key_check_nonce BLOB NOT NULL "
                        f"CHECK (length(key_check_nonce) = {NONCE_LENGTH}), "
                        "key_check_ciphertext BLOB NOT NULL, "
                        "created_at TEXT NOT NULL)"
                    )
                    connection.execute(
                        "CREATE TABLE credential_records ("
                        "service TEXT PRIMARY KEY NOT NULL "
                        "CHECK (length(service) > 0), "
                        "nonce BLOB NOT NULL "
                        f"CHECK (length(nonce) = {NONCE_LENGTH}), "
                        "ciphertext BLOB NOT NULL "
                        "CHECK (length(ciphertext) >= 16), "
                        "created_at TEXT NOT NULL, "
                        "updated_at TEXT NOT NULL)"
                    )
                    nonce = secrets.token_bytes(NONCE_LENGTH)
                    ciphertext = self._aesgcm().encrypt(
                        nonce, KEY_CHECK_PLAINTEXT, KEY_CHECK_AAD
                    )
                    connection.execute(
                        "INSERT INTO axlib_metadata ("
                        "singleton, schema_version, key_check_nonce, "
                        "key_check_ciphertext, created_at) VALUES (1, ?, ?, ?, ?)",
                        (SCHEMA_VERSION, nonce, ciphertext, _utc_timestamp()),
                    )
                    connection.execute(f"PRAGMA application_id = {APPLICATION_ID}")
                    connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
                    connection.commit()
                except Exception:
                    connection.rollback()
                    raise
            self._protect_database_file(path, new_file=True)
        except (
            CredentialBackendError,
            CredentialConfigurationError,
            CredentialDependencyError,
        ):
            if created_file:
                path.unlink(missing_ok=True)
            raise
        except (OSError, sqlite3.Error) as exc:
            if created_file:
                path.unlink(missing_ok=True)
            raise CredentialBackendError(
                f"Unable to initialize SQLite credential database {path}: {exc}"
            ) from exc

    def read(self, service: str, fields: Sequence[str]) -> dict[str, str]:
        """Read selected fields from one encrypted service record.

        Args:
            service (str): Normalized operator or shared-service lookup key.
            fields (Sequence[str]): Requested names such as ``netuser``,
                ``netpass``, and ``netenable``.

        Returns:
            dict[str, str]: Requested fields present in the encrypted record, or
                an empty dictionary when the service does not exist.

        Raises:
            CredentialBackendError: If SQLite, schema, key, or record integrity
                validation fails.
            CredentialConfigurationError: If path or key settings are invalid.
            CredentialDependencyError: If ``cryptography`` is unavailable.
        """
        service = self._validate_service(service)
        if not fields:
            return {}
        try:
            with self._connect(read_only=True) as connection:
                self._verify_database(connection)
                row = connection.execute(
                    "SELECT nonce, ciphertext FROM credential_records "
                    "WHERE service = ?",
                    (service,),
                ).fetchone()
        except (CredentialBackendError, CredentialConfigurationError):
            raise
        except sqlite3.Error as exc:
            raise CredentialBackendError(
                f"Unable to read SQLite credentials for service {service!r}: {exc}"
            ) from exc

        if row is None:
            return {}
        values = self._decrypt_values(
            service,
            bytes(row["nonce"]),
            bytes(row["ciphertext"]),
        )
        return {field: values[field] for field in fields if field in values}

    def create(self, service: str, values: Mapping[str, str]) -> None:
        """Add a new encrypted service record and reject duplicates.

        Args:
            service (str): Normalized operator or automation service name.
            values (Mapping[str, str]): Complete initial credential mapping.

        Returns:
            None: One encrypted row is inserted.

        Raises:
            CredentialRecordExistsError: If ``service`` already exists.
            CredentialBackendError: If SQLite or encryption fails.
            CredentialConfigurationError: If path, key, service, or values are
                invalid.
            CredentialDependencyError: If ``cryptography`` is unavailable.
        """
        service = self._validate_service(service)
        nonce, ciphertext = self._encrypt_values(service, values)
        timestamp = _utc_timestamp()
        try:
            with self._connect(read_only=False) as connection:
                self._verify_database(connection)
                connection.execute("BEGIN IMMEDIATE")
                try:
                    connection.execute(
                        "INSERT INTO credential_records ("
                        "service, nonce, ciphertext, created_at, updated_at) "
                        "VALUES (?, ?, ?, ?, ?)",
                        (service, nonce, ciphertext, timestamp, timestamp),
                    )
                    connection.commit()
                except sqlite3.IntegrityError as exc:
                    connection.rollback()
                    raise CredentialRecordExistsError(
                        f"SQLite credential service already exists: {service!r}"
                    ) from exc
                except Exception:
                    connection.rollback()
                    raise
        except (
            CredentialRecordExistsError,
            CredentialBackendError,
            CredentialConfigurationError,
            CredentialDependencyError,
        ):
            raise
        except sqlite3.Error as exc:
            raise CredentialBackendError(
                f"Unable to add SQLite credential service {service!r}: {exc}"
            ) from exc

    def update(self, service: str, values: Mapping[str, str]) -> None:
        """Merge fields into an existing encrypted service record.

        Args:
            service (str): Normalized service that must already exist.
            values (Mapping[str, str]): Fields to add or replace.  Password text
                is preserved exactly, including leading or trailing spaces.

        Returns:
            None: The record is re-encrypted with a fresh nonce.

        Raises:
            CredentialRecordNotFoundError: If ``service`` does not exist.
            CredentialBackendError: If SQLite, decryption, or encryption fails.
            CredentialConfigurationError: If path, key, service, or values are
                invalid.
            CredentialDependencyError: If ``cryptography`` is unavailable.
        """
        service = self._validate_service(service)
        updates = self._validate_values(values)
        try:
            with self._connect(read_only=False) as connection:
                self._verify_database(connection)
                # BEGIN IMMEDIATE reserves the write lock before reading the old
                # value, preventing another process from rotating the same
                # service between this read and its replacement.
                connection.execute("BEGIN IMMEDIATE")
                try:
                    row = connection.execute(
                        "SELECT nonce, ciphertext FROM credential_records "
                        "WHERE service = ?",
                        (service,),
                    ).fetchone()
                    if row is None:
                        raise CredentialRecordNotFoundError(
                            f"SQLite credential service does not exist: {service!r}"
                        )
                    merged = self._decrypt_values(
                        service,
                        bytes(row["nonce"]),
                        bytes(row["ciphertext"]),
                    )
                    merged.update(updates)
                    nonce, ciphertext = self._encrypt_values(service, merged)
                    connection.execute(
                        "UPDATE credential_records SET nonce = ?, ciphertext = ?, "
                        "updated_at = ? WHERE service = ?",
                        (nonce, ciphertext, _utc_timestamp(), service),
                    )
                    connection.commit()
                except Exception:
                    connection.rollback()
                    raise
        except (
            CredentialRecordNotFoundError,
            CredentialBackendError,
            CredentialConfigurationError,
            CredentialDependencyError,
        ):
            raise
        except sqlite3.Error as exc:
            raise CredentialBackendError(
                f"Unable to update SQLite credential service {service!r}: {exc}"
            ) from exc

    def write(self, service: str, values: Mapping[str, str]) -> None:
        """Atomically create a service or merge fields when it already exists.

        Args:
            service (str): Normalized service name used by axlib lookup.
            values (Mapping[str, str]): Fields to create or update.

        Returns:
            None: The encrypted database contains the supplied values.

        Raises:
            CredentialBackendError: If SQLite, decryption, or encryption fails.
            CredentialConfigurationError: If path, key, service, or values are
                invalid.
            CredentialDependencyError: If ``cryptography`` is unavailable.
        """
        service = self._validate_service(service)
        updates = self._validate_values(values)
        try:
            with self._connect(read_only=False) as connection:
                self._verify_database(connection)
                connection.execute("BEGIN IMMEDIATE")
                try:
                    row = connection.execute(
                        "SELECT nonce, ciphertext, created_at "
                        "FROM credential_records WHERE service = ?",
                        (service,),
                    ).fetchone()
                    timestamp = _utc_timestamp()
                    if row is None:
                        merged = updates
                        created_at = timestamp
                    else:
                        merged = self._decrypt_values(
                            service,
                            bytes(row["nonce"]),
                            bytes(row["ciphertext"]),
                        )
                        merged.update(updates)
                        created_at = str(row["created_at"])
                    nonce, ciphertext = self._encrypt_values(service, merged)
                    connection.execute(
                        "INSERT INTO credential_records ("
                        "service, nonce, ciphertext, created_at, updated_at) "
                        "VALUES (?, ?, ?, ?, ?) "
                        "ON CONFLICT(service) DO UPDATE SET "
                        "nonce = excluded.nonce, "
                        "ciphertext = excluded.ciphertext, "
                        "updated_at = excluded.updated_at",
                        (service, nonce, ciphertext, created_at, timestamp),
                    )
                    connection.commit()
                except Exception:
                    connection.rollback()
                    raise
        except (
            CredentialBackendError,
            CredentialConfigurationError,
            CredentialDependencyError,
        ):
            raise
        except sqlite3.Error as exc:
            raise CredentialBackendError(
                f"Unable to write SQLite credential service {service!r}: {exc}"
            ) from exc

    def delete_service(self, service: str) -> bool:
        """Delete one complete encrypted service record.

        Args:
            service (str): Normalized service to remove from the database.

        Returns:
            bool: ``True`` when a row was deleted, otherwise ``False``.

        Raises:
            CredentialBackendError: If SQLite or schema validation fails.
            CredentialConfigurationError: If path, key, or service is invalid.
            CredentialDependencyError: If ``cryptography`` is unavailable.
        """
        service = self._validate_service(service)
        try:
            with self._connect(read_only=False) as connection:
                self._verify_database(connection)
                connection.execute("BEGIN IMMEDIATE")
                try:
                    cursor = connection.execute(
                        "DELETE FROM credential_records WHERE service = ?",
                        (service,),
                    )
                    connection.commit()
                    return cursor.rowcount > 0
                except Exception:
                    connection.rollback()
                    raise
        except (
            CredentialBackendError,
            CredentialConfigurationError,
            CredentialDependencyError,
        ):
            raise
        except sqlite3.Error as exc:
            raise CredentialBackendError(
                f"Unable to delete SQLite credential service {service!r}: {exc}"
            ) from exc

    def delete(self, service: str, fields: Sequence[str]) -> None:
        """Remove selected fields and delete the row if no fields remain.

        Args:
            service (str): Normalized service whose encrypted payload is changed.
            fields (Sequence[str]): Field names to remove.

        Returns:
            None: The record is re-encrypted or removed in place.

        Raises:
            CredentialRecordNotFoundError: If ``service`` does not exist.
            CredentialBackendError: If SQLite, decryption, or encryption fails.
            CredentialConfigurationError: If path, key, or service is invalid.
            CredentialDependencyError: If ``cryptography`` is unavailable.
        """
        service = self._validate_service(service)
        if not fields:
            return
        try:
            with self._connect(read_only=False) as connection:
                self._verify_database(connection)
                connection.execute("BEGIN IMMEDIATE")
                try:
                    row = connection.execute(
                        "SELECT nonce, ciphertext FROM credential_records "
                        "WHERE service = ?",
                        (service,),
                    ).fetchone()
                    if row is None:
                        raise CredentialRecordNotFoundError(
                            f"SQLite credential service does not exist: {service!r}"
                        )
                    values = self._decrypt_values(
                        service,
                        bytes(row["nonce"]),
                        bytes(row["ciphertext"]),
                    )
                    for field in fields:
                        values.pop(field, None)
                    if not values:
                        connection.execute(
                            "DELETE FROM credential_records WHERE service = ?",
                            (service,),
                        )
                    else:
                        nonce, ciphertext = self._encrypt_values(service, values)
                        connection.execute(
                            "UPDATE credential_records SET nonce = ?, "
                            "ciphertext = ?, updated_at = ? WHERE service = ?",
                            (nonce, ciphertext, _utc_timestamp(), service),
                        )
                    connection.commit()
                except Exception:
                    connection.rollback()
                    raise
        except (
            CredentialRecordNotFoundError,
            CredentialBackendError,
            CredentialConfigurationError,
            CredentialDependencyError,
        ):
            raise
        except sqlite3.Error as exc:
            raise CredentialBackendError(
                f"Unable to delete SQLite fields for service {service!r}: {exc}"
            ) from exc

    def list_records(self) -> list[SQLiteCredentialRecord]:
        """List service metadata without returning credential values.

        Args:
            None: All rows in the configured database are inspected.

        Returns:
            list[SQLiteCredentialRecord]: Service names, encrypted field names,
                and audit timestamps ordered by service.

        Raises:
            CredentialBackendError: If SQLite, schema, key, or record integrity
                validation fails.
            CredentialConfigurationError: If path or key settings are invalid.
            CredentialDependencyError: If ``cryptography`` is unavailable.
        """
        try:
            with self._connect(read_only=True) as connection:
                self._verify_database(connection)
                rows = connection.execute(
                    "SELECT service, nonce, ciphertext, created_at, updated_at "
                    "FROM credential_records ORDER BY service COLLATE NOCASE"
                ).fetchall()
        except (CredentialBackendError, CredentialConfigurationError):
            raise
        except sqlite3.Error as exc:
            raise CredentialBackendError(
                f"Unable to list SQLite credential services: {exc}"
            ) from exc

        records: list[SQLiteCredentialRecord] = []
        for row in rows:
            service = str(row["service"])
            values = self._decrypt_values(
                service,
                bytes(row["nonce"]),
                bytes(row["ciphertext"]),
            )
            records.append(
                SQLiteCredentialRecord(
                    service=service,
                    fields=tuple(sorted(values)),
                    created_at=str(row["created_at"]),
                    updated_at=str(row["updated_at"]),
                )
            )
        return records

    def rotate_key(self, new_key: bytes) -> int:
        """Re-encrypt every record and the key-check marker under a new key.

        Every service record is decrypted with the currently configured key,
        then re-encrypted with fresh nonces under ``new_key`` inside one SQLite
        write transaction, mirroring how :meth:`update` re-encrypts a single
        record.  Rotation requires ``sqlite.key_file`` /
        ``AXLIB_SQLITE_KEY_FILE`` because axlib cannot update the calling
        shell's environment, so a key supplied only through
        ``AXLIB_SQLITE_KEY`` cannot be rotated in place.

        The key file is replaced only after the database transaction commits,
        which is the earliest point both changes can be made durable together.
        If the process is interrupted between those two steps, the new key
        remains recoverable at ``<key_file>.rotating`` beside the configured
        key file.

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
            CredentialBackendError: If SQLite, decryption, encryption, or
                key-file replacement fails.
            CredentialDependencyError: If ``cryptography`` is unavailable.
        """
        if len(new_key) != KEY_LENGTH:
            raise CredentialConfigurationError(
                f"New SQLite encryption key must be exactly {KEY_LENGTH} bytes "
                f"for AES-256-GCM; received {len(new_key)} bytes."
            )
        if self.settings.sqlite_key is not None:
            raise CredentialConfigurationError(
                "SQLite key rotation requires a configured key file; "
                "AXLIB_SQLITE_KEY cannot be rotated in place because axlib "
                "cannot update the calling shell's environment."
            )
        if self.settings.sqlite_key_file is None:
            raise CredentialConfigurationError(
                "SQLite key rotation requires sqlite.key_file or AXLIB_SQLITE_KEY_FILE."
            )
        key_file = self.settings.sqlite_key_file.expanduser()
        staged_key_path = key_file.with_name(key_file.name + ".rotating")
        if staged_key_path.exists():
            raise CredentialConfigurationError(
                "A previous SQLite key rotation may not have completed: found "
                f"leftover staging file {staged_key_path}. Verify it, then "
                "either move it into place as the key file or remove it "
                "before retrying."
            )

        aesgcm_class, _ = _load_aesgcm()
        new_aesgcm = aesgcm_class(new_key)
        rotated: list[tuple[str, bytes, bytes]] = []

        try:
            with self._connect(read_only=False) as connection:
                self._verify_database(connection)
                connection.execute("BEGIN IMMEDIATE")
                try:
                    rows = connection.execute(
                        "SELECT service, nonce, ciphertext FROM credential_records"
                    ).fetchall()
                    for row in rows:
                        service = str(row["service"])
                        values = self._decrypt_values(
                            service, bytes(row["nonce"]), bytes(row["ciphertext"])
                        )
                        plaintext = json.dumps(
                            values,
                            ensure_ascii=False,
                            separators=(",", ":"),
                            sort_keys=True,
                        ).encode("utf-8")
                        nonce = secrets.token_bytes(NONCE_LENGTH)
                        ciphertext = new_aesgcm.encrypt(
                            nonce, plaintext, self._record_aad(service)
                        )
                        rotated.append((service, nonce, ciphertext))

                    for service, nonce, ciphertext in rotated:
                        connection.execute(
                            "UPDATE credential_records SET nonce = ?, "
                            "ciphertext = ? WHERE service = ?",
                            (nonce, ciphertext, service),
                        )

                    key_nonce = secrets.token_bytes(NONCE_LENGTH)
                    key_ciphertext = new_aesgcm.encrypt(
                        key_nonce, KEY_CHECK_PLAINTEXT, KEY_CHECK_AAD
                    )
                    connection.execute(
                        "UPDATE axlib_metadata SET key_check_nonce = ?, "
                        "key_check_ciphertext = ? WHERE singleton = 1",
                        (key_nonce, key_ciphertext),
                    )

                    _write_key_file_atomic(
                        staged_key_path,
                        base64.urlsafe_b64encode(new_key).decode("ascii"),
                        mode=self.settings.sqlite_key_file_mode,
                        owner=self.settings.sqlite_owner,
                        group=self.settings.sqlite_group,
                    )
                    # Point of no return: committing makes the database require
                    # the new key.  Everything above is still reversible.
                    connection.commit()
                except Exception:
                    connection.rollback()
                    staged_key_path.unlink(missing_ok=True)
                    raise
        except (
            CredentialBackendError,
            CredentialConfigurationError,
            CredentialDependencyError,
        ):
            raise
        except sqlite3.Error as exc:
            raise CredentialBackendError(
                f"Unable to rotate SQLite credential encryption key: {exc}"
            ) from exc

        try:
            os.replace(staged_key_path, key_file)
            self._protect_key_file(key_file, new_file=True)
        except OSError as exc:
            raise CredentialBackendError(
                "The SQLite credential database was rotated to the new key, "
                f"but replacing the key file failed: {exc}. Recover by moving "
                f"{staged_key_path} to {key_file}."
            ) from exc

        return len(rotated)
