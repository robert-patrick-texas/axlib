#!/usr/bin/env python3
"""
include_expand.py

Purpose:
    Expand include-style directives found in text input.

Execution modes:
    1) Command line:
       - Reads input text from stdin
       - Writes processed output to stdout

    2) Module:
       - Import and call expand_includes(...)

Default directives:
    $include
    #include
    @include
    @import

Default matching behavior:
    - Directive matching is case-insensitive
    - A directive line must begin with optional leading whitespace,
      followed by a supported directive word, followed by whitespace,
      followed by a filename
    - The filename may be:
          * unquoted: file.txt
          * double-quoted: "file name.txt"
          * single-quoted: 'file name.txt'
    - Any content after the filename is ignored
    - If the file is not found, the original directive line is preserved

Relative path behavior:
    - When processing the main stdin input, relative paths resolve against
      the current working directory unless base_dir is explicitly supplied
    - When processing an included file, relative paths inside that file
      resolve relative to the directory containing that included file

Recursion / loop behavior:
    - Recursive include expansion is enabled by default
    - Cyclic include references are detected and blocked
    - A configurable maximum include depth is enforced

Examples of supported directive lines:
    @include ./data.txt
    @IMPORT "../configs/base.conf"
    #Include '/tmp/file with spaces.txt'
    $INCLUDE relative/path.txt trailing text ignored

Notes:
    - Included file content is inserted exactly as read
    - When an include is blocked due to cycle detection or depth limit,
      the original directive line is preserved unchanged
"""

import argparse
import os
import re
import sys
from collections.abc import Sequence
from pathlib import Path

DEFAULT_DIRECTIVES: list[str] = [
    "$include",
    "#include",
    "@include",
    "@import",
]


def build_directive_pattern(directives: Sequence[str]) -> re.Pattern[str]:
    """
    Build a case-insensitive regex that matches include directive lines.

    Supported filename forms:
      - unquoted token:     path/to/file.txt
      - double-quoted:      "path with spaces/file.txt"
      - single-quoted:      'path with spaces/file.txt'

    Any content after the filename is ignored.

    Captured groups:
      - group 1: directive
      - group 2: double-quoted filename content, if present
      - group 3: single-quoted filename content, if present
      - group 4: unquoted filename, if present
    """
    if not directives:
        msg = "At least one directive must be provided."
        raise ValueError(msg)

    escaped_directives = [re.escape(item) for item in directives]
    directive_group = "|".join(escaped_directives)

    pattern = rf"""
        ^\s*                                 # optional leading whitespace
        ({directive_group})                  # directive
        \s+                                  # required whitespace
        (?:
            "([^"]+)"                        # double-quoted filename
            |
            '([^']+)'                        # single-quoted filename
            |
            (\S+)                            # unquoted filename
        )
        (?:\s+.*)?                           # ignore any remaining content
        $
    """

    return re.compile(pattern, re.IGNORECASE | re.VERBOSE)


def extract_filename(match: re.Match[str]) -> str:
    """
    Extract the filename from a successful directive-line regex match.
    """
    double_quoted = match.group(2)
    single_quoted = match.group(3)
    unquoted = match.group(4)

    if double_quoted is not None:
        return double_quoted
    if single_quoted is not None:
        return single_quoted
    if unquoted is not None:
        return unquoted

    msg = "Matched directive line did not contain a filename."
    raise ValueError(msg)


def normalize_path(path: Path) -> Path:
    """
    Normalize a path for stable loop detection and file access comparisons.

    resolve(strict=False) makes the path absolute and normalizes '..' / '.'
    without requiring the file to exist.
    """
    return path.resolve(strict=False)


def resolve_include_path(filename: str, base_dir: Path) -> Path:
    """
    Resolve an include filename to an absolute normalized path.

    Rules:
      - Absolute paths are used as-is
      - Relative paths are resolved relative to base_dir
    """
    raw_path = Path(filename)

    if raw_path.is_absolute():
        return normalize_path(raw_path)

    return normalize_path(base_dir / raw_path)


def read_text_file(path: Path, encoding: str = "utf-8") -> str | None:
    """
    Read a text file and return its contents.

    Returns:
        str  -> file content if successful
        None -> file not found

    Raises:
        OSError, UnicodeDecodeError for other read/decode failures
    """
    try:
        with path.open("r", encoding=encoding) as handle:
            return handle.read()
    except FileNotFoundError:
        return None


