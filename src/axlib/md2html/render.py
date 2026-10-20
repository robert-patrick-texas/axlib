# Copyright 2026 Robert Patrick
# SPDX-License-Identifier: Apache-2.0

"""Turn Markdown text into an HTML fragment with linkable headings.

The conversion follows GitHub-flavored Markdown closely enough that a
document which looks right on GitHub looks the same here: CommonMark plus
tables, ``~~strikethrough~~``, bare URLs turned into links, ``- [ ]`` task
lists shown as check boxes, and ``[^1]`` footnotes.  The
`markdown-it-py <https://github.com/executablebooks/markdown-it-py>`_ library
does the parsing; this module adds the two things a documentation site
needs on top of it:

* Every heading gets an ``id`` made the way GitHub makes its anchors
  (:func:`slugify`), so a link such as ``INSTALL.md#recorded-options`` that
  works on GitHub also works in the HTML page.
* The parsed document keeps its token stream, so links can be rewritten
  (``INSTALL.md`` becomes ``INSTALL.html``) and checked before rendering.

Raw HTML in the Markdown is passed through, as CommonMark specifies.  Pass
``escape_html=True`` for Markdown from an untrusted source: the HTML is then
shown as text instead.  Links with ``javascript:`` and similar schemes are
always refused by markdown-it-py.

Dependencies:
    ``markdown-it-py`` with its ``linkify`` extra, and ``mdit-py-plugins``:
    ``uv add 'axlib[markdown]'``.  Without ``linkify-it-py``, bare URLs stay
    plain text; without ``mdit-py-plugins``, task lists and footnotes do.

Example:
    >>> from axlib.md2html.render import parse_markdown
    >>> document = parse_markdown("# Change plan\\n## Steps\\n## Steps")
    >>> [heading.slug for heading in document.headings]
    ['change-plan', 'steps', 'steps-1']
"""

from __future__ import annotations

import html
import importlib.util
import unicodedata
from collections.abc import Callable, Iterator, MutableMapping, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from .errors import INSTALL_HINT, MarkdownDependencyError

if TYPE_CHECKING:
    from markdown_it import MarkdownIt
    from markdown_it.token import Token

# The render-environment key that turns on the ``#`` link after each heading.
HEADING_LINKS = "axlib_heading_links"


@dataclass(frozen=True)
class Heading:
    """One heading of a document, as the table of contents needs it.

    Attributes:
        level (int): 1 for ``#``, 2 for ``##``, and so on.
        text (str): The heading's plain text, without Markdown markup.
        slug (str): The ``id`` given to the heading, unique in the document.
    """

    level: int
    text: str
    slug: str


@dataclass(frozen=True)
class LinkReference:
    """A link or image found in a document, ready to be checked or rewritten.

    Attributes:
        url (str): The ``href`` of a link or the ``src`` of an image, as
            markdown-it-py normalized it (percent-encoded).
        kind (str): ``"link"`` or ``"image"``.
        line (int): The 1-based line of the source where the link's
            paragraph, list item, or table row starts.
    """

    url: str
    kind: str
    line: int


@dataclass
class Document:
    """A parsed Markdown document: tokens, headings, and the parser to render.

    Attributes:
        tokens (list[Token]): markdown-it-py's token stream.  Links are
            rewritten by changing these tokens' attributes.
        headings (tuple[Heading, ...]): Every heading, in document order.
        parser (MarkdownIt): The parser that produced the tokens; rendering
            needs the same instance and options.
        env (MutableMapping[str, Any]): Parser state shared with the
            renderer, such as reference-style link definitions.
    """

    tokens: list[Token]
    headings: tuple[Heading, ...]
    parser: MarkdownIt
    env: MutableMapping[str, Any] = field(default_factory=dict)

    @property
    def title(self) -> str | None:
        """Return the text of the first level-1 heading, if there is one.

        Args:
            None: The property reads :attr:`headings`.

        Returns:
            str | None: The title, or ``None`` for a document without ``#``.

        Raises:
            None: Only a list scan is performed.
        """
        return next((h.text for h in self.headings if h.level == 1), None)

    @property
    def slugs(self) -> frozenset[str]:
        """Return every heading ``id``, for checking ``#section`` links.

        Args:
            None: The property reads :attr:`headings`.

        Returns:
            frozenset[str]: The heading slugs.

        Raises:
            None: Only a set is built.
        """
        return frozenset(heading.slug for heading in self.headings)


