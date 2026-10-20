# Copyright 2026 Robert Patrick
# SPDX-License-Identifier: Apache-2.0

"""Convert Markdown files to HTML pages with a navigation sidebar.

Operators keep runbooks, change plans, and notes in Markdown because it
reads well as plain text and in Git.  ``axlib md2html`` turns those files
into HTML pages that open in any browser: one file for a ticket or an
e-mail, or a whole directory as a small site with a sidebar listing every
page and the current page's sections.  This project's own documentation is
built with it and published to GitHub Pages.

The modules, in the order data flows through them:

* :mod:`axlib.md2html.render` parses Markdown (GitHub-flavored) and gives
  headings GitHub-style anchors.
* :mod:`axlib.md2html.site` finds the files, rewrites and checks links
  between them, and writes the pages.
* :mod:`axlib.md2html.page` wraps each page in the HTML layout.

Names are loaded lazily, like :mod:`axlib.parse`, so importing the package
does not need markdown-it-py until a conversion runs.

Dependencies:
    ``markdown-it-py``: ``uv add 'axlib[markdown]'``.

Example::

    from axlib.md2html import markdown_to_html

    print(markdown_to_html("# Change plan\\n\\n1. Drain **sw1**"))
"""

from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING

from .errors import MarkdownDependencyError, Md2HtmlError

if TYPE_CHECKING:
    from .page import NavEntry, render_page
    from .render import Document, Heading, markdown_to_html, parse_markdown, slugify
    from .site import (
        ConversionReport,
        LinkProblem,
        Site,
        SiteOptions,
        build_site,
        convert_paths,
        convert_text,
        write_site,
    )

__all__ = [
    "ConversionReport",
    "Document",
    "Heading",
    "LinkProblem",
    "MarkdownDependencyError",
    "Md2HtmlError",
    "NavEntry",
    "Site",
    "SiteOptions",
    "build_site",
    "convert_paths",
    "convert_text",
    "markdown_to_html",
    "parse_markdown",
    "render_page",
    "slugify",
    "write_site",
]

_LAZY_EXPORTS = {
    "NavEntry": ".page",
    "render_page": ".page",
    "Document": ".render",
    "Heading": ".render",
    "markdown_to_html": ".render",
    "parse_markdown": ".render",
    "slugify": ".render",
    "ConversionReport": ".site",
    "LinkProblem": ".site",
    "Site": ".site",
    "SiteOptions": ".site",
    "build_site": ".site",
    "convert_paths": ".site",
    "convert_text": ".site",
    "write_site": ".site",
}


def __getattr__(name: str) -> object:
    """Load an export from its module the first time it is used.

    Args:
        name (str): Name requested from :mod:`axlib.md2html`.

    Returns:
        object: The class or function.

    Raises:
        AttributeError: If ``name`` is not a package export.
        ImportError: If the implementing module cannot be imported.
    """
    module_name = _LAZY_EXPORTS.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(import_module(module_name, __name__), name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    """Show lazy exports during interactive exploration.

    Args:
        None: Python calls this function without arguments.

    Returns:
        list[str]: Sorted module attributes including lazy exports.

    Raises:
        None: Set and list operations are deterministic.
    """
    return sorted(set(globals()) | set(__all__))
