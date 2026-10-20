# Copyright 2026 Robert Patrick
# SPDX-License-Identifier: Apache-2.0

"""Tests for comparing two collections record by record."""

from __future__ import annotations

import dataclasses
import io
import json
import sys
from pathlib import Path

import pytest

from axlib.__main__ import main as axlib_main
from axlib.parse import TemplateCatalog, compare, parse_paths
from axlib.parse.diff import RecordChange, compare_records, main, render_text
from axlib.parse.settings import TemplateSource
from axlib.parse.snapshot import ParsedCommand, Problem, Snapshot, render

pytestmark = pytest.mark.usefixtures("clean_parse_environment")

EXAMPLES = Path(__file__).resolve().parents[1] / "examples" / "parse"
OPTIONS = ["-p", "cisco_ios", "-t", str(EXAMPLES / "templates")]


@pytest.fixture(scope="module")
def snapshots() -> tuple[Snapshot, Snapshot]:
    """The example before and after collections, parsed."""
    source = TemplateSource(EXAMPLES / "templates", "examples")
    catalog = TemplateCatalog.load([source], platform="cisco_ios", environ={})
    return (
        parse_paths([str(EXAMPLES / "before")], catalog=catalog),
        parse_paths([str(EXAMPLES / "after")], catalog=catalog),
    )


def command(records: list[dict[str, str]], key: tuple[str, ...] = ()) -> ParsedCommand:
    """A parsed command on sw1 with the given records."""
    return ParsedCommand(
        "show x",
        "show x",
        "cisco_ios",
        ("t.textfsm",),
        "test",
        tuple(records),
        key,
        "sw1",
    )


def test_example_reports_only_real_changes(
    snapshots: tuple[Snapshot, Snapshot],
) -> None:
    diff = compare(*snapshots)

    changed = {(item.device, item.command): item for item in diff.differences}
    assert sorted(changed) == [
        ("sw1", "show cdp neighbors"),
        ("sw1", "show cdp neighbors detail"),
        ("sw1", "show interfaces status"),
        ("sw1", "show ip interface brief"),
        ("sw1", "show ip pim neighbor"),
        ("sw1", "show lldp neighbors"),
        ("sw1", "show lldp neighbors detail"),
    ]
    # sw2 was only reordered and its hold times changed: nothing to report.
    assert [
        (item.device, item.status) for item in diff.commands if item.device == "sw2.txt"
    ] == [
        ("sw2.txt", "same"),
        ("sw2.txt", "same"),
    ]
    cdp = changed["sw1", "show cdp neighbors"].changes
    assert [(change.kind, change.key) for change in cdp] == [
        (
            "removed",
            {"local_interface": "GigabitEthernet1/0/5", "neighbor_name": "ap-lobby-01"},
        ),
        (
            "changed",
            {
                "local_interface": "GigabitEthernet1/0/48",
                "neighbor_name": "core-rtr-01.example.net",
            },
        ),
    ]
    assert cdp[1].fields == {
        "neighbor_interface": ("GigabitEthernet0/0/1", "GigabitEthernet0/0/2")
    }
    pim = changed["sw1", "show ip pim neighbor"].changes
    assert [(change.key, change.fields) for change in pim] == [
        (
            {"neighbor": "10.20.0.2", "interface": "Vlan20"},
            {"mode": ("DR S P G", "S P G")},
        )
    ]


def test_render_text_lists_changes_then_a_summary(
    snapshots: tuple[Snapshot, Snapshot],
) -> None:
    text = render_text(compare(*snapshots))

    assert text.splitlines()[:4] == [
        "sw1: show cdp neighbors",
        "  - local_interface=GigabitEthernet1/0/5 neighbor_name=ap-lobby-01",
        (
            "  ~ local_interface=GigabitEthernet1/0/48 "
            "neighbor_name=core-rtr-01.example.net"
        ),
        "      neighbor_interface: GigabitEthernet0/0/1 -> GigabitEthernet0/0/2",
    ]
    assert '      mode: "DR S P G" -> "S P G"' in text
    assert text.endswith(
        "9 commands compared: 7 changed, 0 only before, 0 only after, 2 unchanged\n"
    )
    assert render_text(compare(snapshots[0], snapshots[0])) == (
        "No differences in 9 commands.\n"
    )


def test_ignore_hides_a_field(snapshots: tuple[Snapshot, Snapshot]) -> None:
    diff = compare(*snapshots, ignore={"mode"})

    statuses = {
        item.command: item.status for item in diff.commands if item.device == "sw1"
    }
    assert statuses["show ip pim neighbor"] == "same"


