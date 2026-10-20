# Copyright 2026 Robert Patrick
# SPDX-License-Identifier: Apache-2.0

"""Compare two collections of show-command output record by record.

A text diff of two ``show cdp neighbors`` captures shows every neighbor whose
line moved and every hold time that ticked down, which buries the one cable
that really moved.  This module compares parsed records instead:

1. Commands are paired by device and spelled-out command, so ``sh cdp nei``
   in the "before" capture pairs with ``show cdp neighbors`` after it.  The
   device is the host name from the prompt, or the capture file's name (its
   path inside a searched directory) when there was no prompt.
2. Within a command, records are paired by their key, for example local
   interface plus neighbor name.  A key on both sides with different values
   is *changed* (and the changed fields are listed); a key on one side only
   is *removed* or *added*.  Order never matters.
3. Without a key, or when several records share one, whole records are
   compared, so only additions and removals are reported.

Either side may be a snapshot written by ``axlib parse`` or captures to parse
now: a file, a directory, or ``-``.

Exit status, as for :command:`diff`: 0 when nothing differs, 1 when something
does, 2 when the comparison could not be made or was incomplete because a
command on either side failed to parse.  Skipped commands (no template,
filtered output, a binary file) do not change it.

Dependencies:
    Python standard library to compare snapshots; ``textfsm`` when a side is
    captures that must be parsed first.

CLI example:
    ``axlib parse diff -p cisco_ios -r captures/before/ captures/after/``

Python example::

    from axlib.parse import compare, parse_paths

    before = parse_paths(["captures/before"], platform="cisco_ios", recursive=True)
    after = parse_paths(["captures/after"], platform="cisco_ios", recursive=True)
    for command in compare(before, after).differences:
        print(command.device, command.command, command.status)
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections.abc import Collection, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .capture import add_capture_arguments, read_capture
from .errors import ParseError, SnapshotFormatError
from .normalize import Record, natural_key
from .snapshot import (
    VERBOSE_HELP,
    ParsedCommand,
    Problem,
    Snapshot,
    load_snapshot,
    parse_paths,
    parse_text,
    report_problems,
)
from .templates import TemplateCatalog, add_template_arguments, catalog_from_arguments

_SYMBOLS = {"removed": "-", "added": "+", "changed": "~"}


@dataclass(frozen=True, slots=True)
class RecordChange:
    """One record that differs between the two collections.

    Attributes:
        kind: ``added``, ``removed``, or ``changed``.
        key: The identifying fields and their values (the whole record when
            the command has no key).
        before: The record before, or ``None`` when it was added.
        after: The record after, or ``None`` when it was removed.
    """

    kind: str
    key: dict[str, Any]
    before: Record | None = None
    after: Record | None = None

    @property
    def fields(self) -> dict[str, tuple[Any, Any]]:
        """Return the fields of a changed record that differ.

        Args:
            None: Uses the change's own records.

        Returns:
            dict[str, tuple[Any, Any]]: Field name to ``(before, after)``;
                a field missing on one side shows as ``None``.  Empty for
                added and removed records.

        Raises:
            None: Only dictionary comparison is performed.
        """
        if self.before is None or self.after is None:
            return {}
        names = dict.fromkeys([*self.before, *self.after])
        return {
            name: (self.before.get(name), self.after.get(name))
            for name in names
            if self.before.get(name) != self.after.get(name)
        }

    def to_dict(self) -> dict[str, Any]:
        """Return the change as a JSON-ready dictionary.

        Args:
            None: Uses the change's own fields.

        Returns:
            dict[str, Any]: ``kind``, ``key``, ``before``, ``after``, and
                ``fields`` (changed fields as ``[before, after]`` pairs).

        Raises:
            None: Only dictionary building is performed.
        """
        return {
            "kind": self.kind,
            "key": self.key,
            "before": self.before,
            "after": self.after,
            "fields": {name: list(pair) for name, pair in self.fields.items()},
        }


@dataclass(frozen=True, slots=True)
class CommandDiff:
    """The comparison of one command on one device.

    Attributes:
        device: Host name, or capture file when there was no prompt.
        command: The command spelled out.
        status: ``same``, ``changed``, ``only-before``, or ``only-after``.
        changes: Record changes, for ``changed``.
        records: Number of records, for ``only-before`` and ``only-after``.
    """

    device: str
    command: str
    status: str
    changes: tuple[RecordChange, ...] = ()
    records: int = 0

    def to_dict(self) -> dict[str, Any]:
        """Return the comparison as a JSON-ready dictionary.

        Args:
            None: Uses the comparison's own fields.

        Returns:
            dict[str, Any]: Every field, with changes as dictionaries.

        Raises:
            None: Only dictionary building is performed.
        """
        return {
            "device": self.device,
            "command": self.command,
            "status": self.status,
            "changes": [change.to_dict() for change in self.changes],
            "records": self.records,
        }


@dataclass(frozen=True, slots=True)
class SnapshotDiff:
    """The comparison of two snapshots.

    Attributes:
        commands: Every command compared, sorted by device and command.
        notes: Warnings, such as a command captured twice for one device.
        before_failures: Commands in BEFORE that failed to parse, so they
            could not be compared.
        after_failures: The same for AFTER.
    """

    commands: tuple[CommandDiff, ...]
    notes: tuple[str, ...] = ()
    before_failures: tuple[Problem, ...] = ()
    after_failures: tuple[Problem, ...] = ()

    @property
    def failures(self) -> tuple[Problem, ...]:
        """Return the commands of either side that failed to parse.

        A command that failed on one side is listed as ``only-before`` or
        ``only-after``, and one that failed on both sides is not listed, so
        a comparison with failures is incomplete.

        Args:
            None: Uses the comparison's failures.

        Returns:
            tuple[Problem, ...]: BEFORE's failures, then AFTER's.

        Raises:
            None: Only tuple joining is performed.
        """
        return self.before_failures + self.after_failures

    @property
    def differences(self) -> tuple[CommandDiff, ...]:
        """Return the commands that differ.

        Args:
            None: Uses the comparison's commands.

        Returns:
            tuple[CommandDiff, ...]: Commands whose status is not ``same``.

        Raises:
            None: Only filtering is performed.
        """
        return tuple(item for item in self.commands if item.status != "same")

    def to_dict(self) -> dict[str, Any]:
        """Return the comparison as a JSON-ready dictionary.

        Args:
            None: Uses the comparison's own fields.

        Returns:
            dict[str, Any]: ``commands``; ``failures``, each with the
                ``side`` it came from; ``notes``; and a ``summary`` of counts
                per status plus ``failed``.

        Raises:
            None: Only dictionary building is performed.
        """
        summary = dict.fromkeys(("same", "changed", "only-before", "only-after"), 0)
        for item in self.commands:
            summary[item.status] += 1
        summary["failed"] = len(self.failures)
        failures = [
            {"side": side, **asdict(problem)}
            for side, problems in (
                ("before", self.before_failures),
                ("after", self.after_failures),
            )
            for problem in problems
        ]
        return {
            "summary": summary,
            "commands": [item.to_dict() for item in self.commands],
            "failures": failures,
            "notes": list(self.notes),
        }


def _identity(record: Record, key: Sequence[str]) -> str:
    """Return a hashable identity for a record.

    Args:
        record (Record): The record.
        key (Sequence[str]): Identifying fields; empty means all fields.

    Returns:
        str: JSON text of the key values, or of the whole record.

    Raises:
        TypeError: If a value cannot be converted to JSON.
    """
    if key:
        return json.dumps([record.get(name) for name in key])
    return json.dumps(record, sort_keys=True)


def _key_of(record: Record, key: Sequence[str]) -> dict[str, Any]:
    """Return the identifying fields of a record.

    Args:
        record (Record): The record.
        key (Sequence[str]): Identifying fields; empty means all fields.

    Returns:
        dict[str, Any]: Those fields and their values.

    Raises:
        None: Only dictionary building is performed.
    """
    return {name: record.get(name) for name in key} if key else dict(record)


def compare_records(
    before: ParsedCommand, after: ParsedCommand, *, ignore: Collection[str] = ()
) -> tuple[RecordChange, ...]:
    """Compare the records of one command from two collections.

    Args:
        before (ParsedCommand): The command before.
        after (ParsedCommand): The same command after.
        ignore (Collection[str]): Fields to leave out of the comparison.

    Returns:
        tuple[RecordChange, ...]: Changes in natural key order.

    Raises:
        TypeError: If a value cannot be converted to JSON.
    """
    # A key is trusted only when both sides agree on it; a template change
    # between the collections falls back to comparing whole records.
    key = tuple(name for name in before.key if name not in ignore)
    if before.key != after.key:
        key = ()

    groups: tuple[dict[str, list[Record]], dict[str, list[Record]]] = ({}, {})
    for side, records in zip(groups, (before.records, after.records), strict=True):
        for record in records:
            kept = {name: value for name, value in record.items() if name not in ignore}
            side.setdefault(_identity(kept, key), []).append(kept)

    old, new = groups
    changes: list[RecordChange] = []
    for identity in sorted(old.keys() | new.keys(), key=natural_key):
        olds, news = old.get(identity, []), new.get(identity, [])
        if key and len(olds) == 1 and len(news) == 1:
            if olds[0] != news[0]:
                changes.append(
                    RecordChange("changed", _key_of(olds[0], key), olds[0], news[0])
                )
            continue
        # No key, or a key shared by several records: compare whole records.
        unmatched = list(news)
        for record in olds:
            if record in unmatched:
                unmatched.remove(record)
            else:
                changes.append(
                    RecordChange("removed", _key_of(record, key), before=record)
                )
        changes.extend(
            RecordChange("added", _key_of(record, key), after=record)
            for record in unmatched
        )
    return tuple(changes)


def _by_command(
    snapshot: Snapshot, side: str, notes: list[str]
) -> dict[tuple[str, str], ParsedCommand]:
    """Index a snapshot's results by device and command.

    Args:
        snapshot (Snapshot): The snapshot.
        side (str): ``before`` or ``after``, for notes.
        notes (list[str]): Receives a note for every command captured more
            than once for a device.

    Returns:
        dict[tuple[str, str], ParsedCommand]: The last capture of each
            command per device.

    Raises:
        None: Only dictionary building is performed.
    """
    commands: dict[tuple[str, str], ParsedCommand] = {}
    for result in snapshot.results:
        identity = (result.device, result.template_command)
        if identity in commands:
            notes.append(
                f"{result.device}: {result.template_command!r} appears more than "
                f"once {side}; compared the one at {result.source}:{result.line}"
            )
        commands[identity] = result
    return commands


def compare(
    before: Snapshot, after: Snapshot, *, ignore: Collection[str] = ()
) -> SnapshotDiff:
    """Compare two snapshots command by command and record by record.

    Args:
        before (Snapshot): The earlier collection.
        after (Snapshot): The later collection.
        ignore (Collection[str]): Fields to leave out of the comparison.

    Returns:
        SnapshotDiff: Every command compared, notes, and each side's
            failures (commands that failed to parse and so were not compared).

    Raises:
        TypeError: If a value cannot be converted to JSON.
    """
    notes: list[str] = []
    old = _by_command(before, "before", notes)
    new = _by_command(after, "after", notes)
    commands = []
    for identity in sorted(
        old.keys() | new.keys(), key=lambda item: (natural_key(item[0]), item[1])
    ):
        device, command = identity
        if identity not in new:
            commands.append(
                CommandDiff(
                    device, command, "only-before", records=len(old[identity].records)
                )
            )
        elif identity not in old:
            commands.append(
                CommandDiff(
                    device, command, "only-after", records=len(new[identity].records)
                )
            )
        else:
            changes = compare_records(old[identity], new[identity], ignore=ignore)
            status = "changed" if changes else "same"
            commands.append(CommandDiff(device, command, status, changes))
    return SnapshotDiff(tuple(commands), tuple(notes), before.failures, after.failures)


def _show(value: Any) -> str:
    """Show a value in a diff line.

    Args:
        value (Any): A field value.

    Returns:
        str: Plain text when it has no spaces, else JSON (quoted).

    Raises:
        None: Values from snapshots are JSON-compatible.
    """
    if isinstance(value, str) and value and not any(char.isspace() for char in value):
        return value
    return json.dumps(value)


def _show_fields(fields: dict[str, Any]) -> str:
    """Show identifying fields as ``name=value`` pairs.

    Args:
        fields (dict[str, Any]): Field names and values.

    Returns:
        str: The pairs separated by spaces.

    Raises:
        None: Only string formatting is performed.
    """
    return " ".join(f"{name}={_show(value)}" for name, value in fields.items())


def render_text(diff: SnapshotDiff) -> str:
    """Render the commands that differ, for an operator to read.

    Args:
        diff (SnapshotDiff): The comparison.

    Returns:
        str: ``-`` removed, ``+`` added, and ``~`` changed records under a
            ``device: command`` heading per command, then a summary line that
            also counts the commands that failed to parse.

    Raises:
        None: Only string formatting is performed.
    """
    lines: list[str] = []
    for item in diff.differences:
        lines.append(f"{item.device}: {item.command}")
        if item.status in {"only-before", "only-after"}:
            side = item.status.removeprefix("only-")
            lines.append(f"  only {side} ({item.records} records)")
        for change in item.changes:
            lines.append(f"  {_SYMBOLS[change.kind]} {_show_fields(change.key)}")
            lines.extend(
                f"      {name}: {_show(old)} -> {_show(new)}"
                for name, (old, new) in change.fields.items()
            )
    counts = diff.to_dict()["summary"]
    total = len(diff.commands)
    if not diff.differences and not diff.failures:
        lines.append(f"No differences in {total} commands.")
        return "\n".join(lines) + "\n"
    summary = (
        f"{total} commands compared: {counts['changed']} changed, "
        f"{counts['only-before']} only before, {counts['only-after']} only "
        f"after, {counts['same']} unchanged"
    )
    # "No differences" would be a false all-clear when commands were never
    # compared, so failures always get the full summary and their own count.
    if diff.failures:
        summary += f"; {counts['failed']} failed to parse and could not be compared"
    lines.append(summary)
    return "\n".join(lines) + "\n"


def build_arg_parser() -> argparse.ArgumentParser:
    """Build the parser for ``axlib parse diff``.

    Args:
        None: Options are defined by this module.

    Returns:
        argparse.ArgumentParser: Parser for the diff command.

    Raises:
        argparse.ArgumentError: If option names conflict.
    """
    parser = argparse.ArgumentParser(
        prog="axlib parse diff",
        description="Compare two collections of show-command output record by "
        "record, ignoring the order devices list them in.",
        epilog="BEFORE and AFTER are each a snapshot written by 'axlib parse', a "
        "capture file, a directory of captures, or '-'. Exit status: 0 no "
        "differences, 1 differences, 2 trouble, including a command on either "
        "side that failed to parse.",
    )
    parser.add_argument("before", metavar="BEFORE")
    parser.add_argument("after", metavar="AFTER")
    add_capture_arguments(parser, paths=False)
    add_template_arguments(parser)
    parser.add_argument(
        "--ignore",
        action="append",
        default=[],
        metavar="FIELD",
        help="Leave FIELD out of the comparison. Repeatable.",
    )
    parser.add_argument("--json", action="store_true", help="Print JSON.")
    parser.add_argument("-v", "--verbose", action="store_true", help=VERBOSE_HELP)
    return parser


def _load_side(
    given: str, args: argparse.Namespace, catalog: list[TemplateCatalog]
) -> Snapshot:
    """Load one side: a snapshot file, or captures parsed now.

    Args:
        given (str): Path from the command line, or ``-``.
        args (argparse.Namespace): Parsed options.
        catalog (list[TemplateCatalog]): Empty until a side needs parsing,
            then holds the catalog for the other side to reuse; comparing two
            snapshots needs neither templates nor a platform.

    Returns:
        Snapshot: The side's snapshot.

    Raises:
        SnapshotFormatError: If the snapshot is damaged or not valid JSON;
            the message starts with ``given``.
        ParseError: If settings are invalid, no platform is known, or
            TextFSM is missing.
        OSError: If a file cannot be read.
        LookupError: If the encoding name is unknown.
        ValueError: If a file is binary or ``--marker`` is invalid.
    """
    options = {
        "platform": args.platform,
        "host": args.host,
        "command": args.command,
        "marker": args.marker,
    }
    path = None if given == "-" else Path(given)
    if path is None or not path.is_dir():
        text = read_capture(path, encoding=args.encoding)
        if text is None:
            raise ValueError(f"{given} is a binary file")
        try:
            snapshot = load_snapshot(text)
        except SnapshotFormatError as exc:
            # Name the file: the message alone does not say which side it is.
            raise SnapshotFormatError(f"{given}: {exc}") from exc
        if snapshot is not None:
            return snapshot
        if not catalog:
            catalog.append(catalog_from_arguments(args))
        return parse_text(
            text, catalog=catalog[0], source=path.name if path else "-", **options
        )
    if not catalog:
        catalog.append(catalog_from_arguments(args))
    return parse_paths(
        [given],
        catalog=catalog[0],
        recursive=args.recursive,
        pattern=args.glob,
        encoding=args.encoding,
        **options,
    )


def main(argv: Sequence[str] | None = None) -> int:
    """Run ``axlib parse diff``.

    Args:
        argv (Sequence[str] | None): Arguments excluding the program name.

    Returns:
        int: ``0`` when nothing differs, ``1`` when something does, ``2``
            when the comparison could not be made or a command on either
            side failed to parse.

    Raises:
        None: Expected errors are printed and turned into the exit status.
    """
    args = build_arg_parser().parse_args(argv)
    if args.before == "-" and args.after == "-":
        print("axlib parse diff: only one side can be standard input", file=sys.stderr)
        return 2
    catalog: list[TemplateCatalog] = []
    try:
        before = _load_side(args.before, args, catalog)
        after = _load_side(args.after, args, catalog)
        diff = compare(before, after, ignore=args.ignore)
    except (ParseError, OSError, LookupError, ValueError, re.error) as exc:
        print(f"axlib parse diff: {exc}", file=sys.stderr)
        return 2

    for side, snapshot in (("before", before), ("after", after)):
        report_problems(
            snapshot, program=f"axlib parse diff ({side})", verbose=args.verbose
        )
    for note in diff.notes:
        print(f"axlib parse diff: note: {note}", file=sys.stderr)
    if args.json:
        print(json.dumps(diff.to_dict(), indent=2))
    else:
        sys.stdout.write(render_text(diff))
    # As for diff(1), 2 means trouble: a failure leaves the comparison
    # incomplete, which outranks any differences that were found.
    if diff.failures:
        return 2
    return 1 if diff.differences else 0


if __name__ == "__main__":
    raise SystemExit(main())
