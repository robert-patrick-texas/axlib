# Copyright 2026 Robert Patrick
# SPDX-License-Identifier: Apache-2.0

"""Backwards-compatible credential helpers for network automation scripts.

This module preserves the established axlib API used by operator scripts::

    import axlib as ax
    netuser, netpass, netenable = ax.getkeys()

``getkeys()`` uses the explicit service argument when supplied; otherwise it
looks up the current operator from ``$USER``.  Environment variables override
stored values, Redis may supply cached fields, an AES-256-GCM SQLite database is
preferred when enabled, and the axlib-native AES-256-GCM encrypted text file is
used as the lower-priority durable store when enabled. If the operator record
lacks a network username or password, the function retains the historical
fallback to a *configured* shared service account.  The shared service is
selected with ``AXLIB_SHARED_SERVICE`` or ``credentials.shared_service``
rather than being hard-coded in source.

Dependencies:
    ``cryptography`` for AES-256-GCM durable stores and ``redis`` only when
    plaintext caching is enabled.  Redis can use TLS, ACL
    authentication, operation timeouts, and a cache TTL.

CLI example (checks presence without printing secret values):
    ``python -m axlib.secrets network --service "$USER"``

Module example:
    >>> import os
    >>> os.environ["NETUSER"] = "lab-operator"
    >>> os.environ["NETPASS"] = "example-only"
    >>> from axlib.secrets import getnetkeys
    >>> getnetkeys("lab")[:2]
    ('lab-operator', 'example-only')
"""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Mapping, Sequence
from typing import TextIO

from axlib import __version__, config
from axlib.credentials.manager import (
    lookup_values,
    merge_missing,
    normalize_legacy_service_name,
)

# ``__version__`` is imported from the package (see above) rather than written
# here, so older scripts that read ``axlib.secrets.__version__`` always see the
# current release.

__all__ = [
    "__version__",
    "getinfoblox",
    "getkeys",
    "getnetkeys",
    "printkv",
    "readkeyring",
    "servicenamefilter",
    "updatedict",
]


def _environment_value(name: str) -> str | None:
    """Read a credential override while treating blank variables as missing.

    Args:
        name (str): Environment variable such as ``NETUSER`` or ``IBPASS``.

    Returns:
        str | None: Original non-blank value, or ``None`` when the variable is
            absent or contains only whitespace.

    Raises:
        None: :func:`os.getenv` returns either text or ``None``.
    """
    value = os.getenv(name)
    if value is None or not value.strip():
        return None

    # Preserve the exact non-blank text.  Passwords can legitimately contain
    # leading or trailing spaces, so sanitizing them would change credentials
    # before an SSH or API authentication attempt.
    return value


def _configured_shared_service() -> str | None:
    """Return the operator-configured shared fallback service.

    Args:
        None: The value is read from the backwards-compatible config facade.

    Returns:
        str | None: Configured service name, or ``None`` when fallback has not
            been enabled by the deployment.

    Raises:
        None: Blank text is treated as an unset optional value.
    """
    value = config.shared_service
    return value if value is not None and str(value).strip() else None


def readkeyring(**values: str | None) -> dict[str, str | None]:
    """Resolve fields from Redis, preferred SQLite, and encrypted-file fallback.

    Non-``None`` values supplied by the caller are retained.  Missing fields are
    filled first from Redis, then configured SQLite, and finally the configured
    AES-256-GCM text store. Values read from either durable store are cached when
    Redis is enabled.

    Args:
        **values (str | None): Credential fields to resolve.  Include
            ``service="operator-name"`` to select a service; if omitted,
            ``"default"`` is used.  Calling with no fields requests ``secret``.

    Returns:
        dict[str, str | None]: Requested fields with any available values filled
            in.  Missing entries remain ``None`` for compatibility.

    Raises:
        CredentialConfigurationError: Invalid legacy config values may be raised
            while converting them to modern settings.  Backend lookup errors are
            reported to standard error and treated as misses.
    """
    requested = dict(values)
    service = requested.pop("service", "default")
    return lookup_values(
        service,
        requested or {"secret": None},
        settings=config.as_settings(),
    )


