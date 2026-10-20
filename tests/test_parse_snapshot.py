# Copyright 2026 Robert Patrick
# SPDX-License-Identifier: Apache-2.0

"""Tests for parsing captures into snapshots, its API, and ``axlib parse``."""

from __future__ import annotations

import io
import json
import sys
from pathlib import Path

import pytest

from axlib.__main__ import main as axlib_main
from axlib.parse import TemplateCatalog, parse_output, parse_paths, parse_text
from axlib.parse.errors import (
    ParseDependencyError,
    SnapshotFormatError,
    TemplateNotFoundError,
    TemplateParseError,
)
from axlib.parse.settings import TemplateSource
from axlib.parse.snapshot import Snapshot, load_snapshot, main, render

pytestmark = pytest.mark.usefixtures("clean_parse_environment")

EXAMPLES = Path(__file__).resolve().parents[1] / "examples" / "parse"
CDP_OUTPUT = (EXAMPLES / "before" / "sw2.txt").read_text(encoding="utf-8")
CDP_OUTPUT = CDP_OUTPUT.split("show cdp neighbors\n")[1].split("show interfaces")[0]


@pytest.fixture(scope="module")
def catalog() -> TemplateCatalog:
    """The example operator templates, then ntc-templates."""
    source = TemplateSource(EXAMPLES / "templates", "examples")
    return TemplateCatalog.load([source], platform="cisco_ios", environ={})


def test_example_capture_parses_every_templated_command(
    catalog: TemplateCatalog,
) -> None:
    snapshot = parse_paths([str(EXAMPLES / "before" / "sw1.log")], catalog=catalog)

    assert [result.template_command for result in snapshot.results] == [
        "show cdp neighbors",
        "show cdp neighbors detail",
        "show interfaces status",
        "show ip interface brief",
        "show ip pim neighbor",
        "show lldp neighbors",
        "show lldp neighbors detail",
    ]
    assert [(item.kind, item.command) for item in snapshot.problems] == [
        ("skipped", "terminal length 0"),
        ("skipped", "show running-config | include hostname"),
    ]
    cdp = snapshot.results[0]
    assert (cdp.host, cdp.source, cdp.line, cdp.platform) == (
        "sw1",
        "sw1.log",
        2,
        "cisco_ios",
    )
    assert cdp.key == ("local_interface", "neighbor_name")
    assert [record["local_interface"] for record in cdp.records] == [
        "GigabitEthernet1/0/1",
        "GigabitEthernet1/0/5",
        "GigabitEthernet1/0/10",
        "GigabitEthernet1/0/48",
    ]
    pim = snapshot.results[4]
    assert pim.key == ("neighbor", "interface")
    assert pim.origin == "examples"


def test_parse_output_matches_raw_except_order_and_names(
    catalog: TemplateCatalog,
) -> None:
    normalized = parse_output(CDP_OUTPUT, "sh cdp nei", catalog=catalog)
    raw = parse_output(CDP_OUTPUT, "show cdp neighbors", catalog=catalog, raw=True)

    assert [record["neighbor_name"] for record in raw] == [
        "dist-sw-02.example.net",
        "sw1",
        "printer-3f",
    ]
    assert raw[0]["local_interface"] == "Gig 1/0/1"
    assert [record["neighbor_name"] for record in normalized] == [
        "dist-sw-02.example.net",
        "printer-3f",
        "sw1",
    ]
    assert normalized[0]["local_interface"] == "GigabitEthernet1/0/1"


def test_parse_output_raises_for_unknown_commands(catalog: TemplateCatalog) -> None:
    with pytest.raises(TemplateNotFoundError):
        parse_output("x\n", "show nothing", catalog=catalog)


def test_template_error_is_a_failure(catalog: TemplateCatalog) -> None:
    text = "sw1#show interfaces status\nPort  Name  Status\nnot a status line\n"

    snapshot = parse_text(text, catalog=catalog)

    assert snapshot.results == ()
    assert [item.kind for item in snapshot.failures] == ["failed"]
    assert "State Error raised" in snapshot.failures[0].message
    assert (
        snapshot.failures[0].describe().startswith("-:1 sw1 'show interfaces status'")
    )


def test_broken_template_file_is_a_failure(tmp_path: Path) -> None:
    (tmp_path / "cisco_ios_show_x.textfsm").write_text(
        "not a template\n", encoding="utf-8"
    )
    catalog = TemplateCatalog.load(
        [TemplateSource(tmp_path, "test")], platform="cisco_ios", include_bundled=False
    )

    with pytest.raises(TemplateParseError, match=r"cisco_ios_show_x\.textfsm"):
        parse_output("x\n", "show x", catalog=catalog)


