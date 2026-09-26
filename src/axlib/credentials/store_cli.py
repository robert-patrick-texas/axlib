# Copyright 2026 Robert Patrick
# SPDX-License-Identifier: Apache-2.0

"""One administration command shared by both encrypted credential stores.

``axlib credential-db`` (SQLite) and ``axlib credential-file`` (encrypted text
file) intentionally offer identical verbs -- ``init``, ``add``, ``update``,
``delete``, ``list``, and ``rotate-key`` -- so staff can move between stores
without relearning anything.  This module implements that command once,
parameterized by :class:`~axlib.credentials.admin.StoreKind`; the two public
command modules are thin wrappers that pick a store.

The command is deliberately a thin layer over the Python API in
:mod:`axlib.credentials.admin`: it parses arguments, collects values securely,
and prints ``key=value`` status lines that are easy to test and to consume from
shell scripts.  Credential values are never printed.

Dependencies:
    The Python standard library plus :mod:`axlib.credentials.admin`.

Example:
    >>> from axlib.credentials.admin import StoreKind
    >>> build_arg_parser(StoreKind.SQLITE).prog
    'axlib credential-db'
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import TextIO

from .admin import ChangeResult, StoreAdmin, StoreKind, StoreRecord, prepare_update
from .cli_common import collect_values, normalize_service_for_write, render_table
from .exceptions import CredentialError, CredentialRecordNotFoundError
from .profiles import DEFAULT_PROFILE, PROFILES, get_profile, validate_values
from .settings import load_settings


def _add_value_arguments(parser: argparse.ArgumentParser) -> None:
    """Register the secure value-input options shared by ``add`` and ``update``.

    Args:
        parser (argparse.ArgumentParser): Sub-command parser to extend.

    Returns:
        None: Arguments are registered on ``parser`` in place.

    Raises:
        None: :mod:`argparse` accepts these fixed option definitions.
    """
    parser.add_argument("--service", required=True, help="Operator or service name.")
    parser.add_argument(
        "--profile",
        choices=sorted(PROFILES),
        default=DEFAULT_PROFILE.name,
        help=(
            "Record profile that decides which fields are allowed "
            f"(default: {DEFAULT_PROFILE.name}; see python -m "
            "axlib.credentials.profiles)."
        ),
    )
    parser.add_argument(
        "--set",
        action="append",
        default=[],
        metavar="FIELD=VALUE",
        help=(
            "Set one field directly. Repeats are allowed; prefer --prompt or "
            "--from-env for passwords because shell history is often kept."
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
        help="Validate and show field names without changing the store.",
    )


def build_arg_parser(kind: StoreKind) -> argparse.ArgumentParser:
    """Build the administration command parser for one store.

    Args:
        kind (StoreKind): Store the command administers.

    Returns:
        argparse.ArgumentParser: Parser for ``init``, ``add``, ``update``,
            ``delete``, ``list``, and ``rotate-key``.

    Raises:
        None: Parser construction does not open credential backends.
    """
    parser = argparse.ArgumentParser(
        prog=f"axlib {kind.command}",
        description=(
            "Manage AES-256-GCM encrypted credential records in the "
            f"{kind.label} store."
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
        help=f"Create or verify the configured {kind.label} store.",
    )
    initialize.add_argument(
        "--generate-key",
        action="store_true",
        help=(
            f"Create the configured {kind.config_section}.key_file with a random "
            "AES-256 key. An existing key file is never overwritten."
        ),
    )

    add = subparsers.add_parser("add", help="Add a new service record.")
    _add_value_arguments(add)

    update = subparsers.add_parser(
        "update",
        help="Change or remove fields in an existing service record.",
    )
    _add_value_arguments(update)
    update.add_argument(
        "--remove",
        action="append",
        default=[],
        metavar="FIELD",
        help="Remove one optional field, such as netenable. Repeats are allowed.",
    )

    delete = subparsers.add_parser("delete", help="Delete one complete service record.")
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


def render_records_json(records: Sequence[StoreRecord], *, stream: TextIO) -> None:
    """Write safe record metadata as a JSON array.

    Args:
        records (Sequence[StoreRecord]): Metadata returned by a store.
        stream (TextIO): Destination for the JSON text.

    Returns:
        None: A formatted JSON array is written to ``stream``.

    Raises:
        OSError: If ``stream`` cannot be written.
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


