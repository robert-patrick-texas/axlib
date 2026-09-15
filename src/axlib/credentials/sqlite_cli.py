# Copyright 2026 Robert Patrick
# SPDX-License-Identifier: Apache-2.0

"""Manage axlib's AES-256-GCM encrypted SQLite credential database.

This command-line utility gives Network Operations staff explicit lifecycle
commands for the local credential store: initialize the database, add a network
service, update fields, delete a service, and list safe record metadata.  Secret
values can be read from environment variables or entered with terminal echo
disabled, which avoids placing passwords in shell history or screen recordings.

The command never displays ``netpass`` or ``netenable`` values.  Listing shows
only normalized service names, encrypted field names, and audit timestamps.  A
successful change invalidates the complete Redis hash for that service so a
subsequent automation run cannot use stale plaintext cache data.

Dependencies:
    Python's :mod:`sqlite3`, the ``cryptography`` package for AES-256-GCM, and
    ``redis`` only when cache invalidation is enabled.

Example:
    Initialize and add the current operator::

        python -m axlib credential-db init --generate-key
        python -m axlib credential-db add --service "$USER" \
            --set netuser=operator-login --prompt netpass --prompt netenable
"""

from __future__ import annotations

import argparse
import json
import secrets
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import TextIO

from .cli_common import collect_values, normalize_service_for_write
from .exceptions import CredentialError
from .providers import RedisCredentialCache
from .settings import CredentialSettings, load_settings
from .sqlite_store import (
    KEY_LENGTH,
    SQLiteCredentialRecord,
    SQLiteCredentialStore,
    generate_sqlite_key_file,
    read_sqlite_key,
)

NETWORK_FIELDS = frozenset({"netuser", "netpass", "netenable"})


def _add_value_arguments(parser: argparse.ArgumentParser) -> None:
    """Add the shared secure value-input options to a subcommand parser.

    Args:
        parser (argparse.ArgumentParser): ``add`` or ``update`` parser to extend.

    Returns:
        None: Arguments are registered on ``parser`` in place.

    Raises:
        None: :mod:`argparse` accepts these fixed option definitions.
    """
    parser.add_argument(
        "--set",
        action="append",
        default=[],
        metavar="FIELD=VALUE",
        help=(
            "Set netuser, netpass, or netenable directly. Repeats are allowed; "
            "prefer --prompt or --from-env for passwords."
        ),
    )
    parser.add_argument(
        "--from-env",
        action="append",
        default=[],
        metavar="FIELD=ENV_VAR",
        help="Read one field from an existing environment variable.",
    )
    parser.add_argument(
        "--prompt",
        action="append",
        default=[],
        metavar="FIELD",
        help="Prompt for one field with terminal echo disabled.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate and show field names without changing the database.",
    )


def build_arg_parser() -> argparse.ArgumentParser:
    """Build the SQLite credential-management command parser.

    Args:
        None: Command names and options are defined by this module.

    Returns:
        argparse.ArgumentParser: Parser for ``init``, ``add``, ``update``,
            ``delete``, and ``list`` actions.

    Raises:
        None: Parser construction does not open credential backends.
    """
    parser = argparse.ArgumentParser(
        prog="axlib credential-db",
        description=(
            "Manage AES-256-GCM encrypted network credential records in SQLite."
        ),
    )
    parser.add_argument(
        "--config",
        type=Path,
        help="Optional axlib TOML configuration file.",
    )
    subparsers = parser.add_subparsers(dest="action", required=True)

    initialize = subparsers.add_parser(
        "init",
        help="Create or verify the configured database schema.",
    )
    initialize.add_argument(
        "--generate-key",
        action="store_true",
        help=(
            "Create the configured sqlite.key_file with a random AES-256 key. "
            "An existing key file is never overwritten."
        ),
    )

    add = subparsers.add_parser("add", help="Add a new network service record.")
    add.add_argument("--service", required=True, help="Operator or service name.")
    _add_value_arguments(add)

    update = subparsers.add_parser(
        "update",
        help="Update fields in an existing service record.",
    )
    update.add_argument("--service", required=True, help="Operator or service name.")
    _add_value_arguments(update)

    delete = subparsers.add_parser(
        "delete",
        help="Delete one complete service record.",
    )
    delete.add_argument("--service", required=True, help="Operator or service name.")
    delete.add_argument(
        "--yes",
        action="store_true",
        help="Confirm the irreversible record deletion.",
    )
    delete.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate and display the target without deleting it.",
    )

    listing = subparsers.add_parser(
        "list",
        help="List service names and safe metadata, never credential values.",
    )
    listing.add_argument(
        "--json",
        action="store_true",
        help="Render a machine-readable JSON array.",
    )

    rotate = subparsers.add_parser(
        "rotate-key",
        help="Re-encrypt every record under a new AES-256 key.",
    )
    key_source = rotate.add_mutually_exclusive_group(required=True)
    key_source.add_argument(
        "--generate-key",
        action="store_true",
        help="Generate a new random AES-256 key.",
    )
    key_source.add_argument(
        "--new-key-file",
        type=Path,
        help="Read the new AES-256 key from an existing protected file.",
    )
    rotate.add_argument(
        "--yes",
        action="store_true",
        help="Confirm re-encrypting every record with the new key.",
    )
    rotate.add_argument(
        "--dry-run",
        action="store_true",
        help="Show how many records would be rotated without changing anything.",
    )
    return parser


