# Copyright 2026 Robert Patrick
# SPDX-License-Identifier: Apache-2.0

r"""Parse captured show-command output with TextFSM into a comparable snapshot.

This module joins the other pieces of :mod:`axlib.parse`.  For each command
in a capture (:mod:`axlib.parse.capture`) it chooses a template
(:mod:`axlib.parse.templates`), runs TextFSM, and makes the records comparable
(:mod:`axlib.parse.normalize`).  The result is a :class:`Snapshot`: every
parsed command with its records, plus the commands that were skipped (no
template, or filtered output) or failed (the template rejected the output).

Saved as JSON, a snapshot taken before a maintenance window can be compared
with one taken after it by ``axlib parse diff``.  Snapshots are sorted by
device and command, and each command's records by their key, so two
snapshots of an unchanged network are identical however the devices ordered
their output.

Templates run through :mod:`textfsm.clitable`, the same engine ntc-templates
and netmiko's ``use_textfsm=True`` use, so a template behaves here as it does
there, including index rows that merge several templates.  Field names are
lower-cased, as ntc-templates does.

Dependencies:
    ``textfsm`` (``uv add 'axlib[textfsm]'``), imported only when a template
    runs, and ``ntc-templates`` for the bundled templates.

CLI example:
    ``axlib parse -p cisco_ios -r captures/before/ -o before.json``

Python example::

    from axlib.parse import parse_output

    records = parse_output(connection.send_command("show cdp neighbors"),
                           "show cdp neighbors", "cisco_ios")
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections.abc import Collection, Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .capture import (
    CommandBlock,
    add_capture_arguments,
    iter_capture_files,
    read_capture,
    single_block,
    split_capture,
)
from .errors import (
    INSTALL_HINT,
    AmbiguousCommandError,
    ParseDependencyError,
    ParseError,
    SnapshotFormatError,
    TemplateNotFoundError,
    TemplateParseError,
)
from .normalize import Record, natural_key, normalize_records
from .templates import (
    TemplateCatalog,
    TemplateEntry,
    add_template_arguments,
    catalog_from_arguments,
)

SNAPSHOT_FORMAT = "axlib-parse"
SNAPSHOT_VERSION = 1
OUTPUT_FORMATS = ("json", "jsonl", "table")
# Help for -v, shared by "axlib parse" and "axlib parse diff".
VERBOSE_HELP = "List each skipped command and why it was skipped."

# A JSON object opens with "{" and then a quoted field name.  A bare "{" is
# not enough: Junos prints "{master:0}" above its prompt in session logs.
_JSON_OBJECT_START = re.compile(r'\s*\{\s*"')


@dataclass(frozen=True, slots=True)
class ParsedCommand:
    """One command's parsed records.

    Attributes:
        command: The command as captured, such as ``sh cdp nei``.
        template_command: The command spelled out, such as
            ``show cdp neighbors``; it pairs commands across snapshots.
        platform: Platform the template was chosen for.
        templates: Template file names.
        origin: Where the template directory was configured, such as
            ``ntc-templates 9.3.0``.
        records: The records, one dictionary per row.
        key: Fields that identify a record; ``()`` means the whole record.
        host: Host name, or ``None`` when the capture had no prompt.
        source: Capture file the output came from.
        line: Line of the command in that file.
    """

    command: str
    template_command: str
    platform: str
    templates: tuple[str, ...]
    origin: str
    records: tuple[Record, ...]
    key: tuple[str, ...] = ()
    host: str | None = None
    source: str = "-"
    line: int = 0

    @property
    def device(self) -> str:
        """Return the name that pairs this command with another snapshot's.

        Args:
            None: Uses the command's own fields.

        Returns:
            str: The host name, or the source file when there was no prompt.

        Raises:
            None: Only attribute access is performed.
        """
        return self.host or self.source

    def to_dict(self) -> dict[str, Any]:
        """Return the command as a JSON-ready dictionary.

        Args:
            None: Uses the command's own fields.

        Returns:
            dict[str, Any]: Every field, with tuples as lists.

        Raises:
            None: Only field copying is performed.
        """
        data = asdict(self)
        data["templates"] = list(self.templates)
        data["records"] = list(self.records)
        data["key"] = list(self.key)
        return data

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> ParsedCommand:
        """Rebuild a command from :meth:`to_dict` output.

        Args:
            data (Mapping[str, Any]): One entry of a snapshot's ``results``.

        Returns:
            ParsedCommand: The command.

        Raises:
            SnapshotFormatError: If a required field is missing or has the
                wrong type.
        """
        try:
            records = tuple(dict(record) for record in data["records"])
            return cls(
                command=str(data["command"]),
                template_command=str(data["template_command"]),
                platform=str(data["platform"]),
                templates=tuple(str(name) for name in data["templates"]),
                origin=str(data["origin"]),
                records=records,
                key=tuple(str(name) for name in data.get("key", ())),
                host=data.get("host"),
                source=str(data.get("source", "-")),
                line=int(data.get("line", 0)),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise SnapshotFormatError(f"invalid snapshot result: {exc}") from exc


@dataclass(frozen=True, slots=True)
class Problem:
    """A command that was not parsed.

    Attributes:
        kind: ``skipped`` (no template, filtered output, or a binary file)
            or ``failed`` (the template rejected the output, or the command
            was ambiguous).
        message: Why.
        command: The command as captured.
        host: Host name, if known.
        source: Capture file.
        line: Line of the command in that file.
    """

    kind: str
    message: str
    command: str = ""
    host: str | None = None
    source: str = "-"
    line: int = 0

    def describe(self) -> str:
        """Return a one-line description for an operator.

        Args:
            None: Uses the problem's own fields.

        Returns:
            str: For example ``sw1.log:7 sw1 'show foo': no template ...``.

        Raises:
            None: Only string formatting is performed.
        """
        where = f"{self.source}:{self.line}" if self.line else self.source
        command = f" {self.command!r}" if self.command else ""
        return f"{where} {self.host or '-'}{command}: {self.message}"


@dataclass(frozen=True, slots=True)
class Snapshot:
    """Every parsed command from one collection, plus what was not parsed.

    Attributes:
        results: Parsed commands, sorted by device, command, and location.
        problems: Commands that were skipped or failed, in capture order.
    """

    results: tuple[ParsedCommand, ...] = ()
    problems: tuple[Problem, ...] = ()

    @property
    def failures(self) -> tuple[Problem, ...]:
        """Return the problems that need an operator's attention.

        Args:
            None: Uses the snapshot's problems.

        Returns:
            tuple[Problem, ...]: Problems of kind ``failed``.

        Raises:
            None: Only filtering is performed.
        """
        return tuple(item for item in self.problems if item.kind == "failed")

    def to_dict(self) -> dict[str, Any]:
        """Return the snapshot as the JSON document ``axlib parse`` writes.

        Args:
            None: Uses the snapshot's own fields.

        Returns:
            dict[str, Any]: ``format``, ``version``, ``results``, ``problems``.

        Raises:
            None: Only field copying is performed.
        """
        return {
            "format": SNAPSHOT_FORMAT,
            "version": SNAPSHOT_VERSION,
            "results": [result.to_dict() for result in self.results],
            "problems": [asdict(problem) for problem in self.problems],
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Snapshot:
        """Rebuild a snapshot from its JSON document.

        Args:
            data (Mapping[str, Any]): Parsed JSON.

        Returns:
            Snapshot: The snapshot.

        Raises:
            SnapshotFormatError: If the document is not a version 1 snapshot.
        """
        if data.get("format") != SNAPSHOT_FORMAT:
            raise SnapshotFormatError("not an axlib parse snapshot")
        if data.get("version") != SNAPSHOT_VERSION:
            raise SnapshotFormatError(
                f"unsupported snapshot version {data.get('version')!r}"
            )
        try:
            problems = tuple(Problem(**item) for item in data.get("problems", ()))
        except TypeError as exc:
            raise SnapshotFormatError(f"invalid snapshot problem: {exc}") from exc
        results = tuple(ParsedCommand.from_dict(item) for item in data["results"])
        return cls(results, problems)


def load_snapshot(text: str) -> Snapshot | None:
    """Read a snapshot from text, if the text is one.

    Args:
        text (str): File contents.

    Returns:
        Snapshot | None: The snapshot, or ``None`` when the text does not
            start like a JSON object (a capture, for example).

    Raises:
        SnapshotFormatError: If the text starts like a JSON object but is
            not valid JSON (a truncated snapshot, for example), is JSON of
            another kind, or is a snapshot of another version or a damaged
            one.
    """
    if not _JSON_OBJECT_START.match(text):
        return None
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        # Read as a capture, a damaged snapshot would quietly become a
        # collection with no commands, and a diff would look plausible.
        raise SnapshotFormatError(f"not valid JSON: {exc}") from exc
    if not isinstance(data, dict) or data.get("format") != SNAPSHOT_FORMAT:
        # No capture starts with '{"', so this is some other JSON file given
        # by mistake; reading it as a capture would compare nothing.
        found = data.get("format") if isinstance(data, dict) else None
        raise SnapshotFormatError(
            f"JSON but not an axlib parse snapshot (format {found!r}, "
            f"expected {SNAPSHOT_FORMAT!r})"
        )
    return Snapshot.from_dict(data)


def run_template(
    entry: TemplateEntry, output: str
) -> tuple[list[Record], tuple[str, ...]]:
    """Parse output with a template, as ntc-templates does.

    Args:
        entry (TemplateEntry): The template (or templates, merged on their
            ``Key`` values).
        output (str): One command's output.

    Returns:
        tuple[list[Record], tuple[str, ...]]: Records with lower-case field
            names, and the fields the template marks ``Key``.

    Raises:
        ParseDependencyError: If TextFSM is not installed.
        TemplateParseError: If a template file is missing or invalid, or the
            template reached an ``Error`` state on the output.
    """
    try:
        from textfsm import clitable, parser, texttable
    except ImportError as exc:
        raise ParseDependencyError(f"TextFSM is not installed: {INSTALL_HINT}") from exc

    table = clitable.CliTable(template_dir=str(entry.directory))
    try:
        table.ParseCmd(output, templates=":".join(entry.templates))
    except (parser.Error, clitable.Error, texttable.Error, OSError) as exc:
        raise TemplateParseError(f"{entry.paths[0]}: {exc}") from exc
    header = [str(name).lower() for name in table.header]
    records = [dict(zip(header, row, strict=True)) for row in table]
    return records, tuple(str(name).lower() for name in table.superkey)


def parse_block(
    block: CommandBlock,
    catalog: TemplateCatalog,
    *,
    platform: str | None = None,
    raw: bool = False,
    ignore: Collection[str] = (),
) -> ParsedCommand:
    """Parse one command block.

    Args:
        block (CommandBlock): The command and its output.
        catalog (TemplateCatalog): Where to find templates.
        platform (str | None): Platform; ``None`` uses the catalog's default.
        raw (bool): Keep records exactly as the template returns them.
        ignore (Collection[str]): Fields to drop (ignored with ``raw``).

    Returns:
        ParsedCommand: The parsed command.

    Raises:
        ParseConfigurationError: If no platform is given or configured.
        ParseDependencyError: If TextFSM is not installed.
        TemplateNotFoundError: If the command has no template.
        AmbiguousCommandError: If an abbreviation fits several commands.
        TemplateParseError: If the template rejects the output.
    """
    match = catalog.resolve(block.command, platform, host=block.host)
    records, template_keys = run_template(match.entry, block.output)
    if raw:
        key = template_keys
    else:
        records, key = normalize_records(
            records,
            command=match.template_command,
            template_keys=template_keys,
            ignore=ignore,
        )
    return ParsedCommand(
        command=match.command,
        template_command=match.template_command,
        platform=match.platform,
        templates=match.entry.templates,
        origin=match.entry.origin,
        records=tuple(records),
        key=key,
        host=block.host,
        source=block.source,
        line=block.line,
    )


def _result_order(result: ParsedCommand) -> tuple[Any, ...]:
    """Sort key for results: device, command, then where they came from.

    Args:
        result (ParsedCommand): A parsed command.

    Returns:
        tuple[Any, ...]: The sort key.

    Raises:
        None: Only text keys are built.
    """
    return (
        natural_key(result.device),
        result.template_command,
        natural_key(result.source),
        result.line,
    )


def parse_blocks(
    blocks: Iterable[CommandBlock],
    catalog: TemplateCatalog,
    *,
    platform: str | None = None,
    raw: bool = False,
    ignore: Collection[str] = (),
) -> Snapshot:
    """Parse many blocks, recording the ones that cannot be parsed.

    Args:
        blocks (Iterable[CommandBlock]): Blocks from one or more captures.
        catalog (TemplateCatalog): Where to find templates.
        platform (str | None): Platform; ``None`` uses the catalog's default.
        raw (bool): Keep records exactly as the templates return them.
        ignore (Collection[str]): Fields to drop.

    Returns:
        Snapshot: Parsed commands in sorted order, plus problems.

    Raises:
        ParseConfigurationError: If no platform is given or configured.
        ParseDependencyError: If TextFSM is not installed.
    """
    results: list[ParsedCommand] = []
    problems: list[Problem] = []
    for block in blocks:
        try:
            results.append(
                parse_block(block, catalog, platform=platform, raw=raw, ignore=ignore)
            )
        except (
            TemplateNotFoundError,
            AmbiguousCommandError,
            TemplateParseError,
        ) as exc:
            kind = "skipped" if isinstance(exc, TemplateNotFoundError) else "failed"
            problems.append(
                Problem(
                    kind, str(exc), block.command, block.host, block.source, block.line
                )
            )
    return Snapshot(tuple(sorted(results, key=_result_order)), tuple(problems))


def parse_text(
    text: str,
    *,
    platform: str | None = None,
    catalog: TemplateCatalog | None = None,
    source: str = "-",
    host: str | None = None,
    command: str | None = None,
    marker: str | re.Pattern[str] | None = None,
    raw: bool = False,
    ignore: Collection[str] = (),
) -> Snapshot:
    """Split a capture into commands and parse each one.

    Args:
        text (str): A capture, or one command's output when ``command`` is
            given.
        platform (str | None): Platform; ``None`` uses the configured one.
        catalog (TemplateCatalog | None): Templates; ``None`` loads the
            configured directories and ntc-templates.
        source (str): Name recorded for the capture.
        host (str | None): Host name for every command.
        command (str | None): Treat all of ``text`` as this command's output.
        marker (str | re.Pattern[str] | None): Extra command-line pattern; see
            :func:`axlib.parse.capture.split_capture`.
        raw (bool): Keep records exactly as the templates return them.
        ignore (Collection[str]): Fields to drop.

    Returns:
        Snapshot: The parsed commands and problems.

    Raises:
        ParseConfigurationError: If settings are invalid or no platform is
            known.
        ParseDependencyError: If TextFSM or every template is missing.
        ValueError: If ``marker`` lacks a ``command`` group.
    """
    catalog = catalog or TemplateCatalog.default(platform=platform)
    if command:
        blocks = [single_block(text, command, source=source, host=host)]
    else:
        blocks = split_capture(text, source=source, host=host, marker=marker)
    return parse_blocks(blocks, catalog, platform=platform, raw=raw, ignore=ignore)


def parse_paths(
    paths: Sequence[str],
    *,
    platform: str | None = None,
    catalog: TemplateCatalog | None = None,
    recursive: bool = False,
    pattern: str = "*",
    host: str | None = None,
    command: str | None = None,
    marker: str | re.Pattern[str] | None = None,
    raw: bool = False,
    ignore: Collection[str] = (),
    encoding: str = "utf-8",
) -> Snapshot:
    """Parse every capture in files and directories.

    Args:
        paths (Sequence[str]): Files, directories, or ``-`` for standard input.
        platform (str | None): Platform; ``None`` uses the configured one.
        catalog (TemplateCatalog | None): Templates; ``None`` loads the
            configured directories and ntc-templates.
        recursive (bool): Search directories recursively.
        pattern (str): Glob pattern for files inside directories.
        host (str | None): Host name for every command.
        command (str | None): Every file holds only this command's output.
        marker (str | re.Pattern[str] | None): Extra command-line pattern.
        raw (bool): Keep records exactly as the templates return them.
        ignore (Collection[str]): Fields to drop.
        encoding (str): Text encoding of the captures.

    Returns:
        Snapshot: Every capture's parsed commands, sorted, and the problems,
            including binary files that were skipped.

    Raises:
        OSError: If a file or directory cannot be read.
        LookupError: If the encoding name is unknown.
        ParseConfigurationError: If settings are invalid or no platform is
            known.
        ParseDependencyError: If TextFSM or every template is missing.
        ValueError: If ``marker`` lacks a ``command`` group.
    """
    catalog = catalog or TemplateCatalog.default(platform=platform)
    results: list[ParsedCommand] = []
    problems: list[Problem] = []
    for path, source in iter_capture_files(paths, recursive=recursive, pattern=pattern):
        text = read_capture(path, encoding=encoding)
        if text is None:
            problems.append(Problem("skipped", "binary file", source=source))
            continue
        snapshot = parse_text(
            text,
            platform=platform,
            catalog=catalog,
            source=source,
            host=host,
            command=command,
            marker=marker,
            raw=raw,
            ignore=ignore,
        )
        results.extend(snapshot.results)
        problems.extend(snapshot.problems)
    return Snapshot(tuple(sorted(results, key=_result_order)), tuple(problems))


def parse_output(
    output: str,
    command: str,
    platform: str | None = None,
    *,
    catalog: TemplateCatalog | None = None,
    host: str | None = None,
    raw: bool = False,
    ignore: Collection[str] = (),
) -> list[Record]:
    """Parse one command's output into records.

    This is the one-call form for scripts, like ntc-templates'
    ``parse_output()``, with three differences: operator template
    directories are searched first, abbreviated commands are accepted, and
    records are normalized (``raw=True`` turns that off).

    Args:
        output (str): The command's output.
        command (str): The command, possibly abbreviated.
        platform (str | None): Platform; ``None`` uses the configured one.
        catalog (TemplateCatalog | None): Templates; ``None`` loads the
            configured directories and ntc-templates.
        host (str | None): Host name, for index rows that name hosts.
        raw (bool): Return records exactly as the template does.
        ignore (Collection[str]): Fields to drop.

    Returns:
        list[Record]: One dictionary per row.

    Raises:
        ParseConfigurationError: If settings are invalid or no platform is
            known.
        ParseDependencyError: If TextFSM or every template is missing.
        TemplateNotFoundError: If the command has no template.
        AmbiguousCommandError: If an abbreviation fits several commands.
        TemplateParseError: If the template rejects the output.
    """
    catalog = catalog or TemplateCatalog.default(platform=platform)
    block = single_block(output, command, host=host)
    parsed = parse_block(block, catalog, platform=platform, raw=raw, ignore=ignore)
    return list(parsed.records)


def _format_value(value: Any) -> str:
    """Show a field value in one table cell.

    Args:
        value (Any): Text, or a list of text for ``List`` values.

    Returns:
        str: The text, with list items joined by ``", "``.

    Raises:
        None: Other values are converted with :class:`str`.
    """
    if isinstance(value, list):
        return ", ".join(str(item) for item in value)
    return str(value)


def render_table(snapshot: Snapshot) -> str:
    """Render a snapshot as aligned text tables, one per command.

    The text has no file names or line numbers in it, so two tables of an
    unchanged network are identical and ``diff -u`` shows only real changes.

    Args:
        snapshot (Snapshot): The snapshot.

    Returns:
        str: One ``## device: command`` heading and table per command.

    Raises:
        None: Only string formatting is performed.
    """
    sections = []
    for result in snapshot.results:
        lines = [f"## {result.device}: {result.template_command}"]
        columns = list(dict.fromkeys(name for row in result.records for name in row))
        rows = [
            [_format_value(row.get(name, "")) for name in columns]
            for row in result.records
        ]
        widths = [
            max([len(name), *(len(row[index]) for row in rows)])
            for index, name in enumerate(columns)
        ]
        if rows:
            for cells in [columns, *rows]:
                padded = (
                    cell.ljust(width) for cell, width in zip(cells, widths, strict=True)
                )
                lines.append("  ".join(padded).rstrip())
        else:
            lines.append("(no records)")
        sections.append("\n".join(lines))
    return "\n\n".join(sections) + "\n" if sections else ""


def render(snapshot: Snapshot, output_format: str = "json") -> str:
    """Render a snapshot in one of :data:`OUTPUT_FORMATS`.

    Args:
        snapshot (Snapshot): The snapshot.
        output_format (str): ``json`` (a snapshot ``axlib parse diff``
            reads), ``jsonl`` (one record per line, for ``jq`` and ``grep``),
            or ``table`` (aligned text for reading and ``diff -u``).

    Returns:
        str: The rendered text, ending with a newline when not empty.

    Raises:
        ValueError: If the format is unknown.
    """
    if output_format == "json":
        return json.dumps(snapshot.to_dict(), indent=2) + "\n"
    if output_format == "jsonl":
        lines = [
            json.dumps(
                {
                    "device": result.device,
                    "command": result.template_command,
                    "source": result.source,
                    "line": result.line,
                    "record": record,
                }
            )
            for result in snapshot.results
            for record in result.records
        ]
        return "".join(f"{line}\n" for line in lines)
    if output_format == "table":
        return render_table(snapshot)
    raise ValueError(f"unknown output format: {output_format}")


def report_problems(snapshot: Snapshot, *, program: str, verbose: bool = False) -> None:
    """Print failures, and skipped commands when asked, to standard error.

    Args:
        snapshot (Snapshot): The snapshot.
        program (str): Command name for the messages.
        verbose (bool): List every skipped command instead of a count.

    Returns:
        None: Messages go to standard error.

    Raises:
        None: Only printing is performed.
    """
    skipped = [item for item in snapshot.problems if item.kind == "skipped"]
    for item in snapshot.failures:
        print(f"{program}: failed: {item.describe()}", file=sys.stderr)
    if verbose:
        for item in skipped:
            print(f"{program}: skipped: {item.describe()}", file=sys.stderr)
    elif skipped:
        # Commands are skipped for several reasons (no template, filtered
        # output, a binary file), so the count names none of them.
        print(
            f"{program}: skipped {len(skipped)} command(s) (-v lists them and why)",
            file=sys.stderr,
        )


def build_arg_parser() -> argparse.ArgumentParser:
    """Build the parser for ``axlib parse``.

    Args:
        None: Options are defined by this module.

    Returns:
        argparse.ArgumentParser: Parser for the parse command.

    Raises:
        argparse.ArgumentError: If option names conflict.
    """
    parser = argparse.ArgumentParser(
        prog="axlib parse",
        description="Parse show-command output with TextFSM templates into "
        "sorted records.",
        epilog="More commands: 'axlib parse diff BEFORE AFTER' compares two "
        "collections, 'axlib parse templates [COMMAND]' shows which template "
        "parses a command, and 'axlib parse split [PATH]' lists the commands "
        "found in captures. Add --help after one for its options.",
    )
    add_capture_arguments(parser)
    add_template_arguments(parser)
    parser.add_argument(
        "-f",
        "--format",
        choices=OUTPUT_FORMATS,
        default="json",
        help="json: a snapshot for 'axlib parse diff' (default); jsonl: one "
        "record per line; table: aligned text.",
    )
    parser.add_argument(
        "-o",
        "--output",
        default="-",
        metavar="FILE",
        help="Write to FILE instead of standard output.",
    )
    parser.add_argument(
        "--ignore",
        action="append",
        default=[],
        metavar="FIELD",
        help="Drop FIELD from every record. Repeatable.",
    )
    parser.add_argument(
        "--raw",
        action="store_true",
        help="Records exactly as the templates return them: every field, "
        "interface names as printed, in the device's order.",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help=VERBOSE_HELP,
    )
    return parser


def write_output(destination: str, text: str) -> None:
    """Write text to a file or standard output.

    Args:
        destination (str): File path, or ``-`` for standard output.
        text (str): Text to write.

    Returns:
        None: The text is written.

    Raises:
        OSError: If the file cannot be written.
    """
    if destination == "-":
        sys.stdout.write(text)
    else:
        Path(destination).write_text(text, encoding="utf-8")


def main(argv: Sequence[str] | None = None) -> int:
    """Run ``axlib parse``.

    Args:
        argv (Sequence[str] | None): Arguments excluding the program name.

    Returns:
        int: ``0`` when output was parsed, ``1`` when a command failed or
            nothing was parsed, ``2`` for errors such as a missing file,
            missing TextFSM, or no platform.

    Raises:
        None: Expected errors are printed and turned into the exit status.
    """
    args = build_arg_parser().parse_args(argv)
    try:
        catalog = catalog_from_arguments(args)
        catalog.platform_for(None)  # Fail before reading anything.
        snapshot = parse_paths(
            args.paths,
            catalog=catalog,
            recursive=args.recursive,
            pattern=args.glob,
            host=args.host,
            command=args.command,
            marker=args.marker,
            raw=args.raw,
            ignore=args.ignore,
            encoding=args.encoding,
        )
        write_output(args.output, render(snapshot, args.format))
    except (ParseError, OSError, LookupError, ValueError, re.error) as exc:
        print(f"axlib parse: {exc}", file=sys.stderr)
        return 2

    report_problems(snapshot, program="axlib parse", verbose=args.verbose)
    if snapshot.failures:
        return 1
    if not snapshot.results:
        if not snapshot.problems:
            print(
                "axlib parse: no commands found; use --command when the input "
                "is output only",
                file=sys.stderr,
            )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
