# Copyright 2026 Robert Patrick
# SPDX-License-Identifier: Apache-2.0

"""Exceptions raised by :mod:`axlib.credentials`.

The credential helpers are used by network-automation scripts before they open
SSH, HTTPS, or API sessions to managed infrastructure.  Distinct exception
classes let a caller distinguish a local configuration problem from a temporary
Redis or encrypted-file problem and decide whether to stop safely or continue
with another credential source.

This module has no third-party dependencies.

Example:
    >>> from axlib.credentials.exceptions import CredentialConfigurationError
    >>> raise CredentialConfigurationError("missing credential-store path")
    Traceback (most recent call last):
    ...
    axlib.credentials.exceptions.CredentialConfigurationError: missing
    credential-store path
"""


class CredentialError(RuntimeError):
    """Base class for credential lookup and storage failures."""


class CredentialConfigurationError(CredentialError):
    """Raised when required credential settings are missing or invalid."""


class CredentialDependencyError(CredentialError):
    """Raised when an optional credential backend package is unavailable."""


class CredentialBackendError(CredentialError):
    """Raised when Redis or an encrypted credential store cannot complete work."""


class CredentialRecordExistsError(CredentialBackendError):
    """Raised when a create operation targets an existing service record."""


class CredentialRecordNotFoundError(CredentialBackendError):
    """Raised when an update requires a service record that does not exist."""
