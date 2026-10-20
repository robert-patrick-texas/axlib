# Copyright 2026 Robert Patrick
# SPDX-License-Identifier: Apache-2.0

"""Exit-status and error-message tests for the ``axlib tf`` commands."""

from __future__ import annotations

import io
from pathlib import Path

import pytest

from axlib.tf.__main__ import COMMAND_MODULES
from axlib.tf.__main__ import main as text_main


@pytest.mark.parametrize("command", sorted(COMMAND_MODULES))
def test_undecodable_input_is_an_error_not_a_usage_message(
    tmp_path: Path, command: str, capsys: pytest.CaptureFixture[str]
) -> None:
    source = tmp_path / "latin1.conf"
    source.write_bytes(b"caf\xe9\n")

    status = text_main([command, str(source)])
    captured = capsys.readouterr()

    assert status == 1
    assert captured.out == ""
    assert captured.err == (
        f"axlib tf {command}: 'utf-8' codec can't decode byte 0xe9 in position 3: "
        "invalid continuation byte\n"
    )


def test_undecodable_include_file_is_an_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / "latin1.conf").write_bytes(b"caf\xe9\n")
    site = tmp_path / "site.conf"
    site.write_text("@include latin1.conf\n", encoding="utf-8")

    status = text_main(["include", str(site)])

    assert status == 1
    assert capsys.readouterr().err.startswith("axlib tf include: 'utf-8' codec")


@pytest.mark.parametrize(
    "arguments",
    [
        ["include", "-d", ""],
        ["include", "--max-depth", "-1"],
        ["comments", "-c", "##"],
    ],
)
def test_bad_option_values_still_print_usage(
    tmp_path: Path, arguments: list[str], capsys: pytest.CaptureFixture[str]
) -> None:
    source = tmp_path / "site.conf"
    source.write_text("hostname edge-1\n", encoding="utf-8")

    with pytest.raises(SystemExit) as excinfo:
        text_main([*arguments, str(source)])

    assert excinfo.value.code == 2
    assert capsys.readouterr().err.startswith(f"usage: axlib tf {arguments[0]} ")


def test_include_blocks_cycle_back_to_input_file(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    top = tmp_path / "top.conf"
    top.write_text("top\n@include b.conf\n", encoding="utf-8")
    (tmp_path / "b.conf").write_text("bee\n@include top.conf\n", encoding="utf-8")

    status = text_main(["include", str(top)])

    assert status == 0
    assert capsys.readouterr().out == "top\nbee\n@include top.conf\n"


def test_include_from_standard_input_has_no_input_file_to_block(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tmp_path / "top.conf").write_text("top\n@include b.conf\n", encoding="utf-8")
    (tmp_path / "b.conf").write_text("bee\n@include top.conf\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("sys.stdin", io.StringIO("top\n@include b.conf\n"))

    status = text_main(["include"])

    assert status == 0
    assert capsys.readouterr().out == "top\nbee\ntop\n@include b.conf\n"
