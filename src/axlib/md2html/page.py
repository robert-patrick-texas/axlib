# Copyright 2026 Robert Patrick
# SPDX-License-Identifier: Apache-2.0

"""Wrap a converted Markdown fragment in a complete, self-contained HTML page.

Every page has the same layout:

* an optional bar at the top with the site name and project links (for
  example the GitHub repository and the PyPI project);
* a sidebar down the left listing every page converted together, with the
  current page's sections (its ``##`` and ``###`` headings) under it;
* the document itself, and a footer naming the Markdown source.

The styles are written into each page rather than a separate stylesheet, so
a single converted file can be mailed, attached to a ticket, or opened from
a file share and still look right.  They follow the reader's light or dark
system setting and leave navigation out when printing.  The document comes
before the sidebar in the HTML and a grid draws the sidebar on the left, so
on a phone the text comes first, with a link down to the page list.  Pages
use no JavaScript.

Dependencies:
    Python standard library only.

Example:
    >>> from axlib.md2html.page import NavEntry, render_page
    >>> page = render_page(title="Notes", body="<h1>Notes</h1>",
    ...                    nav=[NavEntry("Notes", "notes.html", current=True)])
    >>> page.startswith("<!DOCTYPE html>")
    True
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from html import escape
from string import Template

from .render import Heading

# The sidebar shows these heading levels of the current page.
SECTION_LEVELS = (2, 3)


@dataclass(frozen=True)
class NavEntry:
    """One page in the sidebar.

    Attributes:
        title (str): The text shown, normally the page's first heading.
        href (str): The page's URL relative to the page being written.
        current (bool): True for the page being written; its sections are
            listed under it.
    """

    title: str
    href: str
    current: bool = False


STYLE = """
:root {
  color-scheme: light dark;
  --bg: #ffffff; --fg: #1f2328; --muted: #59636e; --border: #d1d9e0;
  --accent: #0969da; --code-bg: #f6f8fa; --side-bg: #f6f8fa;
  --hover: #eaeef2; --current: #ddf4ff; --bar-bg: #1f2328; --bar-fg: #ffffff;
}
@media (prefers-color-scheme: dark) {
  :root {
    --bg: #0d1117; --fg: #e6edf3; --muted: #9198a1; --border: #3d444d;
    --accent: #4493f8; --code-bg: #151b23; --side-bg: #010409;
    --hover: #1f242b; --current: #121d2f; --bar-bg: #010409; --bar-fg: #f0f6fc;
  }
}
* { box-sizing: border-box; }
html { scroll-padding-top: 4rem; }
body {
  margin: 0; background: var(--bg); color: var(--fg);
  font: 16px/1.6 system-ui, -apple-system, "Segoe UI", Roboto, "Helvetica Neue",
    Arial, sans-serif;
}
a { color: var(--accent); }
.topbar {
  position: sticky; top: 0; z-index: 10; display: flex; flex-wrap: wrap;
  align-items: center; gap: .25rem 1.25rem; min-height: 3rem;
  padding: .5rem 1.25rem; background: var(--bar-bg); color: var(--bar-fg);
}
.topbar a { color: inherit; text-decoration: none; }
.topbar a:hover { text-decoration: underline; }
.site-name { font-weight: 600; font-size: 1.05rem; }
.project-links { display: flex; flex-wrap: wrap; gap: .25rem 1.25rem;
  margin-left: auto; font-size: .95rem; }
.layout { display: grid; grid-template-columns: 18rem minmax(0, 1fr); }
.layout > .content { grid-column: 2; grid-row: 1; }
.layout > .sidebar { grid-column: 1; grid-row: 1; }
.to-nav { display: none; }
.sidebar {
  position: sticky; top: 0; align-self: start; height: 100vh; overflow-y: auto;
  padding: 1rem .75rem 2rem 1rem; border-right: 1px solid var(--border);
  background: var(--side-bg); font-size: .9rem; line-height: 1.4;
}
.has-topbar .sidebar { top: 3rem; height: calc(100vh - 3rem); }
.sidebar ul { list-style: none; margin: 0; padding: 0; }
.sidebar li { margin: .1rem 0; }
.sidebar a { display: block; padding: .3rem .5rem; border-radius: 6px;
  color: var(--fg); text-decoration: none; }
