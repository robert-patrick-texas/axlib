# Copyright 2026 Robert Patrick
# SPDX-License-Identifier: Apache-2.0

"""Command line for ``axlib md2html``: convert Markdown files to HTML pages.

Three ways to use it, chosen by what is converted:

* **Standard input** (no ``PATH``, or ``-``): Markdown from a pipe becomes
  one HTML page on standard output, or in the ``-o`` file.
* **One file**: ``notes.md`` becomes ``notes.html`` beside it.  ``-o`` names
  another file, a directory (an existing one, or a name ending in ``/``), or
  ``-`` for standard output.
* **Several files or a directory**: every page is written beside its
  Markdown, or under ``-o DIR`` keeping the same layout, with a sidebar
  listing all of them.  ``-r`` includes subdirectories.

Each page written is listed as ``SOURCE -> HTML``.  Broken links and
``#section`` links to headings that do not exist are printed to standard
error as ``file:line: message``.

Exit status: 0 converted; 1 a link problem was found and ``--strict`` was
given (the pages are still written); 2 the conversion could not be done.

Dependencies:
    ``markdown-it-py``: ``uv add 'axlib[markdown]'``.

Example:
    ``axlib md2html README.md docs/ -o site --index README.md --strict``
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Sequence
from pathlib import Path

from .errors import Md2HtmlError
from .render import markdown_to_html
from .site import (
    ConversionReport,
    SiteOptions,
    build_site,
    convert_paths,
    convert_text,
    display_path,
)

PROGRAM = "axlib md2html"


def parse_link(value: str) -> tuple[str, str]:
    """Split a ``--link LABEL=URL`` value.

    Args:
        value (str): Text such as ``GitHub=https://github.com/ORG/REPO``.

    Returns:
        tuple[str, str]: The label and the URL.

    Raises:
        argparse.ArgumentTypeError: If either side of ``=`` is empty.
    """
    label, separator, url = value.partition("=")
    if not separator or not label.strip() or not url.strip():
        message = f"expected LABEL=URL, got {value!r}"
        raise argparse.ArgumentTypeError(message)
    return label.strip(), url.strip()


def build_arg_parser() -> argparse.ArgumentParser:
    """Build the ``axlib md2html`` argument parser.

    Args:
        None: Options are fixed by this module.

    Returns:
        argparse.ArgumentParser: The configured parser.

    Raises:
        None: Parser construction performs no I/O.
    """
    parser = argparse.ArgumentParser(
        prog=PROGRAM,
        description=(
            "Convert Markdown files to HTML pages with a sidebar listing every "
            "page converted together and the current page's sections."
        ),
        epilog=(
            "Exit status: 0 converted, 1 link problems with --strict, 2 error. "
            "See docs/MARKDOWN.md."
        ),
    )
    parser.add_argument(
        "paths",
        nargs="*",
        metavar="PATH",
        help="Markdown files and directories, in sidebar order; '-' or none "
        "reads standard input.",
    )
    parser.add_argument(
        "-o",
        "--output",
        metavar="OUTPUT",
        help="Standard input: an HTML file or '-' (default). One file: an HTML "
        "file, a directory, or '-' (default: beside the Markdown). Several "
        "files: a directory (default: beside each Markdown file).",
    )
    parser.add_argument(
        "-r",
        "--recursive",
        action="store_true",
        help="Also convert Markdown files in subdirectories of a directory.",
    )
    parser.add_argument(
        "--index",
        metavar="FILE",
        help="Write FILE (one of the files converted, such as README.md) as "
        "index.html and list it first.",
    )
    parser.add_argument(
        "--site-name",
        metavar="NAME",
        help="Name shown in the bar at the top of every page and in the browser tab.",
    )
    parser.add_argument(
        "--link",
        dest="links",
        action="append",
        type=parse_link,
        default=[],
        metavar="LABEL=URL",
        help="Add a link to the bar at the top of every page, such as "
        "GitHub=https://github.com/ORG/REPO.  Repeat for more links.",
    )
    parser.add_argument(
        "--source-url",
        metavar="URL",
        help="Base URL of the Markdown sources; each page's footer links to "
        "its file under it.",
    )
    parser.add_argument(
        "--title",
        help="Page title for standard input (default: the first # heading).",
    )
    parser.add_argument(
        "--fragment",
        action="store_true",
        help="Standard input or one file: write only the converted HTML, "
        "without the page layout.",
    )
    parser.add_argument(
        "--escape-html",
        action="store_true",
        help="Show raw HTML in the Markdown as text; use it for Markdown you "
        "did not write.",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Exit with status 1 when a link problem is found.",
    )
    output = parser.add_mutually_exclusive_group()
    output.add_argument(
        "--json",
        action="store_true",
        help="Print a JSON report of the pages written, files copied, and link "
        "problems.",
    )
    output.add_argument(
        "-q",
        "--quiet",
        action="store_true",
        help="Do not list the pages written.",
    )
    return parser


def _write(destination: str, text: str) -> str:
    """Write text to a file, or to standard output for ``-``.

    Args:
        destination (str): A file path or ``-``.
        text (str): The HTML.

    Returns:
        str: ``destination``, for the list of files written.

    Raises:
        OSError: If the file cannot be written.
    """
    if destination == "-":
        sys.stdout.write(text)
        sys.stdout.flush()
    else:
        path = Path(destination)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return destination


def _is_directory_target(output: str) -> bool:
    """Tell whether ``-o`` names a directory rather than an HTML file.

    Args:
        output (str): The ``-o`` value.

    Returns:
        bool: True for an existing directory or a name ending in a slash.

    Raises:
        None: Only a path check is performed.
    """
    return output.endswith(("/", os.sep)) or Path(output).is_dir()


def _convert_stdin(args: argparse.Namespace, options: SiteOptions) -> ConversionReport:
    """Convert Markdown read from standard input.

    Args:
        args (argparse.Namespace): Parsed options.
        options (SiteOptions): Site name, links, and HTML handling.

    Returns:
        ConversionReport: The output written; links are not checked.

    Raises:
        MarkdownDependencyError: If markdown-it-py is not installed.
        OSError: If the output file cannot be written.
    """
    text = sys.stdin.read()
    if args.fragment:
        html = markdown_to_html(text, escape_html=options.escape_html)
    else:
        html = convert_text(text, title=args.title, options=options)
    target = _write(args.output or "-", html)
    return ConversionReport((("-", Path(target)),), (), ())


def _single_output(source: str, output: str | None) -> str:
    """Decide where one file's HTML goes when it is written on its own.

    Args:
        source (str): The Markdown file.
        output (str | None): The ``-o`` value, if any.

    Returns:
        str: ``NAME.html`` beside the source, ``DIR/NAME.html`` for a
            directory, or ``output`` itself (a file name or ``-``).

    Raises:
        None: Only path arithmetic is performed.
    """
    beside = Path(source).with_suffix(".html")
    if output is None:
        return str(beside)
    if _is_directory_target(output):
        return str(Path(output) / beside.name)
    return output


def _convert_one_to_file(
    args: argparse.Namespace, options: SiteOptions
) -> ConversionReport:
    """Convert one Markdown file to a single HTML file or standard output.

    Used for ``-o FILE``, ``-o -``, and ``--fragment``.  Links are rewritten
    and checked as if the page were beside its Markdown file; linked files
    are not copied.

    Args:
        args (argparse.Namespace): Parsed options; ``paths`` has one entry.
        options (SiteOptions): Site name, links, and HTML handling.

    Returns:
        ConversionReport: The page written and any link problems.

    Raises:
        Md2HtmlError: If the file cannot be converted.
        OSError: If the file cannot be read or written.
    """
    site = build_site(args.paths, index=args.index, options=options)
    page = site.pages[0]
    output = _single_output(args.paths[0], args.output)
    target = _write(output, page.body if args.fragment else page.html)
    source = display_path(page.source)
    return ConversionReport(((source, Path(target)),), (), site.problems)


def _convert(args: argparse.Namespace, options: SiteOptions) -> ConversionReport:
    """Choose standard input, one file, or a site, and convert.

    Args:
        args (argparse.Namespace): Parsed options.
        options (SiteOptions): Site name, links, and HTML handling.

    Returns:
        ConversionReport: What was written.

    Raises:
        Md2HtmlError: If the options do not fit the inputs.
        MarkdownDependencyError: If markdown-it-py is not installed.
        OSError: If a file cannot be read or written.
    """
    if not args.paths or args.paths == ["-"]:
        if args.recursive or args.index:
            raise Md2HtmlError("-r and --index need files, not standard input")
        if args.json and (args.output or "-") == "-":
            raise Md2HtmlError("--json needs -o FILE: the page goes to standard output")
        return _convert_stdin(args, options)
    if "-" in args.paths:
        raise Md2HtmlError("'-' (standard input) cannot be mixed with files")
    if args.title:
        raise Md2HtmlError(
            "--title is for standard input; a file's title is its first # heading"
        )
    if args.json and args.output == "-":
        raise Md2HtmlError("--json cannot be used with -o -: the page goes there")
    single_file = len(args.paths) == 1 and Path(args.paths[0]).is_file()
    if single_file and (
        args.fragment or (args.output and not _is_directory_target(args.output))
    ):
        return _convert_one_to_file(args, options)
    if args.fragment:
        raise Md2HtmlError("--fragment needs standard input or one file")
    if args.output == "-":
        raise Md2HtmlError("several pages cannot be written to standard output")
    return convert_paths(
        args.paths,
        args.output,
        recursive=args.recursive,
        index=args.index,
        options=options,
    )


def main(argv: Sequence[str] | None = None) -> int:
    """Run ``axlib md2html``.

    Args:
        argv (Sequence[str] | None): Arguments excluding the program name.

    Returns:
        int: 0 converted, 1 link problems with ``--strict``, 2 error.

    Raises:
        None: Expected errors are printed and turned into the exit status.
    """
    args = build_arg_parser().parse_args(argv)
    options = SiteOptions(
        site_name=args.site_name,
        links=tuple(args.links),
        source_url=args.source_url,
        escape_html=args.escape_html,
    )
    try:
        report = _convert(args, options)
    except (Md2HtmlError, OSError) as exc:
        print(f"{PROGRAM}: {exc}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(report.to_dict(), indent=2))
    else:
        for problem in report.problems:
            print(problem, file=sys.stderr)
        # Listing the pages on standard output would mix with HTML written there.
        if not args.quiet and all(str(path) != "-" for _, path in report.pages):
            for source, path in report.pages:
                print(f"{source} -> {display_path(path)}")
            for path in report.copied:
                print(f"copied {display_path(path)}")
    return 1 if args.strict and report.problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
