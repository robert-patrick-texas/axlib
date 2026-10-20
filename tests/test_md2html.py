# Copyright 2026 Robert Patrick
# SPDX-License-Identifier: Apache-2.0

"""Tests for converting Markdown files to HTML pages."""

from __future__ import annotations

import io
import json
import sys
from pathlib import Path

import pytest

from axlib.__main__ import main as axlib_main
from axlib.md2html import (
    MarkdownDependencyError,
    Md2HtmlError,
    SiteOptions,
    build_site,
    convert_paths,
    convert_text,
    markdown_to_html,
    parse_markdown,
    slugify,
)
from axlib.md2html.__main__ import main
from axlib.md2html.page import NavEntry, render_page, render_sections
from axlib.md2html.render import Heading, unique_slug

ROOT = Path(__file__).resolve().parents[1]


def write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


# --- render: Markdown to an HTML fragment -----------------------------------


@pytest.mark.parametrize(
    ("text", "slug"),
    [
        ("Recorded options", "recorded-options"),
        ("Scheduled jobs (cron and systemd)", "scheduled-jobs-cron-and-systemd"),
        ("rotate_key()", "rotate_key"),
        (
            "Comparing collections: axlib parse diff",
            "comparing-collections-axlib-parse-diff",
        ),
        ("A - B", "a---b"),
        (
            "Adding libraries for everyone (netmiko, scrapli, ...)",
            "adding-libraries-for-everyone-netmiko-scrapli-",
        ),
        ("Café 1.0", "café-10"),
        ("🎉 Party", "-party"),
        ("C++ / C#", "c--c"),
    ],
)
def test_slugify_matches_github_anchors(text: str, slug: str) -> None:
    assert slugify(text) == slug


def test_unique_slug_numbers_repeats_like_github() -> None:
    seen: dict[str, int] = {}

    slugs = [unique_slug(slug, seen) for slug in ("a", "a", "a-1", "a")]

    assert slugs == ["a", "a-1", "a-1-1", "a-2"]


def test_headings_get_ids_anchor_links_and_the_title() -> None:
    document = parse_markdown(
        "# Change plan\n\n## Steps\n\ntext\n\n## Steps\n\n### `drain()` the *link*\n"
    )

    assert document.title == "Change plan"
    assert [(h.level, h.text, h.slug) for h in document.headings] == [
        (1, "Change plan", "change-plan"),
        (2, "Steps", "steps"),
        (2, "Steps", "steps-1"),
        (3, "drain() the link", "drain-the-link"),
    ]
    # A fragment keeps the ids; full pages add a # link to copy each URL.
    assert markdown_to_html("## Steps") == '<h2 id="steps">Steps</h2>\n'
    assert (
        '<h2 id="steps">Steps<a class="anchor" href="#steps" '
        'aria-label="Link to this section">#</a></h2>'
    ) in convert_text("## Steps")


def test_github_flavored_extensions_are_rendered() -> None:
    html = markdown_to_html(
        "| a | b |\n|---|--:|\n| 1 | ~~2~~ |\n\nSee https://example.com/x.\n"
    )

    assert "<table>" in html
    assert '<td style="text-align:right"><s>2</s></td>' in html
    assert '<a href="https://example.com/x">https://example.com/x</a>.' in html


def test_task_lists_and_footnotes_render_like_github() -> None:
    html = markdown_to_html("- [ ] drain sw1\n- [x] backup\n\nBug[^1]\n\n[^1]: CSC1.\n")

    assert '<input class="task-list-item-checkbox" disabled="disabled"' in html
    assert 'checked="checked"' in html
    assert '<a href="#fn1" id="fnref1">[1]</a>' in html
    assert '<li id="fn1" class="footnote-item"><p>CSC1.' in html


def test_raw_html_passes_through_unless_escaped() -> None:
    text = "<details><summary>More</summary>hidden</details>\n\nA <b>bold</b> word.\n"

    assert "<details>" in markdown_to_html(text)
    escaped = markdown_to_html(text, escape_html=True)
    assert "<details>" not in escaped
    assert "&lt;b&gt;bold&lt;/b&gt;" in escaped


