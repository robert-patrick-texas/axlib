# Copyright 2026 Robert Patrick
# SPDX-License-Identifier: Apache-2.0

"""Convert Markdown files, one or many, into linked HTML pages.

Converting several files together builds a small documentation site: every
page gets a sidebar listing all of them, and links between the Markdown
files are rewritten so they work in HTML.  The steps:

1. :func:`find_markdown` expands directories to their ``.md`` files and
   finds the *base* directory the files have in common.  Pages keep their
   place under it, so ``docs/INSTALL.md`` becomes ``docs/INSTALL.html``,
   either beside the Markdown or under an output directory.
2. :func:`build_site` parses every file, then rewrites and checks each link:

   * a link to another converted file (``INSTALL.md#offline-hosts``) points
     at its HTML page, and the ``#section`` must exist there;
   * a ``#section`` link must name a heading of the same page;
   * a link or image to any other local file must exist; when pages are
     written to another directory, the file is copied there too, so images
     keep working, unless it is hidden or outside the base directory;
   * links to web sites are left alone.

   A link that fails a check is reported as a :class:`LinkProblem`; the page
   is still built.
3. :func:`write_site` writes the pages and copies the files.

:func:`convert_paths` does all three.  :func:`convert_text` converts a single
string, such as Markdown read from a pipe.

Dependencies:
    ``markdown-it-py``: ``uv add 'axlib[markdown]'``.

Example::

    from axlib.md2html import SiteOptions, convert_paths

    report = convert_paths(
        ["README.md", "docs"],
        output_dir="site",
        index="README.md",
        options=SiteOptions(site_name="Team runbooks"),
    )
    for problem in report.problems:
        print(problem)
"""

from __future__ import annotations

import os
import posixpath
import shutil
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote, unquote, urlsplit

from .errors import Md2HtmlError
from .page import NavEntry, render_page
from .render import Document, LinkReference, parse_markdown, render_body, rewrite_links

# File names treated as Markdown when a directory is converted.
MARKDOWN_SUFFIXES = (".md", ".markdown")
# A link to a directory opens the first of these that was converted, as
# GitHub shows a directory's README.
DIRECTORY_PAGES = ("index.md", "README.md")


@dataclass(frozen=True)
class SiteOptions:
    """Settings that apply to every page of a conversion.

    Attributes:
        site_name (str | None): Name shown in the top bar and browser tab.
        links (tuple[tuple[str, str], ...]): ``(label, url)`` links shown in
            the top bar of every page, such as the project's repository.
        source_url (str | None): Base URL of the Markdown sources, such as
            ``https://github.com/ORG/REPO/blob/main/``; each page's footer
            links to its own file under it.
        escape_html (bool): Show raw HTML in the Markdown as text instead of
            passing it through; use it for Markdown you did not write.
    """

    site_name: str | None = None
    links: tuple[tuple[str, str], ...] = ()
    source_url: str | None = None
    escape_html: bool = False


@dataclass(frozen=True)
class LinkProblem:
    """A link that will not work in the converted pages.

    Attributes:
        source (str): The Markdown file, as :func:`display_path` shows it.
        line (int): 1-based line where the link's paragraph or item starts.
        url (str): The link as written (percent-encoded).
        message (str): What is wrong.
    """

    source: str
    line: int
    url: str
    message: str

    def __str__(self) -> str:
        """Format the problem like a compiler message: ``file:line: text``.

        Args:
            None: The instance's fields are used.

        Returns:
            str: One line for standard error.

        Raises:
            None: Only string formatting is performed.
        """
        return f"{self.source}:{self.line}: {self.message}"


@dataclass(frozen=True)
class BuiltPage:
    """One converted page, ready to write.

    Attributes:
        source (Path): The Markdown file.
        name (str): The Markdown file relative to the base directory.
        output_name (str): The HTML file relative to the output directory.
        title (str): The page title shown in the sidebar.
        body (str): The converted Markdown alone, without the page layout
            or the ``#`` links after headings.
        html (str): The complete HTML document.
    """

    source: Path
    name: str
    output_name: str
    title: str
    body: str
    html: str


