"""Educational helpers for Python-based network automation.

Axlib combines streaming text filters with credential lookup utilities designed
for Network Operations staff.  The historical package-root functions remain
available, including the common pattern::

    import axlib as ax
    netuser, netpass, netenable = ax.getkeys()

Credential functions are imported lazily so text-only tools can be studied and
used without opening Redis connections or encrypted credential stores.  See
``docs/CREDENTIALS.md`` and ``docs/TEXT_FILTERS.md`` for guided examples.

Dependencies:
    Text filters use only the Python standard library.  Credential access uses
    ``cryptography`` and, when caching is enabled, ``redis``.

Example:
    >>> from axlib.calc import add
    >>> add(2, 3)
    5
"""

from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING

__version__ = "1.0.0"

if TYPE_CHECKING:
    from .secrets import getinfoblox, getkeys, getnetkeys, readkeyring, updatedict

__all__ = [
    "__version__",
    "getinfoblox",
    "getkeys",
    "getnetkeys",
    "readkeyring",
    "updatedict",
]

_CREDENTIAL_EXPORTS = {
    "getinfoblox",
    "getkeys",
    "getnetkeys",
    "readkeyring",
    "updatedict",
}


def __getattr__(name: str) -> object:
    """Load a backwards-compatible credential helper on first access.

    Args:
        name (str): Attribute requested from the :mod:`axlib` package.

    Returns:
        object: Function imported from :mod:`axlib.secrets`.

    Raises:
        AttributeError: If ``name`` is not one of the supported lazy exports.
        ImportError: If a required credential dependency is unavailable when the
            requested helper is first used.
    """
    if name not in _CREDENTIAL_EXPORTS:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

    # Lazy loading keeps simple text-processing scripts independent from the
    # credential stack until a credential function is actually requested.
    secrets_module = import_module(".secrets", __name__)
    value = getattr(secrets_module, name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    """Include lazily exported functions in interactive attribute listings.

    Args:
        None: Python calls this function without arguments.

    Returns:
        list[str]: Sorted package attributes including credential helpers.

    Raises:
        None: Set and list operations are deterministic.
    """
    return sorted(set(globals()) | set(__all__))