def servicenamefilter(service: object = "default") -> str:
    """Return the historical normalized credential service key.

    Dots, dashes, and underscores are removed because the established axlib
    lookup convention and Redis hashes use that rule.  Operators should avoid defining
    service names that collide after this transformation.

    Args:
        service (object): Operator username or logical service name.

    Returns:
        str: Legacy-compatible service key.

    Raises:
        None: Values are converted to text for compatibility with older callers.
    """
    return normalize_legacy_service_name(service)


def updatedict(
    dict1: dict[str, str | None],
    dict2: Mapping[str, str | None],
) -> dict[str, str | None]:
    """Fill ``None`` values in one dictionary from another dictionary.

    Args:
        dict1 (dict[str, str | None]): Higher-priority dictionary modified in
            place.
        dict2 (Mapping[str, str | None]): Lower-priority candidate values.

    Returns:
        dict[str, str | None]: The same ``dict1`` object after missing values are
            filled.

    Raises:
        None: Standard dictionary operations are used without coercion.
    """
    merge_missing(dict1, dict2)
    return dict1


def getkeys(service: str | None = None) -> tuple[str | None, str | None, str | None]:
    """Return network credentials for an operator with shared-service fallback.

    Args:
        service (str | None): Credential service to query.  When omitted, the
            current operator is taken from ``$USER``.

    Returns:
        tuple[str | None, str | None, str | None]: ``(netuser, netpass,
            netenable)`` for logging in to managed network devices.

    Raises:
        CredentialConfigurationError: Invalid local settings may be raised before
            backend access.  Backend failures themselves are reported and return
            unresolved ``None`` values for compatibility.
    """
    operator_service = service or _environment_value("USER")
    shared_service = _configured_shared_service()
    initial_service = operator_service or shared_service
    netuser, netpass, netenable = getnetkeys(initial_service)

    # The fallback is intentionally limited to a missing username or password,
    # matching the historical behavior.  A device enable secret may be optional
    # on privilege-15 accounts and should not force an unrelated account change.
    if netuser and netpass:
        return netuser, netpass, netenable

    requested_key = servicenamefilter(initial_service or "default")
    shared_key = servicenamefilter(shared_service) if shared_service else None

    if shared_service and shared_key != requested_key:
        print(
            "***AX: WARNING: individual network credentials are incomplete for "
            f"service {initial_service or 'default'!r}; trying configured "
            f"shared service {shared_service!r}.",
            file=sys.stderr,
        )
        return getnetkeys(shared_service)

    if shared_service is None:
        print(
            "***AX: WARNING: individual network credentials are incomplete and "
            "no shared fallback service is configured. Set "
            "AXLIB_SHARED_SERVICE or credentials.shared_service.",
            file=sys.stderr,
        )
    return netuser, netpass, netenable


def getnetkeys(
    service: str | None = None,
) -> tuple[str | None, str | None, str | None]:
    """Return network login fields using environment-over-store precedence.

    Args:
        service (str | None): Operator or shared credential service.  When
            omitted, the configured shared service is used; if none is set, the
            generic ``default`` service is queried.

    Returns:
        tuple[str | None, str | None, str | None]: Network username, login
            password, and optional enable password.

    Raises:
        CredentialConfigurationError: Invalid local settings may be raised while
            building the provider configuration.
    """
    selected_service = service or _configured_shared_service() or "default"
    netuser = _environment_value("NETUSER")
    netpass = _environment_value("NETPASS")
    netenable = _environment_value("NETENABLE")

    # Query storage when any field is missing.  The original implementation
    # accidentally checked NETPASS twice and could skip a missing NETENABLE;
    # checking all three fields fixes that bug without changing precedence.
    if netuser is None or netpass is None or netenable is None:
        resolved = readkeyring(
            service=selected_service,
            netuser=netuser,
            netpass=netpass,
            netenable=netenable,
        )
        netuser = resolved["netuser"] if netuser is None else netuser
        netpass = resolved["netpass"] if netpass is None else netpass
        netenable = resolved["netenable"] if netenable is None else netenable

    return netuser, netpass, netenable