def slugify(text: str) -> str:
    """Make a heading anchor the way GitHub does.

    GitHub lowercases the heading text, removes every character that is not
    a letter, digit, mark, connector such as ``_``, hyphen, or space, and
    turns each space into a hyphen.  Spaces are not collapsed, so
    ``"A - B"`` becomes ``"a---b"``, exactly as on GitHub.

    Args:
        text (str): The heading's plain text.

    Returns:
        str: The anchor, possibly empty for a heading of only punctuation.

    Raises:
        None: Only string operations are performed.

    Example:
        >>> slugify("Scheduled jobs (cron and systemd)")
        'scheduled-jobs-cron-and-systemd'
        >>> slugify("rotate_key()")
        'rotate_key'
    """
    kept = (
        character
        for character in text.lower()
        if character in "- "
        # Unicode categories: L* letters, M* marks, N* numbers, Pc connectors.
        or unicodedata.category(character)[0] in "LMN"
        or unicodedata.category(character) == "Pc"
    )
    return "".join(kept).replace(" ", "-")


def unique_slug(slug: str, seen: dict[str, int]) -> str:
    """Make ``slug`` unique in a document the way GitHub does.

    The second ``## Example`` heading becomes ``example-1``, the third
    ``example-2``, and so on, skipping any suffix another heading already
    has.

    Args:
        slug (str): The anchor from :func:`slugify`.
        seen (dict[str, int]): Anchors used so far and how many times each
            base anchor has repeated; updated in place.

    Returns:
        str: An anchor not in ``seen`` before the call.

    Raises:
        None: Only dictionary operations are performed.
    """
    result = slug
    while result in seen:
        seen[slug] += 1
        result = f"{slug}-{seen[slug]}"
    seen[result] = 0
    return result


def create_parser(*, escape_html: bool = False) -> MarkdownIt:
    """Create a markdown-it-py parser for GitHub-flavored Markdown.

    Args:
        escape_html (bool): Show raw HTML in the Markdown as text instead of
            passing it through.

    Returns:
        MarkdownIt: A parser with tables, strikethrough, linkify (when
            ``linkify-it-py`` is installed), task lists and footnotes (when
            ``mdit-py-plugins`` is installed), and heading anchors.

    Raises:
        MarkdownDependencyError: If markdown-it-py is not installed.
    """
    try:
        from markdown_it import MarkdownIt
    except ImportError as exc:
        raise MarkdownDependencyError(
            f"markdown-it-py is not installed: {INSTALL_HINT}"
        ) from exc

    linkify = importlib.util.find_spec("linkify_it") is not None
    # "gfm-like" is CommonMark plus GitHub's tables, strikethrough, and bare
    # URL links.  markdown-it-py raises at parse time when linkify is on but
    # linkify-it-py is missing, so turn it off instead of failing.
    parser = MarkdownIt("gfm-like", {"html": not escape_html, "linkify": linkify})
    if not linkify:
        parser.disable("linkify")
    if importlib.util.find_spec("mdit_py_plugins") is not None:
        from mdit_py_plugins.footnote import footnote_plugin
        from mdit_py_plugins.tasklists import tasklists_plugin

        # Check boxes are shown, not clickable: a page cannot save a tick.
        parser.use(tasklists_plugin).use(footnote_plugin)
    parser.add_render_rule("heading_close", _render_heading_close)
    return parser


def _render_heading_close(
    renderer: Any,
    tokens: Sequence[Token],
    index: int,
    options: Any,
    env: Any,
) -> str:
    """Close a heading, on a full page with a ``#`` link to copy its URL.

    Args:
        renderer (Any): markdown-it-py's HTML renderer (unused).
        tokens (Sequence[Token]): The document's tokens.
        index (int): Position of this ``heading_close`` token.
        options (Any): Renderer options (unused).
        env (Any): Renderer environment; :data:`HEADING_LINKS` set to true
            asks for the link.

    Returns:
        str: The optional anchor link and the closing tag, such as ``</h2>``.

    Raises:
        None: Only string formatting is performed.
    """
    # A heading is always heading_open, inline, heading_close.
    slug = tokens[index - 2].attrGet("id")
    anchor = ""
    if slug and env.get(HEADING_LINKS):
        href = html.escape(f"#{slug}")
        anchor = (
            f'<a class="anchor" href="{href}" aria-label="Link to this section">#</a>'
        )
    return f"{anchor}</{tokens[index].tag}>\n"