def test_missing_textfsm_names_the_install_command(
    catalog: TemplateCatalog, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setitem(sys.modules, "textfsm", None)

    with pytest.raises(ParseDependencyError, match=r"uv add 'axlib\[textfsm\]'"):
        parse_output(CDP_OUTPUT, "show cdp neighbors", catalog=catalog)


def test_binary_files_are_skipped(tmp_path: Path, catalog: TemplateCatalog) -> None:
    (tmp_path / "image.png").write_bytes(b"\x89PNG\x00")

    snapshot = parse_paths([str(tmp_path)], catalog=catalog)

    assert [(item.kind, item.message, item.source) for item in snapshot.problems] == [
        ("skipped", "binary file", "image.png")
    ]


def test_snapshot_json_round_trip(catalog: TemplateCatalog) -> None:
    snapshot = parse_paths([str(EXAMPLES / "before")], catalog=catalog)

    text = render(snapshot, "json")

    assert load_snapshot(text) == snapshot
    assert load_snapshot("sw1#show clock\n") is None
    assert load_snapshot("{not json") is None
    assert load_snapshot('{"format": "something else"}') is None
    with pytest.raises(SnapshotFormatError, match="version"):
        load_snapshot('{"format": "axlib-parse", "version": 99, "results": []}')
    with pytest.raises(SnapshotFormatError, match="invalid snapshot result"):
        load_snapshot('{"format": "axlib-parse", "version": 1, "results": [{}]}')
    with pytest.raises(SnapshotFormatError, match="invalid snapshot problem"):
        Snapshot.from_dict(
            {"format": "axlib-parse", "version": 1, "results": [], "problems": [{}]}
        )


def test_table_and_jsonl_formats(catalog: TemplateCatalog) -> None:
    snapshot = parse_text(
        CDP_OUTPUT, command="show cdp neighbors", host="sw2", catalog=catalog
    )
    empty = parse_text("sw1#show ip pim neighbor\n", catalog=catalog)

    table = render(snapshot, "table")
    lines = [json.loads(line) for line in render(snapshot, "jsonl").splitlines()]

    assert table.splitlines()[:3] == [
        "## sw2: show cdp neighbors",
        (
            "neighbor_name           local_interface        capabilities  platform   "
            "neighbor_interface"
        ),
        (
            "dist-sw-02.example.net  GigabitEthernet1/0/1   R S I         C9300-48P  "
            "GigabitEthernet1/0/50"
        ),
    ]
    assert render(empty, "table") == "## sw1: show ip pim neighbor\n(no records)\n"
    assert render(Snapshot(), "table") == ""
    assert lines[0]["device"] == "sw2"
    assert lines[0]["record"]["neighbor_name"] == "dist-sw-02.example.net"
    with pytest.raises(ValueError, match="unknown output format"):
        render(snapshot, "xml")


def test_parse_command_writes_a_snapshot(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    output = tmp_path / "before.json"
    args = ["-p", "cisco_ios", "-t", str(EXAMPLES / "templates"), "-r"]

    status = main([*args, str(EXAMPLES / "before"), "-o", str(output)])
    summary = capsys.readouterr().err
    verbose_status = main([*args, "-v", "-f", "table", str(EXAMPLES / "before")])
    verbose = capsys.readouterr()
    saved = load_snapshot(output.read_text(encoding="utf-8"))

    assert status == 0
    assert "skipped 2 command(s) with no template (-v lists them)" in summary
    assert saved is not None
    assert len(saved.results) == 9
    assert verbose_status == 0
    assert "skipped: sw1.log:1 sw1 'terminal length 0'" in verbose.err
    assert "## sw2.txt: show interfaces status" in verbose.out


def test_parse_command_reads_standard_input(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(sys, "stdin", io.StringIO(CDP_OUTPUT))
    monkeypatch.setenv("AXLIB_TEXTFSM_PLATFORM", "cisco_ios")

    status = axlib_main(["parse", "--command", "show cdp neighbors", "-f", "jsonl"])

    assert status == 0
    assert len(capsys.readouterr().out.splitlines()) == 3


@pytest.mark.parametrize(
    ("args", "status", "message"),
    [
        (["sw1.log"], 2, "no platform given"),
        (["-p", "cisco_ios", "/nonexistent/sw1.log"], 2, "No such file"),
        (["-p", "cisco_ios", "--marker", "(", "x"], 2, "axlib parse:"),
    ],
)
def test_parse_command_errors(
    args: list[str], status: int, message: str, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(args) == status
    assert message in capsys.readouterr().err


def test_parse_command_reports_input_without_commands(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    notes = tmp_path / "notes.txt"
    notes.write_text("nothing that looks like a command\n", encoding="utf-8")

    assert main(["-p", "cisco_ios", str(notes)]) == 1
    assert "no commands found" in capsys.readouterr().err


def test_parse_command_fails_when_a_template_fails(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    capture = tmp_path / "bad.log"
    capture.write_text("sw1#show int status\nnot a status line\n", encoding="utf-8")

    assert main(["-p", "cisco_ios", str(capture)]) == 1
    assert "failed: bad.log:1 sw1 'show int status'" in capsys.readouterr().err
