# Copyright 2026 Robert Patrick
# SPDX-License-Identifier: Apache-2.0

"""Manage axlib's AES-256-GCM encrypted SQLite credential database.

This command-line utility gives Network Operations staff explicit lifecycle
commands for the local credential store: initialize the database, add a
service, update or remove fields, delete a service, list safe record metadata,
and rotate the encryption key.  Secret values can be read from environment
variables or entered with terminal echo disabled, which avoids placing
passwords in shell history or screen recordings.

The command never displays credential values.  Listing shows only normalized
service names, encrypted field names, and audit timestamps.  A successful change
clears that service's Redis cache entry so a later automation run cannot use
stale plaintext cache data.

This module only selects the SQLite store.  The command itself is implemented
once in :mod:`axlib.credentials.store_cli` and shared with
``axlib credential-file``, which is why both offer exactly the same options.

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
from collections.abc import Sequence
from typing import TextIO

from . import store_cli
from .admin import StoreKind


def build_arg_parser() -> argparse.ArgumentParser:
    """Build the SQLite credential-management command parser.

    Args:
        None: Command names and options are defined by :mod:`store_cli`.

    Returns:
        argparse.ArgumentParser: Parser for ``init``, ``add``, ``update``,
            ``delete``, ``list``, and ``rotate-key``.

    Raises:
        None: Parser construction does not open credential backends.
    """
    return store_cli.build_arg_parser(StoreKind.SQLITE)


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
    return store_cli.main(StoreKind.SQLITE, argv, stream=stream)


if __name__ == "__main__":
    raise SystemExit(main())