@dataclass(frozen=True)
class Site:
    """Every page of a conversion, with the files to copy and the problems.

    Attributes:
        base (Path): The directory the Markdown files have in common.
        pages (tuple[BuiltPage, ...]): The pages, in sidebar order.
        assets (tuple[str, ...]): Linked files, relative to ``base``, to copy
            next to the pages; empty when pages are written beside their
            Markdown.
        problems (tuple[LinkProblem, ...]): Links that will not work.
    """

    base: Path
    pages: tuple[BuiltPage, ...]
    assets: tuple[str, ...]
    problems: tuple[LinkProblem, ...]


@dataclass(frozen=True)
class ConversionReport:
    """What :func:`write_site` wrote.

    Attributes:
        pages (tuple[tuple[str, Path], ...]): Each Markdown file, as
            :func:`display_path` shows it, and the HTML file written for it.
        copied (tuple[Path, ...]): Linked files copied next to the pages.
        problems (tuple[LinkProblem, ...]): Links that will not work.
    """

    pages: tuple[tuple[str, Path], ...]
    copied: tuple[Path, ...]
    problems: tuple[LinkProblem, ...]

    def to_dict(self) -> dict[str, Any]:
        """Return the report as plain data for ``--json``.

        Args:
            None: The instance's fields are used.

        Returns:
            dict[str, Any]: ``pages``, ``copied``, and ``problems``.

        Raises:
            None: Only containers are built.
        """
        return {
            "pages": [
                {"source": source, "output": str(output)}
                for source, output in self.pages
            ],
            "copied": [str(path) for path in self.copied],
            "problems": [
                {
                    "source": problem.source,
                    "line": problem.line,
                    "url": problem.url,
                    "message": problem.message,
                }
                for problem in self.problems
            ],
        }


@dataclass
class _Page:
    """A parsed Markdown file and where its HTML goes, during a build."""

    source: Path
    name: str
    output_name: str
    document: Document

    @property
    def title(self) -> str:
        """Return the first ``#`` heading, or the file name without suffix.

        Args:
            None: The parsed document is read.

        Returns:
            str: The title for the sidebar and browser tab.

        Raises:
            None: Only attribute access is performed.
        """
        return self.document.title or Path(self.name).stem


def _is_hidden(path: Path, root: Path) -> bool:
    """Tell whether any part of ``path`` below ``root`` starts with a dot.

    Args:
        path (Path): A file found under ``root``.
        root (Path): The directory being searched.

    Returns:
        bool: True for files such as ``.git/README.md`` or ``.notes.md``.

    Raises:
        ValueError: If ``path`` is not under ``root``.
    """
    return any(part.startswith(".") for part in path.relative_to(root).parts)


def find_markdown(
    paths: Iterable[str | os.PathLike[str]],
    *,
    recursive: bool = False,
) -> tuple[list[Path], Path]:
    """Expand files and directories into Markdown files and their base.

    Args:
        paths (Iterable[str | os.PathLike[str]]): Markdown files (any name)
            and directories (their ``.md`` and ``.markdown`` files).
        recursive (bool): Also search the directories' subdirectories.
            Hidden files and directories are always skipped.

    Returns:
        tuple[list[Path], Path]: The files in the order given (each
            directory's files sorted by path, duplicates removed) and the
            directory they have in common.

    Raises:
        Md2HtmlError: If a path does not exist or no Markdown file is found.
    """
    files: list[Path] = []
    roots: list[Path] = []
    for raw in paths:
        path = Path(raw).resolve()
        if path.is_dir():
            roots.append(path)
            pattern = path.rglob("*") if recursive else path.glob("*")
            found = sorted(
                item
                for item in pattern
                if item.suffix.lower() in MARKDOWN_SUFFIXES
                and item.is_file()
                and not _is_hidden(item, path)
            )
            files.extend(found)
        elif path.is_file():
            roots.append(path.parent)
            files.append(path)
        else:
            raise Md2HtmlError(f"{raw}: no such file or directory")
    # dict.fromkeys removes duplicates and keeps the first position.
    unique = list(dict.fromkeys(files))
    if not unique:
        raise Md2HtmlError("no Markdown files found (.md or .markdown)")
    return unique, Path(os.path.commonpath(roots))


