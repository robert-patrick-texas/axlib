# Copyright 2026 Robert Patrick
# SPDX-License-Identifier: Apache-2.0

r"""Streaming text filters used in network-automation preparation pipelines.

The modules in this package clean comments and whitespace, expand include files,
and substitute template variables.  Each transformation is available both as a
normal Python function and as a command that reads a file or standard input.
This dual interface lets engineers test a small function first and later compose
it into a shell or Python workflow.

All transformations use only the Python standard library.  Convenience exports
are loaded lazily so running an individual module with ``python -m`` does not
pre-import that same module and trigger a :mod:`runpy` warning.

Example:
    >>> from axlib.tf import strip_comments
    >>> strip_comments("hostname edge-1  # inventory name\n")
    'hostname edge-1  \n'
"""

from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .incfile import expand_includes
    from .rmcomment import strip_comments
    from .rmdouble import remove_double_slash
    from .rmline import normalize_text
    from .rmtriple import remove_triple_quoted
    from .rmwhite import normalize_spaces_outside_quotes
    from .rmwhite2 import normalize as normalize_whitespace_safely
    from .varsub import substitute_variables

__all__ = [
    "expand_includes",
    "normalize_spaces_outside_quotes",
    "normalize_text",
    "normalize_whitespace_safely",
    "remove_double_slash",
    "remove_triple_quoted",
    "strip_comments",
    "substitute_variables",
]

_FILTER_EXPORTS = {
    "expand_includes": (".incfile", "expand_includes"),
    "normalize_spaces_outside_quotes": (
        ".rmwhite",
        "normalize_spaces_outside_quotes",
    ),
    "normalize_text": (".rmline", "normalize_text"),
    "normalize_whitespace_safely": (".rmwhite2", "normalize"),
    "remove_double_slash": (".rmdouble", "remove_double_slash"),
    "remove_triple_quoted": (".rmtriple", "remove_triple_quoted"),
    "strip_comments": (".rmcomment", "strip_comments"),
    "substitute_variables": (".varsub", "substitute_variables"),
}


def __getattr__(name: str) -> object:
    """Load a text-filter convenience export only when it is requested.

    Args:
        name (str): Function name requested from :mod:`axlib.tf`.

    Returns:
        object: Transformation function imported from its implementation module.

    Raises:
        AttributeError: If ``name`` is not a documented package export.
        ImportError: If the selected implementation module cannot be imported.
    """
    export = _FILTER_EXPORTS.get(name)
    if export is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

    module_name, attribute_name = export
    module = import_module(module_name, __name__)
    value = getattr(module, attribute_name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    """Show lazy filter exports during interactive exploration.

    Args:
        None: Python calls this function without arguments.

    Returns:
        list[str]: Sorted module attributes including lazy transformation names.

    Raises:
        None: Set and list operations are deterministic.
    """
    return sorted(set(globals()) | set(__all__))
