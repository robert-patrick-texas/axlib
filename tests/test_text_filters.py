# Copyright 2026 Robert Patrick
# SPDX-License-Identifier: Apache-2.0

"""Behavior tests for reusable axlib text transformations."""

from __future__ import annotations

from pathlib import Path

import pytest

from axlib.tf.incfile import expand_includes
from axlib.tf.rmcomment import strip_comments
from axlib.tf.rmdouble import remove_double_slash
from axlib.tf.rmline import normalize_text
from axlib.tf.rmtriple import remove_triple_quoted
from axlib.tf.rmwhite import normalize_spaces_outside_quotes
from axlib.tf.rmwhite2 import normalize
from axlib.tf.varsub import parse_variable_assignments, substitute_variables


def test_expand_includes_nested_and_relative(tmp_path: Path) -> None:
    fragments = tmp_path / "fragments"
    fragments.mkdir()
    (fragments / "interface.conf").write_text(
        "interface Gi0/1\n@include description.conf\n",
        encoding="utf-8",
    )
    (fragments / "description.conf").write_text(
        " description Uplink\n",
        encoding="utf-8",
    )

    result = expand_includes(
        "hostname edge-1\n@include fragments/interface.conf\n",
        base_dir=tmp_path,
    )

    assert result == ("hostname edge-1\ninterface Gi0/1\n description Uplink\n")


def test_expand_includes_cycle_and_missing_are_visible(tmp_path: Path) -> None:
    (tmp_path / "a.conf").write_text("@include b.conf\n", encoding="utf-8")
    (tmp_path / "b.conf").write_text("@include a.conf\n", encoding="utf-8")

    cycle = expand_includes("@include a.conf\n", base_dir=tmp_path)
    missing = expand_includes("@include absent.conf\n", base_dir=tmp_path)

    assert cycle == "@include a.conf\n"
    assert missing == "@include absent.conf\n"


def test_expand_includes_blocks_cycle_back_to_source_file(tmp_path: Path) -> None:
    top = tmp_path / "top.conf"
    top.write_text("top\n@include b.conf\n", encoding="utf-8")
    (tmp_path / "b.conf").write_text("bee\n@include top.conf\n", encoding="utf-8")
    text = top.read_text(encoding="utf-8")

    # The source file counts as already being expanded, and its directory is
    # the default base for relative paths.
    from_file = expand_includes(text, source_path=top)
    # Text without a source file, such as standard input, has no path to block,
    # so top.conf is inserted once before the repeated file stops the cycle.
    no_source = expand_includes(text, base_dir=tmp_path)

    assert from_file == "top\nbee\n@include top.conf\n"
    assert no_source == "top\nbee\ntop\n@include b.conf\n"


def test_expand_includes_blocks_self_include_without_recursion(
    tmp_path: Path,
) -> None:
    top = tmp_path / "top.conf"
    top.write_text("top\n@include top.conf\n", encoding="utf-8")
    text = top.read_text(encoding="utf-8")

    assert expand_includes(text, source_path=top, recursive=False) == text
    assert expand_includes(text, source_path=top, max_depth=0) == text


@pytest.mark.parametrize("ending", ["\n", "\r\n", "\r"])
@pytest.mark.parametrize("recursive", [True, False])
def test_expand_includes_ends_unterminated_file_like_directive(
    tmp_path: Path, ending: str, recursive: bool
) -> None:
    (tmp_path / "last.conf").write_text("no newline at end", encoding="utf-8")
    text = f"@include last.conf{ending}end-of-site{ending}"

    result = expand_includes(text, base_dir=tmp_path, recursive=recursive)

    assert result == f"no newline at end{ending}end-of-site{ending}"


def test_expand_includes_adds_no_line_ending_the_directive_lacked(
    tmp_path: Path,
) -> None:
    (tmp_path / "last.conf").write_text("no newline at end", encoding="utf-8")
    (tmp_path / "crlf.conf").write_bytes(b"crlf\r\n")
    (tmp_path / "empty.conf").write_text("", encoding="utf-8")

    # A directive on a last line that has no ending, a file that ends with its
    # own line ending, and an empty file are all inserted unchanged.
    assert expand_includes("@include last.conf", base_dir=tmp_path) == (
        "no newline at end"
    )
    assert expand_includes("@include crlf.conf\nx\n", base_dir=tmp_path) == (
        "crlf\r\nx\n"
    )
    assert expand_includes("a\n@include empty.conf\nb\n", base_dir=tmp_path) == (
        "a\nb\n"
    )