def _plan(
    sources: Sequence[Path],
    base: Path,
    index: Path | None,
) -> list[tuple[Path, str, str]]:
    """Decide each page's name and HTML file, index page first.

    Args:
        sources (Sequence[Path]): Resolved Markdown files.
        base (Path): Their common directory.
        index (Path | None): The file to write as ``index.html``.

    Returns:
        list[tuple[Path, str, str]]: ``(source, name, output_name)`` with
            POSIX paths relative to ``base``.

    Raises:
        Md2HtmlError: If ``index`` is not converted, or two files would be
            written to the same HTML file.
    """
    ordered = list(sources)
    if index is not None:
        if index not in ordered:
            shown = display_path(index)
            raise Md2HtmlError(f"{shown}: --index must be one of the files converted")
        ordered.remove(index)
        ordered.insert(0, index)
    planned: list[tuple[Path, str, str]] = []
    outputs: dict[str, str] = {}
    for source in ordered:
        name = source.relative_to(base).as_posix()
        stem = "index" if source == index else posixpath.splitext(name)[0]
        output_name = posixpath.join(
            posixpath.dirname(name), f"{posixpath.basename(stem)}.html"
        )
        if output_name in outputs:
            raise Md2HtmlError(
                f"{outputs[output_name]} and {name} would both become {output_name}"
            )
        outputs[output_name] = name
        planned.append((source, name, output_name))
    return planned


def display_path(path: str | os.PathLike[str]) -> str:
    """Show a path relative to the current directory when it is under it.

    Messages use this form so that ``file:line`` can be opened as printed.

    Args:
        path (str | os.PathLike[str]): A file or directory.

    Returns:
        str: ``docs/INSTALL.md`` rather than a long absolute path, or the
            absolute path for a file outside the current directory.

    Raises:
        None: Only path arithmetic is performed.
    """
    relative = os.path.relpath(path)
    if relative == os.pardir or relative.startswith(os.pardir + os.sep):
        return str(Path(path).resolve())
    return relative


def _relative_url(target: str, page_output: str) -> str:
    """Return a URL to ``target`` from the directory of ``page_output``.

    Args:
        target (str): POSIX path relative to the output directory.
        page_output (str): The page that will contain the link.

    Returns:
        str: A relative, percent-encoded URL, such as ``../INSTALL.html``.

    Raises:
        None: Only path arithmetic is performed.
    """
    start = posixpath.dirname(page_output) or "."
    return quote(posixpath.relpath(target, start))


