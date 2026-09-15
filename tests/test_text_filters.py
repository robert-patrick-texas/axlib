"""Behavior tests for reusable axlib text transformations."""

from __future__ import annotations

from pathlib import Path

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

    assert result == (
        "hostname edge-1\ninterface Gi0/1\n description Uplink\n"
    )


def test_expand_includes_cycle_and_missing_are_visible(tmp_path: Path) -> None:
    (tmp_path / "a.conf").write_text("@include b.conf\n", encoding="utf-8")
    (tmp_path / "b.conf").write_text("@include a.conf\n", encoding="utf-8")

    cycle = expand_includes("@include a.conf\n", base_dir=tmp_path)
    missing = expand_includes("@include absent.conf\n", base_dir=tmp_path)

    assert cycle == "@include a.conf\n"
    assert missing == "@include absent.conf\n"


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
        "description \"WAN #1\"\n"
        "snmp community#literal\n"
        "! whole line\n"
    )
    assert strip_comments(text) == (
        "hostname edge-1  \n"
        "description \"WAN #1\"\n"
        "snmp community#literal\n"
    )


def test_remove_double_slash_only_removes_full_comment_lines() -> None:
    text = "// note\n  // second\nurl https://example.test/a//b\n"
    assert remove_double_slash(text) == "url https://example.test/a//b\n"


def test_remove_triple_quoted_spans() -> None:
    text = "before '''multiline\nnote''' after\nkeep\n"
    assert remove_triple_quoted(text) == "before  after\nkeep\n"


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


def test_variable_substitution_is_case_insensitive() -> None:
    text = "<VAR>HOSTNAME</VAR> at <var>site</var> <var>unknown</var>"
    result = substitute_variables(text, {"hostname": "Core-SW1", "SITE": "DAL"})
    assert result == "Core-SW1 at DAL <var>unknown</var>"
    assert parse_variable_assignments(["hostname=edge-1", "banner=a=b"]) == {
        "hostname": "edge-1",
        "banner": "a=b",
    }