def _collect_network_values(
    direct_assignments: Sequence[str],
    environment_assignments: Sequence[str],
    prompt_fields: Sequence[str],
    *,
    require_login: bool,
) -> dict[str, str]:
    """Collect and validate network fields for an add or update operation.

    Args:
        direct_assignments (Sequence[str]): Repeated ``FIELD=VALUE`` options.
        environment_assignments (Sequence[str]): Repeated ``FIELD=ENV_VAR``
            options.
        prompt_fields (Sequence[str]): Fields read with terminal echo disabled.
        require_login (bool): Require both ``netuser`` and ``netpass`` for a new
            service record.

    Returns:
        dict[str, str]: Validated field names mapped to exact plaintext values.

    Raises:
        ValueError: If no values are supplied, an unsupported field is used, or
            a new record lacks ``netuser`` or ``netpass``.
        EOFError: If a secure prompt cannot read from the terminal.
    """
    values = collect_values(
        direct_assignments,
        environment_assignments,
        prompt_fields,
    )
    if not values:
        raise ValueError("Specify at least one --set, --from-env, or --prompt field.")

    unsupported = sorted(set(values).difference(NETWORK_FIELDS))
    if unsupported:
        raise ValueError(
            "SQLite network records support only netuser, netpass, and "
            f"netenable; received: {', '.join(unsupported)}"
        )
    if require_login:
        missing = sorted({"netuser", "netpass"}.difference(values))
        if missing:
            raise ValueError(
                "A new service requires netuser and netpass; missing: "
                + ", ".join(missing)
            )
    return values


def _print_change_plan(
    action: str,
    service: str,
    values: Mapping[str, str],
    *,
    stream: TextIO,
) -> None:
    """Print a safe change summary containing field names but no values.

    Args:
        action (str): Operation name such as ``add`` or ``update``.
        service (str): Normalized service that will be changed.
        values (Mapping[str, str]): Fields affected by the operation.
        stream (TextIO): Destination for the non-secret summary.

    Returns:
        None: Safe plan text is written to ``stream``.

    Raises:
        OSError: If the output stream cannot be written.
    """
    print(f"action={action}", file=stream)
    print(f"service={service}", file=stream)
    if values:
        print(f"fields={','.join(sorted(values))}", file=stream)


def _invalidate_cache(settings: CredentialSettings, service: str) -> None:
    """Delete a complete Redis service hash after SQLite changes.

    Args:
        settings (CredentialSettings): Redis enablement and connection options.
        service (str): Normalized service whose cached plaintext may be stale.

    Returns:
        None: Redis is unchanged when disabled; otherwise its hash is deleted.

    Raises:
        CredentialError: If enabled Redis cannot connect or delete the hash.
    """
    if not settings.redis_enabled:
        return
    # Invalidating the full hash also clears fields removed by a future schema
    # change, whereas deleting only the fields supplied to this command could
    # leave stale lower-priority values in the cache.
    with RedisCredentialCache(settings) as cache:
        cache.delete(service)


def _initialize_store(
    settings: CredentialSettings,
    *,
    generate_key: bool,
) -> SQLiteCredentialStore:
    """Optionally create the configured key file and initialize SQLite.

    Args:
        settings (CredentialSettings): SQLite database and key configuration.
        generate_key (bool): Create ``settings.sqlite_key_file`` before opening
            the database.

    Returns:
        SQLiteCredentialStore: Initialized provider ready for CRUD operations.

    Raises:
        CredentialError: If the key cannot be created or the database cannot be
            initialized.
    """
    if generate_key:
        if settings.sqlite_key is not None:
            raise CredentialError(
                "--generate-key cannot be used while AXLIB_SQLITE_KEY is set; "
                "the environment key would take precedence over the new file."
            )
        if settings.sqlite_key_file is None:
            raise CredentialError(
                "--generate-key requires sqlite.key_file or AXLIB_SQLITE_KEY_FILE."
            )
        generate_sqlite_key_file(
            settings.sqlite_key_file,
            mode=settings.sqlite_key_file_mode,
            owner=settings.sqlite_owner,
            group=settings.sqlite_group,
        )

    store = SQLiteCredentialStore(settings)
    store.initialize()
    return store