class _LinkChecker:
    """Rewrite and check the links of one page against the whole site."""

    def __init__(
        self,
        page: _Page,
        pages: dict[str, _Page],
        base: Path,
        copy_assets: bool,
    ) -> None:
        """Remember the page being processed and everything it can link to.

        Args:
            page (_Page): The page whose links are processed.
            pages (dict[str, _Page]): Every page by its Markdown name.
            base (Path): The directory the Markdown files have in common.
            copy_assets (bool): Linked local files will be copied to the
                output directory.

        Returns:
            None: The checker is initialized in place.

        Raises:
            None: Only attributes are set.
        """
        self.page = page
        self.pages = pages
        self.base = base
        self.copy_assets = copy_assets
        self.problems: list[LinkProblem] = []
        self.assets: list[str] = []

    def _problem(self, link: LinkReference, message: str) -> None:
        """Record a problem with ``link``.

        Args:
            link (LinkReference): The link.
            message (str): What is wrong.

        Returns:
            None: The problem is appended to :attr:`problems`.

        Raises:
            None: Only a list append is performed.
        """
        source = display_path(self.page.source)
        self.problems.append(LinkProblem(source, link.line, link.url, message))

    def _target_page(self, target: str) -> _Page | None:
        """Find the converted page a link target means, if any.

        Args:
            target (str): POSIX path relative to the base directory.

        Returns:
            _Page | None: The page for the file, or for a directory's
                ``index.md`` or ``README.md``.

        Raises:
            None: Only dictionary lookups are performed.
        """
        if target in self.pages:
            return self.pages[target]
        for name in DIRECTORY_PAGES:
            page = self.pages.get(posixpath.normpath(posixpath.join(target, name)))
            if page is not None:
                return page
        return None

    def __call__(self, link: LinkReference) -> str | None:
        """Return the rewritten URL of ``link``, recording any problem.

        Args:
            link (LinkReference): A link or image of the page.

        Returns:
            str | None: The new URL, or ``None`` to keep the link as written.

        Raises:
            None: Problems are recorded instead of raised.
        """
        parts = urlsplit(link.url)
        if parts.scheme or parts.netloc or parts.path.startswith("/"):
            # Web links, mail links, and site-absolute paths cannot be checked.
            return None
        fragment = unquote(parts.fragment)
        if not parts.path:
            if fragment and fragment not in self.page.document.slugs:
                self._problem(link, f"no section #{fragment} on this page")
            return None
        directory = posixpath.dirname(self.page.name)
        target = posixpath.normpath(posixpath.join(directory, unquote(parts.path)))
        page = self._target_page(target)
        if page is not None:
            if fragment and fragment not in page.document.slugs:
                target_file = display_path(page.source)
                self._problem(link, f"no section #{fragment} in {target_file}")
            url = _relative_url(page.output_name, self.page.output_name)
            if parts.query:
                url += f"?{parts.query}"
            return url + (f"#{parts.fragment}" if parts.fragment else "")
        path = Path(os.path.normpath(self.base / target))
        shown = display_path(path)
        if not path.exists():
            self._problem(link, f"{shown} does not exist")
        elif not self.copy_assets:
            # Beside the Markdown, the link works as written.
            pass
        elif target == os.pardir or target.startswith("../"):
            self._problem(
                link,
                f"{shown} is outside {display_path(self.base)}, so it is not copied",
            )
        elif any(part.startswith(".") for part in target.split("/")):
            # Hidden files are often private (.env, .ssh); never publish them.
            self._problem(link, f"{shown} is hidden, so it is not copied")
        elif path.is_dir():
            self._problem(
                link, f"{shown} is a folder with no converted index.md or README.md"
            )
        else:
            self.assets.append(target)
        return None


def build_site(
    paths: Iterable[str | os.PathLike[str]],
    *,
    output_dir: str | os.PathLike[str] | None = None,
    recursive: bool = False,
    index: str | os.PathLike[str] | None = None,
    options: SiteOptions | None = None,
) -> Site:
    """Read, convert, and cross-link Markdown files without writing them.

    Args:
        paths (Iterable[str | os.PathLike[str]]): Markdown files and
            directories, in the order the sidebar should list them.
        output_dir (str | os.PathLike[str] | None): Where the pages will be
            written; ``None`` means beside each Markdown file.  It decides
            whether linked files need copying.
        recursive (bool): Also convert Markdown in subdirectories.
        index (str | os.PathLike[str] | None): One of the files to write as
            ``index.html`` and list first, such as ``README.md``.
        options (SiteOptions | None): Site name, links, and HTML handling.

    Returns:
        Site: The pages, the files to copy, and any link problems.

    Raises:
        Md2HtmlError: If a path is missing, a file is not UTF-8 text, or two
            files would be written to the same page.
        MarkdownDependencyError: If markdown-it-py is not installed.
        OSError: If a Markdown file cannot be read.
    """
    options = options or SiteOptions()
    sources, base = find_markdown(paths, recursive=recursive)
    index_path = Path(index).resolve() if index is not None else None
    pages: list[_Page] = []
    for source, name, output_name in _plan(sources, base, index_path):
        try:
            text = source.read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            shown = display_path(source)
            raise Md2HtmlError(f"{shown}: not UTF-8 text ({exc.reason})") from exc
        document = parse_markdown(text, escape_html=options.escape_html)
        pages.append(_Page(source, name, output_name, document))

    by_name = {page.name: page for page in pages}
    copy_assets = output_dir is not None and Path(output_dir).resolve() != base
    problems: list[LinkProblem] = []
    assets: list[str] = []
    for page in pages:
        checker = _LinkChecker(page, by_name, base, copy_assets)
        rewrite_links(page.document, checker)
        problems.extend(checker.problems)
        assets.extend(checker.assets)

    built = []
    index_output = pages[0].output_name if index_path is not None else None
    for page in pages:
        nav = [
            NavEntry(
                other.title,
                _relative_url(other.output_name, page.output_name),
                current=other is page,
            )
            for other in pages
        ]
        source_href = None
        if options.source_url:
            source_href = options.source_url.rstrip("/") + "/" + quote(page.name)
        html = render_page(
            title=page.title,
            body=render_body(page.document, heading_links=True),
            nav=nav,
            sections=page.document.headings,
            site_name=options.site_name,
            home_href=_relative_url(index_output, page.output_name)
            if index_output
            else None,
            links=options.links,
            source_label=page.name,
            source_href=source_href,
        )
        built.append(
            BuiltPage(
                page.source,
                page.name,
                page.output_name,
                page.title,
                render_body(page.document),
                html,
            )
        )
    return Site(base, tuple(built), tuple(dict.fromkeys(assets)), tuple(problems))


