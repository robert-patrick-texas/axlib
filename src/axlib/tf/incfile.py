# Copyright 2026 Robert Patrick
# SPDX-License-Identifier: Apache-2.0

r"""Expand include directives in network templates and configuration fragments.

Large network templates are easier to maintain when common sections live in
separate files.  This module replaces lines beginning with ``$include``,
``#include``, ``@include``, or ``@import`` with the referenced file content.
Quoted paths, recursive includes, relative-path resolution, cycle detection, and
a maximum depth are supported.  Missing or blocked includes remain visible in
the output instead of disappearing silently.

Only the Python standard library is required.

CLI example:
    ``python -m axlib.tf.incfile site.conf -o site.expanded.conf``

Python example:
    >>> from pathlib import Path
    >>> base = Path("/tmp")
    >>> isinstance(expand_includes("hostname edge-1\n", base_dir=base), str)
    True
"""

from __future__ import annotations

import argparse
import os
import re
from collections.abc import Sequence
from pathlib import Path

from ._cli import add_text_io_arguments, read_text, render_cli_error, write_text

DEFAULT_DIRECTIVES = ("$include", "#include", "@include", "@import")


def build_directive_pattern(directives: Sequence[str]) -> re.Pattern[str]:
    """Compile a case-insensitive pattern for supported include lines.

    Args:
        directives (Sequence[str]): Directive words such as ``@include``.

    Returns:
        re.Pattern[str]: Pattern whose filename is captured from double-quoted,
            single-quoted, or unquoted syntax.

    Raises:
        ValueError: If no directive is supplied or a directive is blank.
        re.error: If Python cannot compile the generated escaped pattern.
    """
    cleaned = [directive for directive in directives if directive]
    if len(cleaned) != len(directives) or not cleaned:
        raise ValueError("At least one non-blank directive must be provided.")

    # re.escape treats symbols such as '#' and '$' literally rather than as
    # regular-expression syntax, allowing operators to define intuitive tokens.
    directive_group = "|".join(re.escape(item) for item in cleaned)
    pattern = rf"""
        ^\s*                                 # optional indentation
        ({directive_group})                  # include directive
        \s+                                  # required separator
        (?:
            "([^"]+)"                        # double-quoted path
            |
            '([^']+)'                        # single-quoted path
            |
            (\S+)                            # unquoted path
        )
        (?:\s+.*)?                           # ignored trailing annotation
        $
    """
    return re.compile(pattern, re.IGNORECASE | re.VERBOSE)


def extract_filename(match: re.Match[str]) -> str:
    """Extract the filename from a successful directive match.

    Args:
        match (re.Match[str]): Match returned by ``build_directive_pattern``.

    Returns:
        str: Referenced filename without surrounding quotes.

    Raises:
        ValueError: If the supplied match unexpectedly has no filename group.
    """
    for group_number in (2, 3, 4):
        value = match.group(group_number)
        if value is not None:
            return value
    raise ValueError("Matched include directive did not contain a filename.")


def normalize_path(path: Path) -> Path:
    """Create an absolute normalized path without requiring it to exist.

    Args:
        path (pathlib.Path): Candidate include path.

    Returns:
        pathlib.Path: Absolute path with ``.`` and ``..`` resolved.

    Raises:
        OSError: If the operating system cannot resolve the current directory or
            path metadata needed by :meth:`Path.resolve`.
    """
    # Stable absolute paths make cycle detection reliable when two directives
    # spell the same file differently, such as ./common.conf and sub/../common.conf.
    return path.resolve(strict=False)


def resolve_include_path(filename: str, base_dir: Path) -> Path:
    """Resolve one include filename relative to its containing file.

    Args:
        filename (str): Quoted or unquoted path extracted from a directive.
        base_dir (pathlib.Path): Directory of the text containing the directive.

    Returns:
        pathlib.Path: Absolute normalized target path.

    Raises:
        OSError: Propagated if path normalization fails.
    """
    candidate = Path(filename).expanduser()
    target = candidate if candidate.is_absolute() else base_dir / candidate
    return normalize_path(target)


def read_text_file(path: Path, encoding: str = "utf-8") -> str | None:
    """Read an include target while distinguishing a missing file.

    Args:
        path (pathlib.Path): Include target.
        encoding (str): Text codec used for the included file.

    Returns:
        str | None: File content, or ``None`` when the path does not exist.

    Raises:
        OSError: If the file exists but cannot be opened or read.
        UnicodeDecodeError: If bytes are invalid for ``encoding``.
        LookupError: If ``encoding`` is unknown.
    """
    try:
        # The context manager closes each include promptly, avoiding descriptor
        # exhaustion when a generated configuration references many fragments.
        with path.open("r", encoding=encoding, newline="") as handle:
            return handle.read()
    except FileNotFoundError:
        return None