def test_script_links_are_refused() -> None:
    html = markdown_to_html("[click](javascript:alert(1))")

    assert "javascript:" not in html.replace("[click](javascript:alert(1))", "")
    assert "<a " not in html


def test_missing_markdown_it_explains_how_to_install(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A None entry in sys.modules makes the import raise ImportError.
    monkeypatch.setitem(sys.modules, "markdown_it", None)

    with pytest.raises(MarkdownDependencyError, match=r"uv add 'axlib\[markdown\]'"):
        markdown_to_html("# Title")


# --- page: the HTML layout ---------------------------------------------------


def test_sections_nest_level_three_under_level_two() -> None:
    headings = [
        Heading(1, "Title", "title"),
        Heading(3, "Early", "early"),
        Heading(2, "One", "one"),
        Heading(3, "One A", "one-a"),
        Heading(3, "One B", "one-b"),
        Heading(4, "Deep", "deep"),
        Heading(2, "Two", "two"),
    ]

    html = render_sections(headings)

    assert html == (
        '<ul class="sections">\n'
        '<li><a href="#early">Early</a></li>\n'
        '<li><a href="#one">One</a>\n<ul class="sections">\n'
        '<li><a href="#one-a">One A</a></li>\n'
        '<li><a href="#one-b">One B</a></li>\n</ul></li>\n'
        '<li><a href="#two">Two</a></li>\n</ul>'
    )
    assert render_sections([Heading(1, "Title", "title")]) == ""


def test_page_has_top_bar_links_sidebar_and_footer() -> None:
    page = render_page(
        title="Notes <draft>",
        body="<h1>Notes</h1>\n",
        nav=[
            NavEntry("Home", "index.html"),
            NavEntry("Notes <draft>", "n.html", current=True),
        ],
        sections=[Heading(2, "Steps", "steps")],
        site_name="Team & docs",
        home_href="index.html",
        links=[("GitHub", "https://github.com/org/repo")],
        source_label="docs/n.md",
        source_href="https://github.com/org/repo/blob/main/docs/n.md",
    )

    assert "<title>Notes &lt;draft&gt; - Team &amp; docs</title>" in page
    assert '<a class="site-name" href="index.html">Team &amp; docs</a>' in page
    assert '<a href="https://github.com/org/repo">GitHub</a>' in page
    assert '<li class="current"><a href="n.html" aria-current="page">' in page
    assert '<a href="#steps">Steps</a>' in page
    assert (
        'Converted from <a href="https://github.com/org/repo/blob/main/docs/n.md">'
        "docs/n.md</a>" in page
    )
    assert "<script" not in page


def test_page_without_site_name_or_links_has_no_top_bar() -> None:
    page = render_page(
        title="Notes", body="<p>x</p>", nav=[NavEntry("Notes", "#", current=True)]
    )

    assert "<header" not in page
    assert "<title>Notes</title>" in page
    assert 'class="page-footer"' not in page


# --- site: many files, cross-linked ------------------------------------------


@pytest.fixture
def docs_tree(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    # Messages show files relative to the current directory.
    monkeypatch.chdir(tmp_path)
    write(
        tmp_path / "README.md",
        "# Project\n\nSee [install](docs/INSTALL.md#offline-hosts), "
        "[the docs folder](docs/), [the logo](img/logo.png), "
        "[web](https://example.com/a.md) and [mail](mailto:noc@example.com).\n",
    )
    write(
        tmp_path / "docs" / "INSTALL.md",
        "# Installing\n\n## Offline hosts\n\nBack to [the start](../README.md) "
        "or [here](#offline-hosts).\n",
    )
    write(
        tmp_path / "docs" / "README.md", "# Docs index\n\nNothing [here](#nowhere).\n"
    )
    write(tmp_path / "img" / "logo.png", "not really a PNG")
    return tmp_path


def test_links_between_pages_are_rewritten_to_html(docs_tree: Path) -> None:
    site = build_site(
        [docs_tree / "README.md", docs_tree / "docs"], index=docs_tree / "README.md"
    )

    pages = {page.name: page for page in site.pages}
    assert [page.name for page in site.pages] == [
        "README.md",
        "docs/INSTALL.md",
        "docs/README.md",
    ]
    assert pages["README.md"].output_name == "index.html"
    readme = pages["README.md"].body
    assert 'href="docs/INSTALL.html#offline-hosts"' in readme
    assert 'href="docs/README.html"' in readme
    assert 'href="img/logo.png"' in readme
    assert 'href="https://example.com/a.md"' in readme
    assert 'href="mailto:noc@example.com"' in readme
    install = pages["docs/INSTALL.md"].html
    assert 'href="../index.html">the start</a>' in install
    assert '<a class="site-name"' not in install
    # The sidebar lists every page relative to the current one.
    assert '<a href="../index.html">Project</a>' in install
    assert '<a href="README.html">Docs index</a>' in install
    assert [str(problem) for problem in site.problems] == [
        "docs/README.md:3: no section #nowhere on this page"
    ]
    assert site.assets == ()


def test_broken_links_and_missing_sections_are_reported(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    write(
        tmp_path / "a.md",
        "# A\n\n[gone](missing.md)\n\n[wrong section](b.md#nope)\n\n"
        "![diagram](pics/net.svg)\n",
    )
    write(tmp_path / "b.md", "# B\n\n## Yes\n")

    site = build_site([tmp_path])

    assert [(p.source, p.line, p.message) for p in site.problems] == [
        ("a.md", 3, "missing.md does not exist"),
        ("a.md", 5, "no section #nope in b.md"),
        ("a.md", 7, "pics/net.svg does not exist"),
    ]


def test_linked_files_are_copied_only_to_another_directory(
    docs_tree: Path, tmp_path: Path
) -> None:
    output = tmp_path / "site"

    report = convert_paths(
        [docs_tree / "README.md", docs_tree / "docs"],
        output,
        index=docs_tree / "README.md",
        options=SiteOptions(site_name="Project", links=(("GitHub", "https://g"),)),
    )

    assert sorted(p.relative_to(output).as_posix() for _, p in report.pages) == [
        "docs/INSTALL.html",
        "docs/README.html",
        "index.html",
    ]
    assert [p.relative_to(output).as_posix() for p in report.copied] == ["img/logo.png"]
    assert (output / "img" / "logo.png").read_text() == "not really a PNG"
    install = (output / "docs" / "INSTALL.html").read_text()
    assert '<a class="site-name" href="../index.html">Project</a>' in install
    assert '<a href="https://g">GitHub</a>' in install

    beside = convert_paths([docs_tree / "docs" / "INSTALL.md"])
    assert beside.copied == ()
    assert (docs_tree / "docs" / "INSTALL.html").is_file()


def test_link_outside_the_base_is_reported_when_copying(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    write(tmp_path / "outside.txt", "x")
    write(tmp_path / "notes" / "a.md", "# A\n\n[file](../outside.txt)\n")

    copied = build_site([tmp_path / "notes"], output_dir=tmp_path / "site")
    beside = build_site([tmp_path / "notes"])

    assert [p.message for p in copied.problems] == [
        "outside.txt is outside notes, so it is not copied"
    ]
    assert beside.problems == ()


def test_hidden_files_and_folders_are_not_copied(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    write(tmp_path / "notes" / ".secrets" / "key.txt", "private")
    write(tmp_path / "notes" / "configs" / "sw1.cfg", "hostname sw1")
    write(
        tmp_path / "notes" / "a.md",
        "# A\n\n[key](.secrets/key.txt)\n\n[configs](configs/)\n\n"
        "[gone](../../nowhere.txt)\n",
    )

    copied = build_site([tmp_path / "notes"], output_dir=tmp_path / "site")
    beside = build_site([tmp_path / "notes"])

    assert [p.message for p in copied.problems] == [
        "notes/.secrets/key.txt is hidden, so it is not copied",
        "notes/configs is a folder with no converted index.md or README.md",
        f"{(tmp_path.parent / 'nowhere.txt').resolve()} does not exist",
    ]
    assert copied.assets == ()
    # Beside the Markdown the links work as written; only the missing file
    # is a problem, even though it is outside the base directory.
    assert [p.message for p in beside.problems] == [copied.problems[2].message]


def test_directory_search_order_recursion_and_hidden_files(tmp_path: Path) -> None:
    write(tmp_path / "b.md", "# B\n")
    write(tmp_path / "a.markdown", "# A\n")
    write(tmp_path / "notes.txt", "not markdown")
    write(tmp_path / "sub" / "c.md", "# C\n")
    write(tmp_path / ".hidden" / "d.md", "# D\n")
    write(tmp_path / ".e.md", "# E\n")

    flat = build_site([tmp_path])
    deep = build_site([tmp_path], recursive=True)

    assert [page.name for page in flat.pages] == ["a.markdown", "b.md"]
    assert [page.name for page in deep.pages] == ["a.markdown", "b.md", "sub/c.md"]


def test_titles_fall_back_to_the_file_name(tmp_path: Path) -> None:
    write(tmp_path / "runbook.md", "Just text, no heading.\n")

    site = build_site([tmp_path / "runbook.md"])

    assert site.pages[0].title == "runbook"
    assert "<title>runbook</title>" in site.pages[0].html


@pytest.mark.parametrize(
    ("files", "paths", "index", "message"),
    [
        ({}, ["nothing.md"], None, "nothing.md: no such file or directory"),
        ({"x.txt": "x"}, ["."], None, "no Markdown files found"),
        (
            {"README.md": "# R\n", "index.md": "# I\n"},
            ["."],
            "README.md",
            "README.md and index.md would both become index.html",
        ),
        ({"a.md": "# A\n"}, ["a.md"], "b.md", "--index must be one"),
        ({"a.md": b"\xff\xfe# A"}, ["a.md"], None, r"a\.md: not UTF-8 text"),
    ],
)
def test_inputs_that_cannot_be_converted(
    tmp_path: Path,
    files: dict[str, str | bytes],
    paths: list[str],
    index: str | None,
    message: str,
) -> None:
    for name, content in files.items():
        if isinstance(content, bytes):
            (tmp_path / name).write_bytes(content)
        else:
            write(tmp_path / name, content)

    with pytest.raises(Md2HtmlError, match=message):
        build_site(
            [tmp_path / path for path in paths],
            index=tmp_path / index if index else None,
        )


def test_convert_text_builds_one_page_with_its_own_sections() -> None:
    page = convert_text(
        "Intro\n\n## One\n\n## Two\n", options=SiteOptions(site_name="NOC")
    )

    assert "<title>Document - NOC</title>" in page
    assert '<a href="#one">One</a>' in page
    assert '<span class="site-name">NOC</span>' in page


def test_project_documentation_has_no_broken_links(tmp_path: Path) -> None:
    # The published site is built from these files into another folder (see
    # the Makefile's docs target), so every link and #section must resolve
    # and every linked file must be one that can be copied there.
    paths = [
        ROOT / "README.md",
        ROOT / "docs",
        ROOT / "examples" / "parse" / "README.md",
        ROOT / "examples" / "text" / "README.md",
        ROOT / "CHANGELOG.md",
        ROOT / "RELEASE_NOTES.md",
    ]

    site = build_site(paths, output_dir=tmp_path / "site", index=ROOT / "README.md")

    assert [str(problem) for problem in site.problems] == []
    assert "docs/MARKDOWN.md" in {page.name for page in site.pages}


# --- command line ------------------------------------------------------------


def run(
    argv: list[str], capsys: pytest.CaptureFixture[str], stdin: str | None = None
) -> tuple[int, str, str]:
    if stdin is not None:
        sys.stdin = io.StringIO(stdin)
    try:
        status = main(argv)
    finally:
        sys.stdin = sys.__stdin__
    captured = capsys.readouterr()
    return status, captured.out, captured.err


def test_cli_converts_standard_input_to_standard_output(
    capsys: pytest.CaptureFixture[str],
) -> None:
    status, out, err = run(
        ["--title", "Piped", "--link", "Wiki=https://w"], capsys, "# x\n"
    )

    assert (status, err) == (0, "")
    assert out.startswith("<!DOCTYPE html>")
    assert "<title>Piped</title>" in out
    assert '<a href="https://w">Wiki</a>' in out

    status, out, _ = run(["--fragment", "-"], capsys, "*hi*\n")
    assert (status, out) == (0, "<p><em>hi</em></p>\n")


def test_cli_writes_one_file_beside_it_or_where_asked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)
    write(tmp_path / "plan.md", "# Plan\n\n[gone](#nope)\n")

    assert run(["plan.md"], capsys)[1:] == (
        "plan.md -> plan.html\n",
        "plan.md:3: no section #nope on this page\n",
    )
    assert run(["plan.md", "-o", "out.html", "-q"], capsys) == (
        0,
        "",
        "plan.md:3: no section #nope on this page\n",
    )
    assert (tmp_path / "out.html").read_text().startswith("<!DOCTYPE html>")
    status, out, _ = run(["plan.md", "-o", "-", "--fragment"], capsys)
    assert status == 0
    assert out.startswith('<h1 id="plan">')
    status, out, _ = run(["plan.md", "-o", "html/", "--strict"], capsys)
    assert status == 1
    assert (tmp_path / "html" / "plan.html").is_file()
    status, out, _ = run(["plan.md", "--fragment", "-o", "frag/"], capsys)
    assert (status, out) == (0, f"plan.md -> {Path('frag', 'plan.html')}\n")
    assert (tmp_path / "frag" / "plan.html").read_text().startswith('<h1 id="plan">')


def test_cli_site_json_report_and_strict_status(
    docs_tree: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    output = tmp_path / "out"
    status, out, err = run(
        [
            str(docs_tree / "README.md"),
            str(docs_tree / "docs"),
            "-o",
            str(output),
            "--index",
            str(docs_tree / "README.md"),
            "--json",
            "--strict",
        ],
        capsys,
    )

    report = json.loads(out)
    assert status == 1
    assert err == ""
    assert [page["source"] for page in report["pages"]] == [
        "README.md",
        "docs/INSTALL.md",
        "docs/README.md",
    ]
    assert report["copied"] == [str(output / "img" / "logo.png")]
    assert report["problems"] == [
        {
            "source": "docs/README.md",
            "line": 3,
            "url": "#nowhere",
            "message": "no section #nowhere on this page",
        }
    ]


@pytest.mark.parametrize(
    ("argv", "message"),
    [
        (["missing.md"], "missing.md: no such file or directory"),
        (["a.md", "b.md", "--fragment"], "--fragment needs standard input or one file"),
        (["a.md", "b.md", "-o", "-"], "several pages cannot be written to standard"),
        (["a.md", "-"], "cannot be mixed with files"),
        (["a.md", "--title", "T"], "--title is for standard input"),
        (["a.md", "-o", "-", "--json"], "--json cannot be used with -o -"),
        (["--json"], "--json needs -o FILE"),
        (["-r"], "-r and --index need files"),
        (["a.md", "--index", "b.md"], "b.md: --index must be one of the files"),
    ],
)
def test_cli_errors_exit_2(
    argv: list[str],
    message: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.chdir(tmp_path)
    write(tmp_path / "a.md", "# A\n")
    write(tmp_path / "b.md", "# B\n")

    status, out, err = run(argv, capsys)

    assert (status, out) == (2, "")
    assert err.startswith("axlib md2html: ")
    assert message in err


def test_cli_rejects_a_link_without_url(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exit_info:
        main(["--link", "GitHub"])

    assert exit_info.value.code == 2
    assert "expected LABEL=URL" in capsys.readouterr().err


def test_axlib_dispatches_md2html(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)
    write(tmp_path / "a.md", "# A\n")

    assert axlib_main(["md2html", "a.md", "-q"]) == 0
    assert (tmp_path / "a.html").is_file()
    assert capsys.readouterr().out == ""