def test_expand_includes_zero_depth_inserts_direct_file_only(tmp_path: Path) -> None:
    (tmp_path / "outer.conf").write_text(
        "outer\n@include inner.conf\n",
        encoding="utf-8",
    )
    (tmp_path / "inner.conf").write_text("inner\n", encoding="utf-8")

    result = expand_includes(
        "@include outer.conf\n",
        base_dir=tmp_path,
        max_depth=0,
    )

    assert result == "outer\n@include inner.conf\n"


def test_strip_comments_protects_quoted_and_embedded_markers() -> None:
    text = (
        "hostname edge-1  # inventory note\n"
        'description "WAN #1"\n'
        "snmp community#literal\n"
        "! whole line\n"
    )
    assert strip_comments(text) == (
        'hostname edge-1  \ndescription "WAN #1"\nsnmp community#literal\n'
    )


def test_remove_double_slash_only_removes_full_comment_lines() -> None:
    text = "// note\n  // second\nurl https://example.test/a//b\n"
    assert remove_double_slash(text) == "url https://example.test/a//b\n"


def test_remove_triple_quoted_spans() -> None:
    text = "before '''multiline\nnote''' after\nkeep\n"
    assert remove_triple_quoted(text) == "before  after\nkeep\n"


@pytest.mark.parametrize("ending", ["\n", "\r\n", "\r"])
def test_remove_triple_quoted_spans_with_every_line_ending(ending: str) -> None:
    text = "keep\na \"\"\"x\ny\"\"\" b\n'''note\nmore'''\nend\n"
    expected = "keep\na  b\nend\n"

    result = remove_triple_quoted(text.replace("\n", ending))

    assert result == expected.replace("\n", ending)


def test_remove_triple_quoted_keeps_mixed_line_endings_outside_spans() -> None:
    text = "one\r\ntwo '''x\r\ny\rz''' three\rfour\n"
    assert remove_triple_quoted(text) == "one\r\ntwo  three\rfour\n"


def test_normalize_lines_and_blank_lines() -> None:
    text = "  show version  \n   \n\n  show clock\n"
    assert normalize_text(text) == "show version\nshow clock\n"
    assert normalize_text(text, collapse_blank_lines=True) == (
        "show version\n\nshow clock\n"
    )


def test_whitespace_normalizers_preserve_quoted_content() -> None:
    text = "set\t  description='WAN   Link'  next\n"
    expected = "set description='WAN   Link' next\n"
    assert normalize_spaces_outside_quotes(text) == expected
    assert normalize(text) == expected


@pytest.mark.parametrize("ending", ["\n", "\r\n", "\r"])
def test_whitespace_safe_quotes_stop_at_every_line_ending(ending: str) -> None:
    # A quote never closes on a later line, even after a backslash, and a
    # straight single quote at the start of a line can open a quoted region.
    text = 'say "hi\nnext  line  "\nask "x\\\nnext  line  "\n\'a  b\'\n'
    expected = 'say "hi\nnext line "\nask "x\\\nnext line "\n\'a  b\'\n'

    result = normalize(text.replace("\n", ending))

    assert result == expected.replace("\n", ending)


def test_variable_substitution_is_case_insensitive() -> None:
    text = "<VAR>HOSTNAME</VAR> at <var>site</var> <var>unknown</var>"
    result = substitute_variables(text, {"hostname": "Core-SW1", "SITE": "DAL"})
    assert result == "Core-SW1 at DAL <var>unknown</var>"
    assert parse_variable_assignments(["hostname=edge-1", "banner=a=b"]) == {
        "hostname": "edge-1",
        "banner": "a=b",
    }


def test_last_variable_assignment_wins_in_any_case() -> None:
    values = parse_variable_assignments(["host=a", "Host=b", "host=c"])

    assert values == {"host": "c"}
    assert substitute_variables("<var>HOST</var>", values) == "c"