def _expand_includes_internal(
    text: str,
    *,
    pattern: re.Pattern[str],
    directives: Sequence[str],
    encoding: str,
    current_base_dir: Path,
    current_file: Path | None,
    recursive: bool,
    max_depth: int,
    current_depth: int,
    active_stack: set[Path],
) -> str:
    """
    Internal recursive implementation.

    Args:
        text:
            Text to process.

        pattern:
            Compiled directive regex.

        directives:
            Directive list, preserved for recursive calls.

        encoding:
            Encoding used to read included files.

        current_base_dir:
            Directory used to resolve relative include paths found in `text`.

        current_file:
            The file currently being processed, if any. For stdin / raw text
            calls this can be None.

        recursive:
            Whether recursive include processing is enabled.

        max_depth:
            Maximum allowed include depth.
            Depth 0 means only the original input is processed and includes
            are not expanded recursively beyond direct replacement unless
            current_depth < max_depth.

        current_depth:
            Current recursion depth. The initial caller starts at 0.

        active_stack:
            Set of normalized file paths currently in the active include chain.
            Used for cyclic-include detection.

    Returns:
        Expanded text.
    """
    lines = text.splitlines(keepends=True)
    output_parts: list[str] = []

    for line in lines:
        match = pattern.match(line)
        if not match:
            output_parts.append(line)
            continue

        filename = extract_filename(match)
        include_path = resolve_include_path(filename, current_base_dir)

        included_text = read_text_file(include_path, encoding=encoding)

        # File not found: preserve original line exactly.
        if included_text is None:
            output_parts.append(line)
            continue

        # Non-recursive mode:
        # still allow direct insertion of the referenced file content,
        # but do not expand nested includes found inside that file.
        if not recursive:
            output_parts.append(included_text)
            continue

        # Max depth enforcement.
        # current_depth counts how deep we already are.
        # If current_depth == max_depth, do not recurse into another file.
        if current_depth >= max_depth:
            output_parts.append(line)
            continue

        # Cycle detection.
        # If the target file is already in the active include chain,
        # preserve the original directive line unchanged.
        if include_path in active_stack:
            output_parts.append(line)
            continue

        next_active_stack = set(active_stack)
        next_active_stack.add(include_path)

        expanded_text = _expand_includes_internal(
            text=included_text,
            pattern=pattern,
            directives=directives,
            encoding=encoding,
            current_base_dir=include_path.parent,
            current_file=include_path,
            recursive=True,
            max_depth=max_depth,
            current_depth=current_depth + 1,
            active_stack=next_active_stack,
        )

        output_parts.append(expanded_text)

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
    """
    Expand include directives in text.

    Args:
        text:
            Input text to process.

        directives:
            Optional directive list. Matching is case-insensitive.
            If omitted, DEFAULT_DIRECTIVES is used.

        recursive:
            If True, recursively process include directives found in included
            files. Default: True.

            If False, direct include lines in `text` are replaced by file
            contents, but included content is not scanned again.

        max_depth:
            Maximum include recursion depth. Default: 20.
            Must be >= 0.

            Interpretation:
              - 0: only direct includes from the initial text are inserted,
                   but nested include expansion is blocked
              - 1: one nested expansion level allowed
              - 20: practical default for most use cases

        encoding:
            Encoding used to read included files. Default: utf-8

        base_dir:
            Base directory for resolving relative include paths in the initial
            input text. If omitted, current working directory is used.

    Returns:
        Processed text.

    Behavior:
        - Directive matching is case-insensitive
        - Quoted and unquoted filenames are supported
        - Relative paths in included files resolve relative to the including file
        - If an include target does not exist, the original line is preserved
        - If a cycle is detected, the original line is preserved
        - If max depth would be exceeded, the original line is preserved
    """
    if max_depth < 0:
        msg = "max_depth must be >= 0"
        raise ValueError(msg)

    directive_list = (
        list(directives) if directives is not None else list(DEFAULT_DIRECTIVES)
    )
    pattern = build_directive_pattern(directive_list)

    initial_base_dir = (
        normalize_path(Path(base_dir))
        if base_dir is not None
        else normalize_path(Path.cwd())
    )

    return _expand_includes_internal(
        text=text,
        pattern=pattern,
        directives=directive_list,
        encoding=encoding,
        current_base_dir=initial_base_dir,
        current_file=None,
        recursive=recursive,
        max_depth=max_depth,
        current_depth=0,
        active_stack=set(),
    )


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    """
    Parse CLI arguments.
    """
    parser = argparse.ArgumentParser(
        description=(
            "Read text from stdin and expand include directives. "
            "Directive matching is case-insensitive. "
            "Supports quoted filenames, relative path resolution, cycle detection, "
            "and max recursion depth."
        )
    )

    parser.add_argument(
        "-d",
        "--directive",
        action="append",
        dest="directives",
        default=None,
        help=(
            "Custom directive word to match. "
            "May be specified multiple times. "
            "If omitted, defaults are used."
        ),
    )

    # Keep recursive enabled by default, but allow explicit disable from CLI.
    parser.add_argument(
        "--no-recursive",
        action="store_true",
        help="Disable recursive expansion of includes found inside included files.",
    )

    parser.add_argument(
        "--max-depth",
        type=int,
        default=20,
        help="Maximum recursive include depth. Default: 20",
    )

    parser.add_argument(
        "--encoding",
        default="utf-8",
        help="Encoding used when reading included files. Default: utf-8",
    )

    parser.add_argument(
        "--base-dir",
        default=None,
        help=(
            "Base directory used to resolve relative include paths in the "
            "initial stdin input. Defaults to the current working directory."
        ),
    )

    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    """
    CLI entry point.

    Reads all text from stdin, expands includes, writes result to stdout.
    """
    args = parse_args(argv)

    if args.max_depth < 0:
        print("Error: --max-depth must be >= 0", file=sys.stderr)
        return 2

    input_text = sys.stdin.read()

    output_text = expand_includes(
        text=input_text,
        directives=args.directives,
        recursive=not args.no_recursive,
        max_depth=args.max_depth,
        encoding=args.encoding,
        base_dir=args.base_dir,
    )

    sys.stdout.write(output_text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