def getinfoblox(
    service: str | None = None,
) -> tuple[str | None, str | None, str | None]:
    """Return Infoblox grid and API login fields.

    Args:
        service (str | None): Credential service containing Infoblox fields;
            defaults to ``"infoblox"`` for compatibility.

    Returns:
        tuple[str | None, str | None, str | None]: ``(ibgrid, ibuser, ibpass)``
            for an Infoblox WAPI or SDK connection.

    Raises:
        CredentialConfigurationError: Invalid local settings may be raised while
            building the provider configuration.
    """
    selected_service = service or "infoblox"
    ibgrid = _environment_value("IBGRID")
    ibuser = _environment_value("IBUSER")
    ibpass = _environment_value("IBPASS")

    if ibgrid is None or ibuser is None or ibpass is None:
        resolved = readkeyring(
            service=selected_service,
            ibgrid=ibgrid,
            ibuser=ibuser,
            ibpass=ibpass,
        )
        ibgrid = resolved["ibgrid"] if ibgrid is None else ibgrid
        ibuser = resolved["ibuser"] if ibuser is None else ibuser
        ibpass = resolved["ibpass"] if ibpass is None else ibpass

    return ibgrid, ibuser, ibpass


def printkv(my_dict: Mapping[object, object]) -> None:
    """Print dictionary keys and values for historical debugging compatibility.

    This helper is retained because older scripts may import it.  It must not be
    used with production credentials because terminal output, shell capture, and
    CI logs can persist plaintext secrets.

    Args:
        my_dict (Mapping[object, object]): Mapping whose entries are printed.

    Returns:
        None: Each key and value is written to standard output.

    Raises:
        OSError: If standard output cannot be written.
    """
    for key, value in my_dict.items():
        print(key, value)


def _presence(value: str | None) -> str:
    """Convert a credential value to a non-secret diagnostic label.

    Args:
        value (str | None): Credential field that must not be printed directly.

    Returns:
        str: ``"set"`` when a value exists, otherwise ``"missing"``.

    Raises:
        None: The function performs a truth-value check only.
    """
    return "set" if value else "missing"


def build_arg_parser() -> argparse.ArgumentParser:
    """Build the safe credential-diagnostic command-line parser.

    Args:
        None: Parser configuration is defined by this module.

    Returns:
        argparse.ArgumentParser: Parser for ``network`` and ``infoblox`` checks.

    Raises:
        None: Constructing argparse objects does not access credential backends.
    """
    parser = argparse.ArgumentParser(
        description=(
            "Check whether axlib can resolve credential fields without printing "
            "their plaintext values."
        )
    )
    subparsers = parser.add_subparsers(dest="credential_type", required=True)
    network = subparsers.add_parser("network", help="Check network login fields.")
    network.add_argument("--service", help="Operator or shared service name.")
    infoblox = subparsers.add_parser("infoblox", help="Check Infoblox API fields.")
    infoblox.add_argument("--service", help="Infoblox credential service name.")
    return parser


def main(
    argv: Sequence[str] | None = None,
    *,
    stream: TextIO | None = None,
) -> int:
    """Run a non-secret credential availability check.

    Args:
        argv (Sequence[str] | None): Command-line arguments excluding the program
            name.  Defaults to :data:`sys.argv`.
        stream (TextIO | None): Destination for safe ``set``/``missing`` labels;
            defaults to the current standard output stream.

    Returns:
        int: ``0`` when required login fields are present, otherwise ``1``.

    Raises:
        CredentialConfigurationError: Invalid local settings are allowed to
            surface so operators receive a clear startup failure.
    """
    args = build_arg_parser().parse_args(argv)
    output_stream = sys.stdout if stream is None else stream
    if args.credential_type == "network":
        netuser, netpass, netenable = getkeys(args.service)
        print(f"netuser={_presence(netuser)}", file=output_stream)
        print(f"netpass={_presence(netpass)}", file=output_stream)
        print(f"netenable={_presence(netenable)}", file=output_stream)
        return 0 if netuser and netpass else 1

    ibgrid, ibuser, ibpass = getinfoblox(args.service)
    print(f"ibgrid={_presence(ibgrid)}", file=output_stream)
    print(f"ibuser={_presence(ibuser)}", file=output_stream)
    print(f"ibpass={_presence(ibpass)}", file=output_stream)
    return 0 if ibgrid and ibuser and ibpass else 1


if __name__ == "__main__":
    raise SystemExit(main())