def test_records_without_a_key_are_added_or_removed() -> None:
    before = command([{"a": "1"}, {"a": "2"}, {"a": "2"}])
    after = command([{"a": "2"}, {"a": "3"}])

    changes = compare_records(before, after)

    assert [(change.kind, change.key) for change in changes] == [
        ("removed", {"a": "1"}),
        ("removed", {"a": "2"}),
        ("added", {"a": "3"}),
    ]


def test_shared_key_values_fall_back_to_whole_records() -> None:
    before = command([{"port": "1", "mac": "a"}, {"port": "1", "mac": "b"}], ("port",))
    after = command([{"port": "1", "mac": "b"}, {"port": "1", "mac": "c"}], ("port",))

    changes = compare_records(before, after)

    assert [(change.kind, change.before, change.after) for change in changes] == [
        ("removed", {"port": "1", "mac": "a"}, None),
        ("added", None, {"port": "1", "mac": "c"}),
    ]
    assert changes[0].fields == {}


def test_different_keys_compare_whole_records() -> None:
    before = command([{"port": "1", "state": "up"}], ("port",))
    after = command([{"port": "1", "state": "down"}], ())

    kinds = [change.kind for change in compare_records(before, after)]

    # Whole records are listed in their natural order: "down" before "up".
    assert kinds == ["added", "removed"]


def test_record_change_lists_fields_missing_on_one_side() -> None:
    change = RecordChange(
        "changed", {"k": "1"}, {"k": "1", "a": "x"}, {"k": "1", "b": "y"}
    )

    assert change.fields == {"a": ("x", None), "b": (None, "y")}
    assert change.to_dict()["fields"] == {"a": ["x", None], "b": [None, "y"]}


def test_commands_on_one_side_and_duplicates() -> None:
    first = command([{"a": "1"}])
    again = dataclasses.replace(first, line=9)
    before = Snapshot((first, again))
    after = Snapshot(
        (
            ParsedCommand(
                "show y",
                "show y",
                "cisco_ios",
                ("y",),
                "test",
                ({"b": "2"},),
                (),
                "sw1",
            ),
        )
    )

    diff = compare(before, after)

    assert [(item.command, item.status, item.records) for item in diff.commands] == [
        ("show x", "only-before", 1),
        ("show y", "only-after", 1),
    ]
    assert diff.notes == (
        "sw1: 'show x' appears more than once before; compared the one at -:9",
    )
    assert "  only before (1 records)" in render_text(diff)
    assert diff.to_dict()["summary"] == {
        "same": 0,
        "changed": 0,
        "only-before": 1,
        "only-after": 1,
        "failed": 0,
    }


def test_diff_command_compares_directories(capsys: pytest.CaptureFixture[str]) -> None:
    status = main([*OPTIONS, str(EXAMPLES / "before"), str(EXAMPLES / "after")])
    output = capsys.readouterr()

    assert status == 1
    assert "~ port=GigabitEthernet1/0/5" in output.out
    assert "axlib parse diff (before): skipped 2 command(s)" in output.err


def test_diff_command_compares_saved_snapshots_without_templates(
    tmp_path: Path,
    snapshots: tuple[Snapshot, Snapshot],
    capsys: pytest.CaptureFixture[str],
) -> None:
    before, after = tmp_path / "before.json", tmp_path / "after.json"
    before.write_text(render(snapshots[0]), encoding="utf-8")
    after.write_text(render(snapshots[1]), encoding="utf-8")

    # No platform and no templates: snapshots need neither.
    status = main(["--json", str(before), str(after)])
    report = json.loads(capsys.readouterr().out)
    same = main([str(before), str(before)])

    assert status == 1
    assert report["summary"]["changed"] == 7
    assert same == 0
    assert capsys.readouterr().out == "No differences in 9 commands.\n"


