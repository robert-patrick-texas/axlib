# Copyright 2026 Robert Patrick
# SPDX-License-Identifier: Apache-2.0

r"""Split a captured CLI session into one block per show command.

Operators save show-command output in many ways: a terminal session log, a
script that writes each command and its output to a file, or a pipe straight
from ``ssh``.  Before a TextFSM template can parse anything, the text has to
be cut into pieces that each hold the output of exactly one command, because a
template describes one command's output and rejects lines it does not expect.

A line starts a new block when it looks like one of these:

* a prompt followed by a command: ``sw1#show cdp neighbors``,
  ``sw1>show version``, ``sw1(config)#do show ip int brief``,
  ``admin@mx1> show lldp neighbors``, or
  ``RP/0/RSP0/CPU0:xr1#show cdp neighbors detail``.  The host name is taken
  from the prompt.
* a bare command at the start of a line, beginning with ``show`` or
  ``display`` or an abbreviation of them (``sh``, ``sho``, ``dis``, ...).
* a line matching an operator's own ``marker`` regular expression, for site
  formats such as ``===== sw1: show cdp neighbors =====``.

A prompt with no command after it (``sw1#``) ends the current block, which
keeps the closing prompt of a session log out of the last command's output.
Pager prompts (``--More--`` and the backspaces that erase it), carriage
returns, the ``Load for five secs`` / ``Time source is`` lines that IOS
prints when ``terminal exec prompt timestamp`` is on, and the ``{master:0}``
and ``[edit]`` lines Junos prints above a prompt are removed, because each
would make a strict template stop with an error.

Dependencies:
    Python standard library only.

CLI example:
    ``axlib parse split session.log`` lists the commands found in a capture.

Python example:
    >>> log = "sw1#show clock\n*10:01:02.003 UTC Mon Oct 19 2026\nsw1#\n"
    >>> [(block.host, block.command, block.output) for block in split_capture(log)]
    [('sw1', 'show clock', '*10:01:02.003 UTC Mon Oct 19 2026\n')]
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections.abc import Iterator, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path

# A prompt is an optional Junos "user@", an optional IOS-XR route-processor
# location ("RP/0/RSP0/CPU0:"), the host name, an optional mode such as
# "(config-if)", then "#" or ">".  The host name needs at least two characters
# so that route-table lines such as "C>* 10.0.0.0/8" (FRR) are not prompts.
PROMPT_LINE = re.compile(
    r"^(?:[\w.\-]+@)?"
    r"(?:[\w/]+:)?"
    r"(?P<host>[A-Za-z0-9][\w.\-]+)"
    r"(?:\([\w.\-]+\))?"
    r"[#>]\s?(?P<command>.*)$"
)

# A command typed or logged without a prompt.  Only "show" and "display" (the
# Huawei/HPE Comware spelling) and their abbreviations count, and only in
# column 0, so indented output such as "show history" listings is not mistaken
# for a new command.
BARE_COMMAND = re.compile(r"^(?:sh|sho|show|dis|disp|displ|displa|display)\s+\S")

# " --More-- " (IOS, NX-OS, EOS) or "---(more 45%)---" (Junos), plus the
# backspace-space-backspace run a device sends to erase it from the screen.
PAGER_PROMPT = re.compile(
    r" ?(?:--More--|---\(more(?: \d+%)?\)---) ?(?:\x08+ *\x08+)?", re.IGNORECASE
)

# Lines a device adds around output that no template expects: the IOS exec
# prompt timestamp, and the Junos "{master:0}" / "[edit]" lines printed above
# each prompt.
NOISE_LINE = re.compile(
    r"^(?:Load for five secs:|Time source is |No time source, "
    r"|\{\w+(?::\d+)?\}\s*$|\[edit[^\]]*\]\s*$)"
)


@dataclass(frozen=True, slots=True)
class CommandBlock:
    """The output of one command, cut from a capture.

    Attributes:
        command: The command as it appears in the capture, for example
            ``sh cdp nei``.
        output: The command's output, cleaned of pager prompts and noise.
        host: Host name from the prompt (or given by the caller), else
            ``None``.
        source: Where the text came from: a file name, a path relative to
            a searched directory, or ``-`` for standard input.
        line: Line number of the command in ``source``; ``0`` when the caller
            named the command because the text holds only output.
    """

    command: str
    output: str
    host: str | None = None
    source: str = "-"
    line: int = 0

    def to_dict(self) -> dict[str, object]:
        """Return the block as a JSON-ready dictionary.

        Args:
            None: The block's own fields are used.

        Returns:
            dict[str, object]: ``source``, ``line``, ``host``, ``command``,
                and ``output``.

        Raises:
            None: Only field copying is performed.
        """
        return asdict(self)


def clean_line(line: str) -> str:
    r"""Remove pager prompts, backspaces, and carriage returns from one line.

    Args:
        line (str): One line of captured text, without its line ending.

    Returns:
        str: The line as the operator saw it on the screen.

    Raises:
        None: Only regular-expression substitution is performed.

    Example:
        >>> clean_line(" --More-- \x08\x08\x08  \x08\x08\x08Gi1/0/2  connected")
        'Gi1/0/2  connected'
    """
    return PAGER_PROMPT.sub("", line).replace("\x08", "").replace("\r", "")


def _command_start(
    line: str, marker: re.Pattern[str] | None
) -> tuple[str | None, str] | None:
    """Decide whether a line starts a command, and which one.

    Args:
        line (str): One cleaned line.
        marker (re.Pattern[str] | None): Operator pattern with a ``command``
            group and an optional ``host`` group, tried first.

    Returns:
        tuple[str | None, str] | None: ``(host, command)`` for a command line,
            where ``command`` is empty for a bare prompt; ``None`` for output.

    Raises:
        IndexError: If ``marker`` has no ``command`` group.
    """
    if marker is not None and (match := marker.match(line)):
        return match.groupdict().get("host"), match["command"].strip()
    if match := PROMPT_LINE.match(line):
        return match["host"], match["command"].strip()
    if BARE_COMMAND.match(line):
        return None, line.strip()
    return None


def _join_output(lines: Sequence[str]) -> str:
    """Join output lines, dropping blank lines at the end.

    Args:
        lines (Sequence[str]): Output lines without line endings.

    Returns:
        str: Newline-terminated text, or ``""`` when there is no output.

    Raises:
        None: Only list and string operations are performed.
    """
    kept = list(lines)
    while kept and not kept[-1].strip():
        kept.pop()
    return "".join(f"{line}\n" for line in kept)


def split_capture(
    text: str,
    *,
    source: str = "-",
    host: str | None = None,
    marker: str | re.Pattern[str] | None = None,
) -> list[CommandBlock]:
    r"""Cut captured text into one block per command.

    Text before the first command line is ignored (a login banner, for
    example), and so is text after a bare prompt until the next command.

    Args:
        text (str): A session log, a script's output file, or piped text.
        source (str): Name recorded in every block, such as the file name.
        host (str | None): Host name for every block.  When ``None``, each
            block takes the host from its prompt, or from the last prompt seen
            when its command line has none.
        marker (str | re.Pattern[str] | None): Extra pattern for lines that
            start a command, matched at the start of a line.  It must have a
            named group ``command`` and may have one named ``host``.

    Returns:
        list[CommandBlock]: Blocks in capture order.  A command with no output
            (``terminal length 0``) still has a block, with ``output == ""``.

    Raises:
        ValueError: If ``marker`` lacks a ``command`` group.
        re.error: If ``marker`` is not a valid regular expression.

    Example:
        >>> log = "sw1#sh cdp nei\nDevice ID  Local Intrfce\nsw1#\n"
        >>> [(b.command, b.line) for b in split_capture(log, source="sw1.log")]
        [('sh cdp nei', 1)]
    """
    pattern = re.compile(marker) if isinstance(marker, str) else marker
    if pattern is not None and "command" not in pattern.groupindex:
        raise ValueError("the marker pattern needs a (?P<command>...) group")

    blocks: list[CommandBlock] = []
    current: tuple[str, str | None, int] | None = None
    output: list[str] = []
    last_host: str | None = None

    def close() -> None:
        """Turn the open command and its collected lines into a block.

        Args:
            None: Reads ``current`` and ``output`` from the enclosing call.

        Returns:
            None: The block is appended to ``blocks``.

        Raises:
            None: Only list operations are performed.
        """
        if current is not None:
            command, block_host, line = current
            blocks.append(
                CommandBlock(command, _join_output(output), block_host, source, line)
            )

    for number, raw_line in enumerate(text.splitlines(), start=1):
        line = clean_line(raw_line)
        start = _command_start(line, pattern)
        if start is None:
            if current is not None and not NOISE_LINE.match(line):
                output.append(line)
            continue

        close()
        prompt_host, command = start
        last_host = prompt_host or last_host
        current = (command, host or last_host, number) if command else None
        output = []
    close()
    return blocks


def single_block(
    text: str, command: str, *, source: str = "-", host: str | None = None
) -> CommandBlock:
    """Treat all of ``text`` as the output of one named command.

    Use this when the text holds output only, as from
    ``ssh sw1 'show cdp neighbors'`` or a netmiko ``send_command()`` call.

    Args:
        text (str): The command's output.
        command (str): The command that produced it.
        source (str): Name recorded in the block.
        host (str | None): Host name recorded in the block.

    Returns:
        CommandBlock: One block with ``line == 0``.

    Raises:
        None: Only string cleaning is performed.
    """
    lines = (clean_line(line) for line in text.splitlines())
    kept = [line for line in lines if not NOISE_LINE.match(line)]
    return CommandBlock(command.strip(), _join_output(kept), host, source, 0)


def _hidden(relative: Path) -> bool:
    """Report whether a path inside a searched directory is hidden.

    Args:
        relative (pathlib.Path): Path relative to the searched directory.

    Returns:
        bool: ``True`` when any part starts with a dot (``.git``, ``.swp``).

    Raises:
        None: Only path parts are inspected.
    """
    return any(part.startswith(".") for part in relative.parts)


def iter_capture_files(
    paths: Sequence[str], *, recursive: bool = False, pattern: str = "*"
) -> Iterator[tuple[Path | None, str]]:
    """List the capture files named on a command line, in a stable order.

    A directory contributes the files directly inside it, or every file below
    it with ``recursive``.  Hidden files and directories are skipped.  Sorting
    makes the result independent of the order the file system returns.

    Args:
        paths (Sequence[str]): Files, directories, or ``-`` for standard input.
        recursive (bool): Search directories recursively.
        pattern (str): Glob pattern for files inside directories, such as
            ``*.txt``.

    Returns:
        Iterator[tuple[pathlib.Path | None, str]]: ``(path, source)`` pairs.
            ``path`` is ``None`` for standard input.  ``source`` is the file
            name, the path relative to its directory argument, or ``-``.

    Raises:
        None: Missing paths are yielded and fail when they are read.
    """
    for given in paths:
        if given == "-":
            yield None, "-"
            continue
        path = Path(given)
        if not path.is_dir():
            yield path, path.name
            continue
        found = path.rglob(pattern) if recursive else path.glob(pattern)
        for file in sorted(found):
            relative = file.relative_to(path)
            if file.is_file() and not _hidden(relative):
                yield file, relative.as_posix()


def read_capture(path: Path | None, *, encoding: str = "utf-8") -> str | None:
    """Read one capture file, or standard input.

    Session logs sometimes hold stray bytes from a terminal, so undecodable
    bytes become U+FFFD instead of stopping the whole run.

    Args:
        path (pathlib.Path | None): File to read; ``None`` reads standard input.
        encoding (str): Text encoding of the capture.

    Returns:
        str | None: The text, or ``None`` for a binary file (one containing a
            NUL byte), which cannot be a capture.

    Raises:
        OSError: If the file cannot be read.
        LookupError: If the encoding name is unknown.
    """
    if path is None:
        stream = getattr(sys.stdin, "buffer", None)
        data = stream.read() if stream is not None else sys.stdin.read()
    else:
        data = path.read_bytes()
    if isinstance(data, str):
        return data
    if b"\x00" in data:
        return None
    return data.decode(encoding, errors="replace")


def add_capture_arguments(
    parser: argparse.ArgumentParser, *, paths: bool = True
) -> None:
    """Add the options that say where captures are and how to split them.

    Args:
        parser (argparse.ArgumentParser): Parser to extend.
        paths (bool): Also add the ``PATH ...`` positional argument; commands
            with their own positional arguments (``diff``) leave it out.

    Returns:
        None: Arguments are added in place.

    Raises:
        argparse.ArgumentError: If an option name is already defined.
    """
    if paths:
        parser.add_argument(
            "paths",
            nargs="*",
            default=["-"],
            metavar="PATH",
            help="Capture file, directory of captures, or '-' for standard "
            "input (default).",
        )
    parser.add_argument(
        "-r",
        "--recursive",
        action="store_true",
        help="Read every file below directory arguments, not just the files "
        "directly inside them.",
    )
    parser.add_argument(
        "--glob",
        default="*",
        metavar="PATTERN",
        help="Only read files in directories that match PATTERN, such as "
        "'*.txt' (default: every file).",
    )
    parser.add_argument(
        "--command",
        metavar="COMMAND",
        help="The input holds the output of COMMAND only, with no command line "
        "(for example piped from ssh).",
    )
    parser.add_argument(
        "--host",
        metavar="NAME",
        help="Host name for every command (default: from each prompt).",
    )
    parser.add_argument(
        "--marker",
        metavar="REGEX",
        help="Extra regular expression for lines that start a command, with a "
        "(?P<command>...) group and optionally (?P<host>...).",
    )
    parser.add_argument(
        "--encoding",
        default="utf-8",
        help="Text encoding of the captures (default: utf-8).",
    )


def blocks_from_text(
    text: str, source: str, args: argparse.Namespace
) -> list[CommandBlock]:
    """Split one capture according to the parsed command-line options.

    Args:
        text (str): Capture text.
        source (str): Name of the capture.
        args (argparse.Namespace): Options from :func:`add_capture_arguments`.

    Returns:
        list[CommandBlock]: The capture's blocks.

    Raises:
        ValueError: If ``--marker`` lacks a ``command`` group.
        re.error: If ``--marker`` is not a valid regular expression.
    """
    if args.command:
        return [single_block(text, args.command, source=source, host=args.host)]
    return split_capture(text, source=source, host=args.host, marker=args.marker)


def build_arg_parser() -> argparse.ArgumentParser:
    """Build the parser for ``axlib parse split``.

    Args:
        None: Options are defined by this module.

    Returns:
        argparse.ArgumentParser: Parser for the split command.

    Raises:
        argparse.ArgumentError: If option names conflict.
    """
    parser = argparse.ArgumentParser(
        prog="axlib parse split",
        description="List the commands found in captures, to check how "
        "axlib parse will cut them up.",
    )
    add_capture_arguments(parser)
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print every block, with its output, as a JSON list.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run ``axlib parse split``.

    Args:
        argv (Sequence[str] | None): Arguments excluding the program name.

    Returns:
        int: ``0`` on success, ``2`` when a capture cannot be read or the
            marker is invalid.

    Raises:
        None: Expected errors are printed and turned into the exit status.
    """
    args = build_arg_parser().parse_args(argv)
    blocks: list[CommandBlock] = []
    try:
        for path, source in iter_capture_files(
            args.paths, recursive=args.recursive, pattern=args.glob
        ):
            text = read_capture(path, encoding=args.encoding)
            if text is not None:
                blocks.extend(blocks_from_text(text, source, args))
    except (OSError, LookupError, ValueError, re.error) as exc:
        print(f"axlib parse split: {exc}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps([block.to_dict() for block in blocks], indent=2))
        return 0
    for block in blocks:
        lines = block.output.count("\n")
        print(
            f"{block.source}:{block.line}  {block.host or '-'}  "
            f"{block.command}  ({lines} line{'s' * (lines != 1)})"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