def _expand_includes_internal(
    text: str,
    *,
    pattern: re.Pattern[str],
    encoding: str,
    current_base_dir: Path,
    recursive: bool,
    max_depth: int,
    current_depth: int,
    active_stack: set[Path],
) -> str:
    """Perform recursive expansion with depth and cycle guardrails.

    Args:
        text (str): Text being processed at the current include level.
        pattern (re.Pattern[str]): Compiled directive-line pattern.
        encoding (str): Codec used for included files.
        current_base_dir (pathlib.Path): Directory for relative paths in ``text``.
        recursive (bool): Whether nested include directives are expanded.
        max_depth (int): Maximum number of nested expansion levels.
        current_depth (int): Current nested level; initial input is level zero.
        active_stack (set[pathlib.Path]): Files in the active include chain.

    Returns:
        str: Expanded text.

    Raises:
        OSError: If an existing include target cannot be read or normalized.
        UnicodeDecodeError: If an included file cannot be decoded.
        LookupError: If the requested encoding is unknown.
        ValueError: If an internal regex match lacks a filename.
    """
    output_parts: list[str] = []
    for line in text.splitlines(keepends=True):
        match = pattern.match(line)
        if match is None:
            output_parts.append(line)
            continue

        include_path = resolve_include_path(
            extract_filename(match),
            current_base_dir,
        )
        included_text = read_text_file(include_path, encoding=encoding)
        if included_text is None:
            # Preserving an unresolved directive makes a missing fragment visible
            # during review rather than silently producing an incomplete config.
            output_parts.append(line)
            continue
        if not recursive or current_depth >= max_depth:
            output_parts.append(included_text)
            continue
        if include_path in active_stack:
            # A cycle is kept as the original line so engineers can identify the
            # offending relationship without an infinite recursion or data loss.
            output_parts.append(line)
            continue

        next_stack = set(active_stack)
        next_stack.add(include_path)
        output_parts.append(
            _expand_includes_internal(
                included_text,
                pattern=pattern,
                encoding=encoding,
                current_base_dir=include_path.parent,
                recursive=True,
                max_depth=max_depth,
                current_depth=current_depth + 1,
                active_stack=next_stack,
            )
        )
    return "".join(output_parts)


def expand_includes(
    text: str,
    *,
    directives: Sequence[str] | None = None,
    recursive: bool = True,
    max_depth: int = 20,
    encoding: str = "utf-8",
    base_dir: str | os.PathLike[str] | None = None,
) -> str:
    """Expand include directives in a block of text.

    Args:
        text (str): Main template or configuration text.
        directives (Sequence[str] | None): Case-insensitive directive words;
            defaults to :data:`DEFAULT_DIRECTIVES`.
        recursive (bool): Expand directives found inside included files.
        max_depth (int): Number of nested expansion levels allowed after direct
            includes.  Zero inserts direct files without scanning them again.
        encoding (str): Codec used to read include targets.
        base_dir (str | os.PathLike[str] | None): Directory for relative paths in
            the initial text; defaults to the current working directory.

    Returns:
        str: Text with all allowed, readable includes expanded.

    Raises:
        ValueError: If ``max_depth`` is negative or directives are invalid.
        OSError: If an existing include target cannot be read or a path cannot be
            resolved.
        UnicodeDecodeError: If an included file cannot be decoded.
        LookupError: If ``encoding`` is unknown.
    """
    if max_depth < 0:
        raise ValueError("max_depth must be >= 0")
    directive_list = (
        list(DEFAULT_DIRECTIVES) if directives is None else list(directives)
    )
    pattern = build_directive_pattern(directive_list)
    initial_path = Path(base_dir) if base_dir is not None else Path.cwd()
    initial_base = normalize_path(initial_path)
    return _expand_includes_internal(
        text,
        pattern=pattern,
        encoding=encoding,
        current_base_dir=initial_base,
        recursive=recursive,
        max_depth=max_depth,
        current_depth=0,
        active_stack=set(),
    )


transform_text = expand_includes


def build_arg_parser() -> argparse.ArgumentParser:
    """Build the standardized include-expansion parser.

    Args:
        None: Parser configuration is defined by this module.

    Returns:
        argparse.ArgumentParser: Parser with include and common text I/O options.

    Raises:
        argparse.ArgumentError: If an option conflicts with another definition.
    """
    parser = argparse.ArgumentParser(
        description=(
            "Expand include directives with relative paths, cycle detection, "
            "and a recursion-depth limit."
        )
    )
    parser.add_argument(
        "-d",
        "--directive",
        action="append",
        dest="directives",
        help="Custom directive word. May be repeated; replaces the defaults.",
    )
    parser.add_argument(
        "--no-recursive",
        action="store_true",
        help="Insert direct include files without expanding their nested includes.",
    )
    parser.add_argument(
        "--max-depth",
        type=int,
        default=20,
        help="Maximum nested include depth after direct includes (default: 20).",
    )
    parser.add_argument(
        "--base-dir",
        type=Path,
        help=(
            "Directory for relative includes in the initial input. For a file "
            "input, defaults to that file's parent; for stdin, current directory."
        ),
    )
    add_text_io_arguments(parser)
    return parser


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    """Parse command-line arguments using the public standardized parser.

    Args:
        argv (Sequence[str] | None): Arguments excluding the program name.

    Returns:
        argparse.Namespace: Parsed include and text-I/O options.

    Raises:
        SystemExit: If argparse encounters invalid syntax or ``--help``.
    """
    return build_arg_parser().parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    """Expand includes for a file or pipeline.

    Args:
        argv (Sequence[str] | None): Arguments excluding the program name.

    Returns:
        int: ``0`` on success, ``1`` for operational errors, or ``2`` for a
            negative depth or invalid directive.

    Raises:
        None: Expected errors are rendered and converted to exit statuses.
    """
    parser = build_arg_parser()
    args = parser.parse_args(argv)
    if args.max_depth < 0:
        parser.error("--max-depth must be >= 0")

    if args.base_dir is not None:
        base_dir = args.base_dir
    elif args.input != "-":
        base_dir = Path(args.input).expanduser().resolve(strict=False).parent
    else:
        base_dir = Path.cwd()

    try:
        text = read_text(args.input, encoding=args.encoding)
        output = expand_includes(
            text,
            directives=args.directives,
            recursive=not args.no_recursive,
            max_depth=args.max_depth,
            encoding=args.encoding,
            base_dir=base_dir,
        )
        write_text(args.output, output, encoding=args.encoding)
    except ValueError as exc:
        parser.error(str(exc))
    except (OSError, UnicodeError, LookupError, BrokenPipeError) as exc:
        return render_cli_error("incfile", exc)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
