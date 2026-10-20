# Copyright 2026 Robert Patrick
# SPDX-License-Identifier: Apache-2.0

"""Tests for cutting captured CLI sessions into command blocks."""

from __future__ import annotations

import io
import json
import sys
from pathlib import Path

import pytest

from axlib.parse.capture import (
    clean_line,
    iter_capture_files,
    main,
    read_capture,
    single_block,
    split_capture,
)

EXAMPLES = Path(__file__).resolve().parents[1] / "examples" / "parse"


@pytest.mark.parametrize(
    ("line", "host", "command"),
    [
        ("sw1#show cdp neighbors", "sw1", "show cdp neighbors"),
        ("sw1>show version", "sw1", "show version"),
        ("sw1(config-if)#do show ip int brief", "sw1", "do show ip int brief"),
        ("admin@mx1> show lldp neighbors", "mx1", "show lldp neighbors"),
        (
            "RP/0/RSP0/CPU0:xr1#show cdp neighbors detail",
            "xr1",
            "show cdp neighbors detail",
        ),
        ("edge-01.example.net#  show clock  ", "edge-01.example.net", "show clock"),
    ],
)
def test_prompt_styles_name_host_and_command(
    line: str, host: str, command: str
) -> None:
    blocks = split_capture(f"{line}\noutput line\n")

    assert [(block.host, block.command, block.output) for block in blocks] == [
        (host, command, "output line\n")
    ]


def test_bare_prompt_ends_a_block_and_banner_is_ignored() -> None:
    text = (
        "Welcome to sw1\n"
        "sw1#show clock\n"
        "10:01:02 UTC\n"
        "sw1#\n"
        "stray text after the prompt\n"
        "sw1#exit\n"
    )

    blocks = split_capture(text, source="sw1.log")

    assert [(block.command, block.output, block.line) for block in blocks] == [
        ("show clock", "10:01:02 UTC\n", 2),
        ("exit", "", 6),
    ]
    assert all(block.source == "sw1.log" for block in blocks)


def test_bare_commands_take_the_last_prompt_host() -> None:
    text = "show version\nv1\nsw9#show clock\nnow\nsh cdp nei\nnone\n"

    blocks = split_capture(text)

    assert [(block.host, block.command) for block in blocks] == [
        (None, "show version"),
        ("sw9", "show clock"),
        ("sw9", "sh cdp nei"),
    ]


def test_explicit_host_wins_over_prompts() -> None:
    blocks = split_capture("sw1#show clock\nnow\n", host="core-1")

    assert blocks[0].host == "core-1"


@pytest.mark.parametrize(
    "line",
    [
        "C>* 10.0.0.0/8 is directly connected, eth0",
        "  show history entry indented",
        "showing something",
        "Device ID: sw2",
        "Platform: cisco C9300-48P,  Capabilities: Router Switch IGMP",
    ],
)
def test_output_lines_are_not_commands(line: str) -> None:
    blocks = split_capture(f"sw1#show x\n{line}\n")

    assert len(blocks) == 1
    assert blocks[0].output == f"{line}\n"


def test_pager_prompts_and_device_noise_are_removed() -> None:
    text = (
        "sw1#show interfaces status\r\n"
        "Load for five secs: 5%/0%; one minute: 6%; five minutes: 5%\n"
        "Time source is NTP, 10:12:01.123 UTC Mon Oct 19 2026\n"
        "Gi1/0/1  connected\n"
        " --More-- \x08\x08\x08\x08\x08\x08\x08\x08\x08         "
        "\x08\x08\x08\x08\x08\x08\x08\x08\x08Gi1/0/2  notconnect\n"
        "{master:0}\n"
        "[edit]\n"
        "admin@mx1> show lldp neighbors | no-more\n"
        "ge-0/0/1 ---(more 45%)---\n"
    )

    blocks = split_capture(text)

    assert blocks[0].output == "Gi1/0/1  connected\nGi1/0/2  notconnect\n"
    assert blocks[1].output == "ge-0/0/1\n"


def test_clean_line_handles_junos_pager() -> None:
    assert clean_line("---(more)---\x08\x08  \x08\x08xe-0/0/0 up") == "xe-0/0/0 up"


def test_marker_pattern_adds_a_site_header_format() -> None:
    text = "===== sw7: show clock =====\n10:00\n===== sw8: show clock =====\n11:00\n"

    blocks = split_capture(text, marker=r"=+ (?P<host>\S+): (?P<command>.+?) =+$")

    assert [(b.host, b.command, b.output) for b in blocks] == [
        ("sw7", "show clock", "10:00\n"),
        ("sw8", "show clock", "11:00\n"),
    ]


def test_marker_needs_a_command_group() -> None:
    with pytest.raises(ValueError, match="command"):
        split_capture("x\n", marker=r"=+ (?P<host>\S+)")


def test_single_block_cleans_but_does_not_split() -> None:
    block = single_block(
        "Load for five secs: 1%\nsh this is output\n\n\n", " show x ", host="sw1"
    )

    assert (block.command, block.output, block.host, block.line) == (
        "show x",
        "sh this is output\n",
        "sw1",
        0,
    )


def test_iter_capture_files_sorts_recurses_and_skips_hidden(tmp_path: Path) -> None:
    for name in ("b.txt", "a.txt", "sub/c.txt", "sub/d.log", ".git/config", "x/.swp"):
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("x", encoding="utf-8")

    flat = [source for _, source in iter_capture_files([str(tmp_path)])]
    deep = [
        source
        for _, source in iter_capture_files(
            [str(tmp_path)], recursive=True, pattern="*.txt"
        )
    ]
    named = list(iter_capture_files([str(tmp_path / "a.txt"), "-"]))

    assert flat == ["a.txt", "b.txt"]
    assert deep == ["a.txt", "b.txt", "sub/c.txt"]
    assert named == [(tmp_path / "a.txt", "a.txt"), (None, "-")]


def test_read_capture_skips_binary_and_replaces_bad_bytes(tmp_path: Path) -> None:
    binary = tmp_path / "image.png"
    binary.write_bytes(b"\x89PNG\x00\x01")
    latin = tmp_path / "latin.log"
    latin.write_bytes(b"caf\xe9\n")

    assert read_capture(binary) is None
    assert read_capture(latin) == "caf�\n"


def test_read_capture_reads_standard_input(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "stdin", io.StringIO("sw1#show clock\n"))

    assert read_capture(None) == "sw1#show clock\n"


def test_split_command_lists_and_dumps_blocks(
    capsys: pytest.CaptureFixture[str],
) -> None:
    capture = str(EXAMPLES / "before" / "sw2.txt")

    assert main([capture]) == 0
    listing = capsys.readouterr().out
    assert main([capture, "--json"]) == 0
    blocks = json.loads(capsys.readouterr().out)

    assert listing.splitlines() == [
        "sw2.txt:1  -  show cdp neighbors  (11 lines)",
        "sw2.txt:14  -  show interfaces status  (5 lines)",
    ]
    assert [block["command"] for block in blocks] == [
        "show cdp neighbors",
        "show interfaces status",
    ]


def test_split_command_reports_unreadable_input(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main(["/nonexistent/capture.log"]) == 2
    assert "axlib parse split:" in capsys.readouterr().err