def inline_text(token: Token) -> str:
    """Return the plain text of an inline token, as a browser would show it.

    Args:
        token (Token): An ``inline`` token, such as a heading's content.

    Returns:
        str: Text and code spans joined, without markup or raw HTML.

    Raises:
        None: Only the token's children are read.
    """
    parts = []
    for child in token.children or ():
        if child.type in {"text", "code_inline"}:
            parts.append(child.content)
        elif child.type in {"softbreak", "hardbreak"}:
            parts.append(" ")
    return "".join(parts).strip()


def parse_markdown(text: str, *, escape_html: bool = False) -> Document:
    """Parse Markdown and give every heading a unique GitHub-style ``id``.

    Args:
        text (str): Markdown source.
        escape_html (bool): Show raw HTML as text instead of passing it
            through.

    Returns:
        Document: Tokens ready for :func:`rewrite_links` and
            :func:`render_body`.

    Raises:
        MarkdownDependencyError: If markdown-it-py is not installed.
    """
    parser = create_parser(escape_html=escape_html)
    env: dict[str, Any] = {}
    tokens = parser.parse(text, env)
    headings = []
    seen: dict[str, int] = {}
    for index, token in enumerate(tokens):
        if token.type != "heading_open":
            continue
        heading_text = inline_text(tokens[index + 1])
        slug = unique_slug(slugify(heading_text), seen)
        token.attrSet("id", slug)
        headings.append(Heading(int(token.tag[1:]), heading_text, slug))
    return Document(tokens, tuple(headings), parser, env)


def _link_tokens(tokens: Sequence[Token]) -> Iterator[tuple[Token, str, int]]:
    """Yield every link and image token with its URL attribute and line.

    Args:
        tokens (Sequence[Token]): A document's block-level tokens.

    Returns:
        Iterator[tuple[Token, str, int]]: The token, ``"href"`` or ``"src"``,
            and the 1-based source line of the enclosing block.

    Raises:
        None: Only the token tree is walked.
    """
    for token in tokens:
        if token.type != "inline":
            continue
        # Inline tokens carry no line map of their own; the block's is used.
        line = token.map[0] + 1 if token.map else 0
        for child in token.children or ():
            if child.type == "link_open":
                yield child, "href", line
            elif child.type == "image":
                yield child, "src", line


def find_links(document: Document) -> list[LinkReference]:
    """List the links and images in a document.

    Args:
        document (Document): A parsed document.

    Returns:
        list[LinkReference]: Every link and image, in document order.

    Raises:
        None: Only the tokens are read.
    """
    return [
        LinkReference(
            str(token.attrGet(attribute) or ""),
            "link" if attribute == "href" else "image",
            line,
        )
        for token, attribute, line in _link_tokens(document.tokens)
    ]


def rewrite_links(
    document: Document,
    rewrite: Callable[[LinkReference], str | None],
) -> None:
    """Replace link and image URLs in place before rendering.

    Args:
        document (Document): A parsed document; its tokens are changed.
        rewrite (Callable[[LinkReference], str | None]): Called once per link
            or image; returns the new URL, or ``None`` to keep the old one.

    Returns:
        None: The document's tokens are updated in place.

    Raises:
        Exception: Whatever ``rewrite`` raises.
    """
    for token, attribute, line in _link_tokens(document.tokens):
        url = str(token.attrGet(attribute) or "")
        kind = "link" if attribute == "href" else "image"
        new_url = rewrite(LinkReference(url, kind, line))
        if new_url is not None:
            token.attrSet(attribute, new_url)


def render_body(document: Document, *, heading_links: bool = False) -> str:
    """Render a parsed document to an HTML fragment.

    Args:
        document (Document): A parsed, possibly rewritten, document.
        heading_links (bool): End each heading with a ``#`` link to itself.
            Pages style it to appear on hover; leave it off for HTML that is
            pasted into other pages, where it would show as a stray ``#``.

    Returns:
        str: HTML for the inside of a page's ``<main>`` element.

    Raises:
        None: Rendering only formats tokens.
    """
    # markdown-it-py hands `env` to every render rule; this is how a rule
    # learns about a choice made for this one rendering.
    env = {**document.env, HEADING_LINKS: heading_links}
    return document.parser.renderer.render(
        document.tokens, document.parser.options, env
    )


def markdown_to_html(text: str, *, escape_html: bool = False) -> str:
    """Convert Markdown to an HTML fragment in one call.

    Args:
        text (str): Markdown source.
        escape_html (bool): Show raw HTML as text instead of passing it
            through.

    Returns:
        str: The HTML fragment, without ``<html>``, styles, or navigation.

    Raises:
        MarkdownDependencyError: If markdown-it-py is not installed.
    """
    return render_body(parse_markdown(text, escape_html=escape_html))