def write_site(
    site: Site, output_dir: str | os.PathLike[str] | None = None
) -> ConversionReport:
    """Write a built site's pages and copy its linked files.

    Args:
        site (Site): The result of :func:`build_site`.
        output_dir (str | os.PathLike[str] | None): The directory to write
            into, created if needed; ``None`` writes each page beside its
            Markdown file.  Pass the same value given to :func:`build_site`.

    Returns:
        ConversionReport: The files written and copied, and the problems.

    Raises:
        OSError: If a directory cannot be created or a file written.
    """
    root = Path(output_dir) if output_dir is not None else site.base
    written = []
    for page in site.pages:
        target = root / page.output_name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(page.html, encoding="utf-8")
        written.append((display_path(page.source), target))
    copied = []
    for asset in site.assets:
        target = root / asset
        target.parent.mkdir(parents=True, exist_ok=True)
        # copyfile copies the content only, so the copy gets normal modes
        # instead of, say, a source file's executable bit.
        shutil.copyfile(site.base / asset, target)
        copied.append(target)
    return ConversionReport(tuple(written), tuple(copied), site.problems)


def convert_paths(
    paths: Iterable[str | os.PathLike[str]],
    output_dir: str | os.PathLike[str] | None = None,
    *,
    recursive: bool = False,
    index: str | os.PathLike[str] | None = None,
    options: SiteOptions | None = None,
) -> ConversionReport:
    """Convert Markdown files and directories to HTML pages and write them.

    Args:
        paths (Iterable[str | os.PathLike[str]]): Markdown files and
            directories, in sidebar order.
        output_dir (str | os.PathLike[str] | None): Directory for the pages;
            ``None`` writes each page beside its Markdown file.
        recursive (bool): Also convert Markdown in subdirectories.
        index (str | os.PathLike[str] | None): The file to write as
            ``index.html``, such as ``README.md``.
        options (SiteOptions | None): Site name, links, and HTML handling.

    Returns:
        ConversionReport: The files written and copied, and the problems.

    Raises:
        Md2HtmlError: If the inputs cannot be converted as asked.
        MarkdownDependencyError: If markdown-it-py is not installed.
        OSError: If a file cannot be read or written.
    """
    site = build_site(
        paths, output_dir=output_dir, recursive=recursive, index=index, options=options
    )
    return write_site(site, output_dir)


def convert_text(
    text: str,
    *,
    title: str | None = None,
    options: SiteOptions | None = None,
) -> str:
    """Convert one Markdown string to a complete HTML page.

    The sidebar lists the page's own sections.  Links are left as written.

    Args:
        text (str): Markdown source, such as text read from a pipe.
        title (str | None): Page title; defaults to the first ``#`` heading,
            then ``"Document"``.
        options (SiteOptions | None): Site name, links, and HTML handling.

    Returns:
        str: The HTML document.

    Raises:
        MarkdownDependencyError: If markdown-it-py is not installed.
    """
    options = options or SiteOptions()
    document = parse_markdown(text, escape_html=options.escape_html)
    page_title = title or document.title or "Document"
    return render_page(
        title=page_title,
        body=render_body(document, heading_links=True),
        nav=[NavEntry(page_title, "#", current=True)],
        sections=document.headings,
        site_name=options.site_name,
        links=options.links,
    )