def render_records_table(records: Sequence[StoreRecord], *, stream: TextIO) -> None:
    """Write safe record metadata as an aligned text table.

    Args:
        records (Sequence[StoreRecord]): Metadata returned by a store.
        stream (TextIO): Destination for the table.

    Returns:
        None: A header and one line per record are written to ``stream``.

    Raises:
        OSError: If ``stream`` cannot be written.
    """
    render_table(
        ("SERVICE", "FIELDS", "CREATED_UTC", "UPDATED_UTC"),
        [
            (
                record.service,
                ",".join(record.fields),
                record.created_at,
                record.updated_at,
            )
            for record in records
        ],
        stream=stream,
    )


def _print_lines(stream: TextIO, **lines: str) -> None:
    """Print ``key=value`` status lines, skipping empty values.

    Args:
        stream (TextIO): Destination for the lines.
        **lines (str): Keys and values in the order they should appear.

    Returns:
        None: One line per non-empty value is written to ``stream``.

    Raises:
        OSError: If ``stream`` cannot be written.
    """
    for key, value in lines.items():
        if value:
            print(f"{key}={value}", file=stream)


def _report_change(result: ChangeResult, *, kind: StoreKind, stream: TextIO) -> int:
    """Print the outcome of a change, warning when the cache may be stale.

    Args:
        result (ChangeResult): Outcome returned by :class:`StoreAdmin`.
        kind (StoreKind): Store that was changed, used in the error prefix.
        stream (TextIO): Destination for the status lines.

    Returns:
        int: ``0`` normally, or ``1`` when the change was stored but the Redis
            cache could not be cleared.

    Raises:
        OSError: If an output stream cannot be written.
    """
    print(f"status={result.action}", file=stream)
    if result.cache_error is None:
        return 0
    # The durable store *did* change, so say so clearly; the non-zero exit
    # still tells scripts that something needs attention.
    print("cache=stale", file=stream)
    print(
        f"axlib-{kind.command}: record {result.action}, but the Redis cache could "
        f"not be cleared: {result.cache_error}",
        file=sys.stderr,
    )
    return 1


def _run_init(admin: StoreAdmin, args: argparse.Namespace, stream: TextIO) -> int:
    """Handle ``init``.

    Args:
        admin (StoreAdmin): Store administrator.
        args (argparse.Namespace): Parsed arguments.
        stream (TextIO): Destination for status lines.

    Returns:
        int: ``0`` after the store is created or verified.

    Raises:
        CredentialError: If the key or store cannot be created or verified.
    """
    admin.initialize(generate_key=args.generate_key)
    print("status=initialized", file=stream)
    return 0


def _run_list(admin: StoreAdmin, args: argparse.Namespace, stream: TextIO) -> int:
    """Handle ``list``.

    Args:
        admin (StoreAdmin): Store administrator.
        args (argparse.Namespace): Parsed arguments.
        stream (TextIO): Destination for the listing.

    Returns:
        int: ``0`` after the listing is printed.

    Raises:
        CredentialError: If the store cannot be opened or decrypted.
    """
    records = admin.list_records()
    render = render_records_json if args.json else render_records_table
    render(records, stream=stream)
    return 0


def _run_rotate(admin: StoreAdmin, args: argparse.Namespace, stream: TextIO) -> int:
    """Handle ``rotate-key``.

    Args:
        admin (StoreAdmin): Store administrator.
        args (argparse.Namespace): Parsed arguments.
        stream (TextIO): Destination for status lines.

    Returns:
        int: ``0`` after a dry run or a successful rotation.

    Raises:
        ValueError: If ``--yes`` was not given for a real rotation.
        CredentialError: If the new key or the store is unusable.
    """
    # Reading the replacement key first validates it even during a dry run.
    new_key = (
        None if args.new_key_file is None else admin.load_key_file(args.new_key_file)
    )
    _print_lines(stream, action="rotate-key", records=str(len(admin.list_records())))
    if args.dry_run:
        print("dry_run=true", file=stream)
        return 0
    if not args.yes:
        raise ValueError(
            "rotate-key requires --yes to confirm re-encrypting every record."
        )
    rotated = admin.rotate_key(new_key)
    _print_lines(stream, status="rotated", rotated=str(rotated))
    return 0