def test_diff_command_mixes_a_snapshot_and_standard_input(
    tmp_path: Path,
    snapshots: tuple[Snapshot, Snapshot],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    before = tmp_path / "before.json"
    before.write_text(render(snapshots[0]), encoding="utf-8")
    after = (EXAMPLES / "after" / "sw1.log").read_text(encoding="utf-8")
    monkeypatch.setattr(sys, "stdin", io.StringIO(after))

    status = axlib_main(["parse", "diff", *OPTIONS, str(before), "-"])
    output = capsys.readouterr().out

    # sw2.txt is only in the before snapshot.
    assert status == 1
    assert "sw2.txt: show cdp neighbors\n  only before (3 records)" in output


@pytest.mark.parametrize(
    ("args", "message"),
    [
        (["-", "-"], "only one side"),
        (["/nonexistent/a", "/nonexistent/b"], "No such file"),
        ([str(EXAMPLES / "before"), str(EXAMPLES / "after")], "no platform given"),
    ],
)
def test_diff_command_trouble(
    args: list[str], message: str, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(args) == 2
    assert message in capsys.readouterr().err


def test_diff_command_rejects_binary_files(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    binary = tmp_path / "x.bin"
    binary.write_bytes(b"\x00")

    assert main([str(binary), str(binary)]) == 2
    assert "is a binary file" in capsys.readouterr().err


def write_failing_capture(path: Path, line: str = "not a status line") -> Path:
    """Write a capture whose one command the ntc template rejects."""
    path.write_text(f"sw1#show int status\n{line}\n", encoding="utf-8")
    return path


def test_diff_command_exits_2_when_every_command_failed(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    before = write_failing_capture(tmp_path / "before.log")
    after = write_failing_capture(tmp_path / "after.log", "also not a status line")

    status = main(["-p", "cisco_ios", str(before), str(after)])
    output = capsys.readouterr()

    # Nothing was compared, so "No differences" would be a false all-clear.
    assert status == 2
    assert output.out == (
        "0 commands compared: 0 changed, 0 only before, 0 only after, "
        "0 unchanged; 2 failed to parse and could not be compared\n"
    )
    assert "axlib parse diff (before): failed: before.log:1 sw1" in output.err
    assert "axlib parse diff (after): failed: after.log:1 sw1" in output.err


def test_diff_command_reports_differences_and_failures(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    after = tmp_path / "after"
    after.mkdir()
    for name in ("sw1.log", "sw2.txt"):
        (after / name).write_text(
            (EXAMPLES / "after" / name).read_text(encoding="utf-8"), encoding="utf-8"
        )
    write_failing_capture(after / "sw3.log")

    status = main([*OPTIONS, str(EXAMPLES / "before"), str(after)])
    output = capsys.readouterr()

    assert status == 2
    assert "~ port=GigabitEthernet1/0/5" in output.out
    assert output.out.endswith(
        "9 commands compared: 7 changed, 0 only before, 0 only after, "
        "2 unchanged; 1 failed to parse and could not be compared\n"
    )
    assert "axlib parse diff (after): failed: sw3.log:1 sw1" in output.err


def test_diff_command_json_lists_failures_by_side(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    good = tmp_path / "good.log"
    good.write_text("sw1#show ip pim neighbor\n", encoding="utf-8")
    bad = write_failing_capture(tmp_path / "bad.log")

    status = main([*OPTIONS, "--json", str(good), str(bad)])
    report = json.loads(capsys.readouterr().out)

    assert status == 2
    assert report["summary"] == {
        "same": 0,
        "changed": 0,
        "only-before": 1,
        "only-after": 0,
        "failed": 1,
    }
    assert [(item["side"], item["source"]) for item in report["failures"]] == [
        ("after", "bad.log")
    ]


def test_skipped_commands_do_not_change_the_status(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    capture = tmp_path / "skipped.log"
    capture.write_text(
        "sw1#terminal length 0\nsw1#show running-config | include hostname\n"
        "hostname sw1\n",
        encoding="utf-8",
    )

    status = main([*OPTIONS, str(capture), str(capture)])
    output = capsys.readouterr()

    assert status == 0
    assert output.out == "No differences in 0 commands.\n"
    assert "skipped 2 command(s)" in output.err


def test_compare_carries_each_side_failures() -> None:
    failed = Problem("failed", "State Error raised", "show x", "sw1", "sw1.log", 3)
    skipped = Problem("skipped", "binary file", source="image.png")

    diff = compare(Snapshot(problems=(failed, skipped)), Snapshot(problems=(failed,)))

    assert diff.before_failures == (failed,)
    assert diff.after_failures == (failed,)
    assert diff.failures == (failed, failed)
    assert compare(Snapshot(problems=(skipped,)), Snapshot()).failures == ()


def test_diff_command_rejects_a_truncated_snapshot(
    tmp_path: Path,
    snapshots: tuple[Snapshot, Snapshot],
    capsys: pytest.CaptureFixture[str],
) -> None:
    truncated = tmp_path / "trunc.json"
    truncated.write_text(render(snapshots[0])[:3000], encoding="utf-8")

    status = main([*OPTIONS, str(truncated), str(EXAMPLES / "before")])
    output = capsys.readouterr()

    # Read as a capture, it would have looked like nine commands "only after".
    assert status == 2
    assert output.out == ""
    assert f"axlib parse diff: {truncated}: not valid JSON: " in output.err