.sidebar a:hover { background: var(--hover); }
.sidebar .current > a { background: var(--current); font-weight: 600; }
.sidebar .sections { margin: .25rem 0 .5rem .75rem; padding-left: .5rem;
  border-left: 1px solid var(--border); }
.sidebar .sections a { padding: .15rem .5rem; color: var(--muted);
  font-weight: normal; }
.content { min-width: 0; max-width: 58rem; padding: 1.5rem 2.5rem 3rem; }
h1, h2, h3, h4, h5, h6 { margin: 1.5em 0 .75em; line-height: 1.25; }
h1 { font-size: 2em; margin-top: .25em; }
h1, h2 { padding-bottom: .3em; border-bottom: 1px solid var(--border); }
h2 { font-size: 1.5em; }
h3 { font-size: 1.25em; }
.anchor { margin-left: .4rem; color: var(--muted); text-decoration: none;
  opacity: 0; }
:is(h1, h2, h3, h4, h5, h6):hover .anchor, .anchor:focus { opacity: 1; }
code, pre { font-family: ui-monospace, SFMono-Regular, "SF Mono", Menlo, Consolas,
  "Liberation Mono", monospace; font-size: .875em; }
code { padding: .15em .35em; border-radius: 6px; background: var(--code-bg); }
pre { padding: 1rem; overflow-x: auto; border-radius: 6px; line-height: 1.45;
  background: var(--code-bg); }
pre code { padding: 0; background: none; font-size: 1em; }
table { display: block; max-width: 100%; overflow-x: auto; margin: 1rem 0;
  border-collapse: collapse; }
th, td { padding: .4rem .75rem; border: 1px solid var(--border);
  vertical-align: top; }
th { background: var(--code-bg); }
blockquote { margin: 1rem 0; padding: 0 1rem; color: var(--muted);
  border-left: .25rem solid var(--border); }
img { max-width: 100%; }
.contains-task-list { padding-left: 1.25rem; list-style: none; }
.task-list-item-checkbox { margin: 0 .4rem 0 -1.25rem; }
.footnotes { color: var(--muted); font-size: .9rem; }
hr { border: 0; border-top: 1px solid var(--border); }
.page-footer { margin-top: 3rem; padding-top: 1rem; color: var(--muted);
  border-top: 1px solid var(--border); font-size: .85rem; }