def _run_add_or_update(
    admin: StoreAdmin, args: argparse.Namespace, stream: TextIO
) -> int:
    """Handle ``add`` and ``update``.

    Args:
        admin (StoreAdmin): Store administrator.
        args (argparse.Namespace): Parsed arguments.
        stream (TextIO): Destination for status lines.

    Returns:
        int: ``0`` on success, or ``1`` if the cache could not be cleared.

    Raises:
        ValueError: If names, fields, or prompted values are invalid.
        EOFError: If a secure prompt cannot read from the terminal.
        CredentialError: If the store cannot be written.
    """
    creating = args.action == "add"
    profile = get_profile(args.profile)
    service = normalize_service_for_write(args.service)
    values = collect_values(args.set, args.from_env, args.prompt)
    # Validate with the same functions StoreAdmin uses, *before* printing the
    # plan, so a dry run reports exactly the errors a real run would.
    if creating:
        values, removing = validate_values(values, profile, creating=True), ()
    else:
        values, removing = prepare_update(values, args.remove, profile)

    _print_lines(
        stream,
        action=args.action,
        service=service,
        fields=",".join(sorted(values)),
        removed=",".join(removing),
    )
    if args.dry_run:
        print("dry_run=true", file=stream)
        return 0
    if creating:
        result = admin.add(service, values, profile=profile)
    else:
        result = admin.update(service, values, remove=removing, profile=profile)
    return _report_change(result, kind=admin.kind, stream=stream)


def _run_delete(admin: StoreAdmin, args: argparse.Namespace, stream: TextIO) -> int:
    """Handle ``delete``.

    Args:
        admin (StoreAdmin): Store administrator.
        args (argparse.Namespace): Parsed arguments.
        stream (TextIO): Destination for status lines.

    Returns:
        int: ``0`` when deleted, or ``1`` when the service was not found or
            the cache could not be cleared.

    Raises:
        ValueError: If the name is invalid or ``--yes`` was not given.
        CredentialError: If the store cannot be written.
    """
    service = normalize_service_for_write(args.service)
    _print_lines(stream, action="delete", service=service)
    if args.dry_run:
        print("dry_run=true", file=stream)
        return 0
    if not args.yes:
        raise ValueError(
            "delete requires --yes to confirm the complete record removal."
        )
    try:
        result = admin.delete(service)
    except CredentialRecordNotFoundError:
        print("status=not-found", file=stream)
        return 1
    return _report_change(result, kind=admin.kind, stream=stream)


# A dispatch table maps each sub-command to its handler.  Adding a verb means
# adding one handler and one entry here, with no growing if/elif chain.
_HANDLERS = {
    "init": _run_init,
    "list": _run_list,
    "rotate-key": _run_rotate,
    "add": _run_add_or_update,
    "update": _run_add_or_update,
    "delete": _run_delete,
}


def main(
    kind: StoreKind,
    argv: Sequence[str] | None = None,
    *,
    stream: TextIO | None = None,
) -> int:
    """Run one administration sub-command against one store.

    Args:
        kind (StoreKind): Store to administer.
        argv (Sequence[str] | None): Arguments excluding the executable name.
        stream (TextIO | None): Destination for non-secret output; defaults to
            standard output.

    Returns:
        int: ``0`` on success or ``1`` for a credential backend failure.

    Raises:
        SystemExit: If :mod:`argparse` rejects the command line or a value is
            invalid.  Console entry points translate this to exit status ``2``.
    """
    parser = build_arg_parser(kind)
    args = parser.parse_args(argv)
    output = sys.stdout if stream is None else stream
    try:
        admin = StoreAdmin(load_settings(args.config), kind)
        return _HANDLERS[args.action](admin, args, output)
    except (ValueError, EOFError) as exc:
        # parser.error() prints usage plus the message and exits with status 2,
        # the conventional code for "the command line was wrong".
        parser.error(str(exc))
    except CredentialError as exc:
        print(f"axlib-{kind.command}: {exc}", file=sys.stderr)
        return 1