def _render_records_json(
    records: Sequence[SQLiteCredentialRecord],
    *,
    stream: TextIO,
) -> None:
    """Render safe SQLite record metadata as JSON.

    Args:
        records (Sequence[SQLiteCredentialRecord]): Safe metadata objects
            returned by the provider.
        stream (TextIO): Destination for serialized JSON.

    Returns:
        None: A formatted JSON array is written to ``stream``.

    Raises:
        TypeError: If a caller supplies objects without expected attributes.
        OSError: If the destination stream cannot be written.
    """
    payload = [
        {
            "service": record.service,
            "fields": list(record.fields),
            "created_at": record.created_at,
            "updated_at": record.updated_at,
        }
        for record in records
    ]
    print(json.dumps(payload, indent=2, sort_keys=True), file=stream)


def _render_records_table(
    records: Sequence[SQLiteCredentialRecord],
    *,
    stream: TextIO,
) -> None:
    """Render safe SQLite record metadata as an aligned text table.

    Args:
        records (Sequence[SQLiteCredentialRecord]): Safe metadata objects
            returned by the provider.
        stream (TextIO): Destination for the operator-facing table.

    Returns:
        None: A header and zero or more rows are written to ``stream``.

    Raises:
        TypeError: If a caller supplies objects without expected attributes.
        OSError: If the destination stream cannot be written.
    """
    rows = [
        (
            record.service,
            ",".join(record.fields),
            record.created_at,
            record.updated_at,
        )
        for record in records
    ]
    headers = ("SERVICE", "FIELDS", "CREATED_UTC", "UPDATED_UTC")
    widths = [
        max(len(headers[index]), *(len(row[index]) for row in rows))
        if rows
        else len(headers[index])
        for index in range(len(headers))
    ]
    print(
        "  ".join(header.ljust(widths[index]) for index, header in enumerate(headers)),
        file=stream,
    )
    for row in rows:
        print(
            "  ".join(value.ljust(widths[index]) for index, value in enumerate(row)),
            file=stream,
        )


def main(
    argv: Sequence[str] | None = None,
    *,
    stream: TextIO | None = None,
) -> int:
    """Run one SQLite credential database lifecycle operation.

    Args:
        argv (Sequence[str] | None): Arguments excluding the executable name.
        stream (TextIO | None): Destination for non-secret output; defaults to
            standard output.

    Returns:
        int: ``0`` on success or ``1`` for a credential backend failure.

    Raises:
        SystemExit: If :mod:`argparse` rejects invalid command-line syntax or
            values.  Console entry points translate this to exit status ``2``.
    """
    parser = build_arg_parser()
    args = parser.parse_args(argv)
    output = sys.stdout if stream is None else stream

    try:
        settings = load_settings(args.config)

        if args.action == "init":
            _initialize_store(settings, generate_key=args.generate_key)
            print("status=initialized", file=output)
            return 0

        store = SQLiteCredentialStore(settings)
        if args.action == "list":
            records = store.list_records()
            if args.json:
                _render_records_json(records, stream=output)
            else:
                _render_records_table(records, stream=output)
            return 0

        if args.action == "rotate-key":
            if args.new_key_file is not None:
                new_key = read_sqlite_key(args.new_key_file)
            else:
                new_key = secrets.token_bytes(KEY_LENGTH)
            records = store.list_records()
            print("action=rotate-key", file=output)
            print(f"records={len(records)}", file=output)
            if args.dry_run:
                print("dry_run=true", file=output)
                return 0
            if not args.yes:
                parser.error(
                    "rotate-key requires --yes to confirm re-encrypting every record."
                )
            rotated = store.rotate_key(new_key)
            print("status=rotated", file=output)
            print(f"rotated={rotated}", file=output)
            return 0

        service = normalize_service_for_write(args.service)
        if args.action in {"add", "update"}:
            try:
                values = _collect_network_values(
                    args.set,
                    args.from_env,
                    args.prompt,
                    require_login=args.action == "add",
                )
            except (ValueError, EOFError) as exc:
                parser.error(str(exc))

            _print_change_plan(args.action, service, values, stream=output)
            if args.dry_run:
                print("dry_run=true", file=output)
                return 0
            if args.action == "add":
                store.create(service, values)
            else:
                store.update(service, values)
            _invalidate_cache(settings, service)
            status = "added" if args.action == "add" else "updated"
            print(f"status={status}", file=output)
            return 0

        _print_change_plan("delete", service, {}, stream=output)
        if args.dry_run:
            print("dry_run=true", file=output)
            return 0
        if not args.yes:
            parser.error(
                "delete requires --yes to confirm the complete record removal."
            )
        deleted = store.delete_service(service)
        if not deleted:
            print("status=not-found", file=output)
            return 1
        _invalidate_cache(settings, service)
        print("status=deleted", file=output)
        return 0
    except ValueError as exc:
        parser.error(str(exc))
    except CredentialError as exc:
        print(f"axlib-credential-db: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