@media (max-width: 52rem) {
  .layout { display: block; }
  .sidebar, .has-topbar .sidebar { position: static; height: auto;
    border-right: 0; border-top: 1px solid var(--border); }
  .content { padding: 1rem 1rem 2rem; }
  .to-nav { display: block; margin-bottom: .5rem; font-size: .9rem; }
}
@media print {
  .topbar, .sidebar, .anchor, .to-nav { display: none; }
  .layout { display: block; }
}
""".strip()

PAGE = Template("""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="generator" content="axlib md2html">
<title>$title</title>
<style>
$style
</style>
</head>
<body class="$body_class">
$topbar<div class="layout">
<main class="content">
<a class="to-nav" href="#pages">Pages and sections &darr;</a>
$body
$footer</main>
<nav class="sidebar" id="pages" aria-label="Pages and sections">
$nav
</nav>
</div>
</body>
</html>
""")


def render_sections(headings: Sequence[Heading]) -> str:
    """Render a page's ``##`` and ``###`` headings as a nested list.

    Args:
        headings (Sequence[Heading]): Every heading of the page.

    Returns:
        str: A ``<ul class="sections">`` element, or ``""`` when the page has
            no headings at those levels.

    Raises:
        None: Only string formatting is performed.
    """
    sub_level = SECTION_LEVELS[-1]
    # Each entry is a ## link and the ### links nested under it.
    entries: list[tuple[str, list[str]]] = []
    for heading in headings:
        if heading.level not in SECTION_LEVELS:
            continue
        link = f'<a href="#{escape(heading.slug)}">{escape(heading.text)}</a>'
        if heading.level == sub_level and entries:
            entries[-1][1].append(link)
        else:
            # A ### before the first ## is shown at the top level.
            entries.append((link, []))
    if not entries:
        return ""
    items = []
    for link, children in entries:
        nested = ""
        if children:
            nested = (
                '\n<ul class="sections">\n'
                + "\n".join(f"<li>{child}</li>" for child in children)
                + "\n</ul>"
            )
        items.append(f"<li>{link}{nested}</li>")
    return '<ul class="sections">\n' + "\n".join(items) + "\n</ul>"


def render_nav(nav: Sequence[NavEntry], sections: Sequence[Heading]) -> str:
    """Render the sidebar: every page, with the current page's sections.

    Args:
        nav (Sequence[NavEntry]): The pages, in sidebar order.
        sections (Sequence[Heading]): The current page's headings.

    Returns:
        str: A ``<ul class="pages">`` element.

    Raises:
        None: Only string formatting is performed.
    """
    items = []
    for entry in nav:
        link = f'<a href="{escape(entry.href)}">{escape(entry.title)}</a>'
        if entry.current:
            link = (
                f'<a href="{escape(entry.href)}" aria-current="page">'
                f"{escape(entry.title)}</a>"
            )
            section_list = render_sections(sections)
            items.append(f'<li class="current">{link}\n{section_list}</li>')
        else:
            items.append(f"<li>{link}</li>")
    return '<ul class="pages">\n' + "\n".join(items) + "\n</ul>"


def render_topbar(
    site_name: str | None,
    home_href: str | None,
    links: Sequence[tuple[str, str]],
) -> str:
    """Render the bar at the top of the page, or nothing when it is empty.

    Args:
        site_name (str | None): Name shown at the left, such as ``axlib``.
        home_href (str | None): Where the site name links to, normally the
            index page; ``None`` shows the name without a link.
        links (Sequence[tuple[str, str]]): ``(label, url)`` pairs shown at
            the right, such as the project's repository.

    Returns:
        str: A ``<header class="topbar">`` element, or ``""`` when there is
            neither a site name nor a link.

    Raises:
        None: Only string formatting is performed.
    """
    if not site_name and not links:
        return ""
    name = ""
    if site_name:
        name = f'<span class="site-name">{escape(site_name)}</span>'
        if home_href:
            href = escape(home_href)
            name = f'<a class="site-name" href="{href}">{escape(site_name)}</a>'
    link_html = "".join(
        f'<a href="{escape(url)}">{escape(label)}</a>' for label, url in links
    )
    project_links = (
        f'<nav class="project-links" aria-label="Project links">{link_html}</nav>'
        if links
        else ""
    )
    return f'<header class="topbar">{name}{project_links}</header>\n'


def render_page(
    *,
    title: str,
    body: str,
    nav: Sequence[NavEntry],
    sections: Sequence[Heading] = (),
    site_name: str | None = None,
    home_href: str | None = None,
    links: Sequence[tuple[str, str]] = (),
    source_label: str | None = None,
    source_href: str | None = None,
) -> str:
    """Build a complete HTML page around a converted fragment.

    Args:
        title (str): The page title, normally its first heading.
        body (str): The converted Markdown, from
            :func:`axlib.md2html.render.render_body`.
        nav (Sequence[NavEntry]): The sidebar's pages, current one included.
        sections (Sequence[Heading]): The current page's headings.
        site_name (str | None): Name shown in the top bar and the browser
            tab, such as ``axlib``.
        home_href (str | None): Where the site name links to.
        links (Sequence[tuple[str, str]]): ``(label, url)`` project links for
            the top bar.
        source_label (str | None): The Markdown file's name, shown in the
            footer.
        source_href (str | None): A URL for the Markdown source, such as the
            file on GitHub; the footer links to it.

    Returns:
        str: The whole HTML document.

    Raises:
        None: Only string formatting is performed.
    """
    full_title = (
        title if not site_name or site_name == title else f"{title} - {site_name}"
    )
    topbar = render_topbar(site_name, home_href, links)
    footer = ""
    if source_label:
        source = escape(source_label)
        if source_href:
            source = f'<a href="{escape(source_href)}">{source}</a>'
        footer = (
            f'<footer class="page-footer">Converted from {source} '
            "with axlib md2html.</footer>\n"
        )
    return PAGE.substitute(
        title=escape(full_title),
        style=STYLE,
        body_class="has-topbar" if topbar else "",
        topbar=topbar,
        nav=render_nav(nav, sections),
        body=body.rstrip("\n"),
        footer=footer,
    )
