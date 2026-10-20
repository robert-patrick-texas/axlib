# Copyright 2026 Robert Patrick
# SPDX-License-Identifier: Apache-2.0

"""Exceptions raised while converting Markdown files to HTML pages.

Every exception derives from :class:`Md2HtmlError`, so a script can catch one
type around a whole conversion.  A broken link is not an exception: it is a
:class:`~axlib.md2html.site.LinkProblem` in the report, because the pages are
still worth writing and the operator decides whether it matters
(``axlib md2html --strict`` turns problems into a failing exit status).

Dependencies:
    Python standard library only.

Example:
    >>> from axlib.md2html.errors import MarkdownDependencyError, Md2HtmlError
    >>> issubclass(MarkdownDependencyError, Md2HtmlError)
    True
"""

from __future__ import annotations

# One spelling of the install hint, used by every message that needs it.
INSTALL_HINT = "uv add 'axlib[markdown]'"


class Md2HtmlError(Exception):
    """Base class for every :mod:`axlib.md2html` error.

    Raised directly for inputs that cannot be converted as asked, such as two
    Markdown files that would be written to the same HTML file.
    """


class MarkdownDependencyError(Md2HtmlError):
    """The markdown-it-py package (the ``markdown`` extra) is not installed."""
