"""Shared command-line input and output helpers for axlib text filters.

All text-filter commands accept the same positional input path, ``-o/--output``
path, and ``--encoding`` option.  A dash means standard input or standard output,
which lets Network Operations staff compose filters in Unix pipelines without
learning a different interface for each module.

Binary file access plus explicit decoding preserves CRLF, LF, and CR line endings
when a transformation promises to keep them.  Context managers close regular
files automatically even if decoding, transformation, or writing fails.

This internal module uses only the Python standard library.

Example:
    ``python -m axlib.tf.rmdouble device.conf -o device.clean.conf``
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import BinaryIO


def add_text_io_arguments(parser: argparse.ArgumentParser) -> None:
    """Add the standard input, output, and encoding arguments to a parser.

    Args:
        parser (argparse.ArgumentParser): Module-specific parser to extend.

    Returns:
        None: Arguments are added to ``parser`` in place.

    Raises:
        argparse.ArgumentError: If a caller has already defined a conflicting
            option name.
    """
    parser.add_argument(
        "input",
        nargs="?",
        default="-",
        metavar="INPUT",
        help="Input text file, or '-' for standard input (default).",
    )
    parser.add_argument(
        "-o",
        "--output",
        default="-",
        metavar="OUTPUT",
        help="Output text file, or '-' for standard output (default).",
    )
    parser.add_argument(
        "--encoding",
        default="utf-8",
        help="Text encoding for files and byte streams (default: utf-8).",
    )


def _stdin_buffer() -> BinaryIO | object:
    """Return the binary standard-input stream when the runtime exposes one.

    Args:
        None: The function inspects :data:`sys.stdin`.

    Returns:
        BinaryIO | object: ``sys.stdin.buffer`` in normal execution, or the text
            stream itself in test harnesses such as ``io.StringIO``.

    Raises:
        None: ``getattr`` supplies a safe fallback.
    """
    return getattr(sys.stdin, "buffer", sys.stdin)


def _stdout_buffer() -> BinaryIO | object:
    """Return the binary standard-output stream when available.

    Args:
        None: The function inspects :data:`sys.stdout`.

    Returns:
        BinaryIO | object: ``sys.stdout.buffer`` in normal execution, or the text
            stream itself in tests.

    Raises:
        None: ``getattr`` supplies a safe fallback.
    """
    return getattr(sys.stdout, "buffer", sys.stdout)


def read_text(source: str, *, encoding: str) -> str:
    """Read text from a path or standard input without newline translation.

    Args:
        source (str): File path or ``"-"`` for standard input.
        encoding (str): Codec used to decode input bytes.

    Returns:
        str: Decoded input text.

    Raises:
        OSError: If a regular file cannot be opened or read.
        UnicodeDecodeError: If bytes are invalid for the selected encoding.
        LookupError: If Python does not recognize the encoding name.
    """
    if source == "-":
        stream = _stdin_buffer()
        data = stream.read()
    else:
        # Binary mode prevents Python from silently converting device-config
        # line endings before a filter can honor --preserve-line-endings.
        with Path(source).open("rb") as handle:
            data = handle.read()

    return data if isinstance(data, str) else data.decode(encoding)


def write_text(destination: str, text: str, *, encoding: str) -> None:
    """Write text to a path or standard output without newline translation.

    Args:
        destination (str): File path or ``"-"`` for standard output.
        text (str): Transformed network configuration or template text.
        encoding (str): Codec used to encode output bytes.

    Returns:
        None: Text is written to the selected destination.

    Raises:
        OSError: If a regular file cannot be opened or written.
        UnicodeEncodeError: If text cannot be represented by the encoding.
        LookupError: If Python does not recognize the encoding name.
        BrokenPipeError: If a downstream pipeline command closes early.
    """
    if destination == "-":
        stream = _stdout_buffer()
        if hasattr(stream, "write"):
            if stream is sys.stdout:
                stream.write(text)
            else:
                try:
                    stream.write(text.encode(encoding))
                except TypeError:
                    # StringIO and similar teaching/test streams accept text,
                    # while normal stdout buffers accept bytes.
                    stream.write(text)
        return

    output_path = Path(destination)
    # A context manager closes the file even if encoding or disk I/O fails,
    # avoiding descriptor leaks in scripts that process many device configs.
    with output_path.open("wb") as handle:
        handle.write(text.encode(encoding))


def render_cli_error(program: str, exc: BaseException) -> int:
    """Write a consistent text-filter error and return a failure status.

    Args:
        program (str): Friendly command or module name.
        exc (BaseException): File, codec, or transformation error.

    Returns:
        int: Exit status ``1`` for an operational failure.

    Raises:
        OSError: If standard error itself cannot be written.
    """
    print(f"{program}: {exc}", file=sys.stderr)
    return 1
