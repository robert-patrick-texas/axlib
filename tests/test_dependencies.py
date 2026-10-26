# Copyright 2026 Robert Patrick
# SPDX-License-Identifier: Apache-2.0

"""The dependency policy tool, ``tools/dependencies.py``, without the network.

``tools/dependencies.py`` is a standalone script, not part of the package, so
it is loaded from its file.  Its ``upgrade`` and ``check`` commands need uv and
PyPI; these tests cover the parts they are built from, and check that the
repository's own ``pyproject.toml`` and holds file agree.
"""

from __future__ import annotations

import importlib.util
import sys
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "dependencies", ROOT / "tools" / "dependencies.py"
)
assert SPEC is not None
assert SPEC.loader is not None
dependencies = importlib.util.module_from_spec(SPEC)
# dataclasses look their module up in sys.modules while the class is built.
sys.modules["dependencies"] = dependencies
SPEC.loader.exec_module(dependencies)

PYPROJECT = """\
[project]
name = "axlib"
version = "9.1.0"
classifiers = [
    "Programming Language :: Python :: 3",
    "Programming Language :: Python :: 3.11",
    "Programming Language :: Python :: 3.14",
]
dependencies = [
    "cryptography>=42",
]

[project.optional-dependencies]
markdown = [
    "markdown-it-py[linkify]>=4.0",
]
tui = [
    "textual>=8.2.8,<9.1.0",
]

[dependency-groups]
dev = [
    "axlib[markdown,tui]",
    # Exact on purpose.
    "ruff==0.16.7",
    {include-group = "other"},
]
"""

LOCK = """\
version = 1

[[package]]
name = "Cryptography"
version = "50.0.2"

[[package]]
name = "numpy"
version = "2.4.0"

[[package]]
name = "numpy"
version = "2.3.5"
"""


def hold(
    name: str = "textual", held_at: str = "8.2.8", failed: str = "9.1.0"
) -> dependencies.Hold:
    """Return a hold with a fixed date and reason."""
    return dependencies.Hold(name, held_at, failed, "2026-10-26", "tests fail")


def requirements() -> list[dependencies.Requirement]:
    """Return the direct dependencies of the sample pyproject.toml."""
    return dependencies.direct_requirements(PYPROJECT)


def test_parse_lower_bound_with_extras() -> None:
    requirement = dependencies.parse_requirement("Markdown_It.py[linkify]>=4.0")
    assert requirement.name == "markdown-it-py"
    assert requirement.extras == "[linkify]"
    assert (requirement.version, requirement.pinned, requirement.capped) == (
        "4.0",
        False,
        False,
    )
    assert requirement.at("4.2.0") == "Markdown_It.py[linkify]>=4.2.0"
    assert requirement.held("4.1.0", "4.2.0") == "Markdown_It.py[linkify]>=4.1.0,<4.2.0"


def test_parse_exact_pin_stays_exact() -> None:
    requirement = dependencies.parse_requirement("ruff==0.16.7")
    assert requirement.pinned
    assert requirement.at("0.17.2") == "ruff==0.17.2"
    assert requirement.held("0.16.7", "0.17.2") == "ruff==0.16.7"
    assert requirement.loosened() == "ruff>=0.16.7"


@pytest.mark.parametrize(
    ("text", "version"),
    [("textual>=8.2.8,<9.1.0", "8.2.8"), ("rich~=15.0", "15.0"), ("x!=2,>=1", "1")],
)
def test_parse_upper_bounds(text: str, version: str) -> None:
    requirement = dependencies.parse_requirement(text)
    assert requirement.capped
    assert requirement.version == version
    assert "<" not in requirement.loosened()


def test_parse_keeps_the_marker() -> None:
    requirement = dependencies.parse_requirement('tomli>=2 ; python_version < "3.11"')
    assert requirement.at("2.5.0") == 'tomli>=2.5.0 ; python_version < "3.11"'


def test_parse_without_a_version() -> None:
    requirement = dependencies.parse_requirement("pytest")
    assert requirement.version == ""
    assert requirement.loosened() == "pytest"
    assert requirement.at("9.1.1") == "pytest>=9.1.1"


@pytest.mark.parametrize(
    "text", ["x>1", "x===1", "x>=1,>=2", "x @ https://example.com/x.whl"]
)
def test_parse_refuses_what_it_cannot_rewrite(text: str) -> None:
    with pytest.raises(ValueError, match="x"):
        dependencies.parse_requirement(text)


def test_direct_requirements_skip_the_project_and_include_groups() -> None:
    assert [r.name for r in requirements()] == [
        "cryptography",
        "markdown-it-py",
        "textual",
        "ruff",
    ]


def test_python_versions_come_from_the_classifiers() -> None:
    assert dependencies.python_versions(PYPROJECT) == ["3.11", "3.14"]


def test_rewrite_changes_only_the_requirement() -> None:
    text = dependencies.rewrite(PYPROJECT, {"ruff==0.16.7": "ruff==0.17.2"})
    assert '"ruff==0.17.2"' in text
    assert "# Exact on purpose." in text
    assert text.replace("0.17.2", "0.16.7") == PYPROJECT


def test_rewrite_refuses_a_requirement_not_written_once() -> None:
    with pytest.raises(ValueError, match="exactly once"):
        dependencies.rewrite(PYPROJECT, {"pytest>=8": "pytest>=9"})


def test_locked_versions_normalize_names_and_take_the_lowest() -> None:
    assert dependencies.locked_versions(LOCK) == {
        "cryptography": "50.0.2",
        "numpy": "2.3.5",
    }


def test_holds_round_trip_through_the_file() -> None:
    holds = {"textual": hold(), "ruff": hold("ruff", "0.16.7", "0.17.2")}
    holds["ruff"] = dependencies.Hold(
        "ruff", "0.16.7", "0.17.2", "2026-10-26", 'E999 "x"\\ \x1b[31mred\x7f'
    )
    text = dependencies.render_holds(holds)
    assert text.startswith("# Dependencies held below")
    read = dependencies.read_holds(text)
    assert read["textual"] == holds["textual"]
    assert read["ruff"].reason == 'E999 "x"\\ [31mred'
    assert list(tomllib.loads(text)) == ["ruff", "textual"]


def test_no_problems_when_everything_is_newest() -> None:
    current = [
        dependencies.parse_requirement(text)
        for text in ("cryptography>=50.0.2", "ruff==0.17.2")
    ]
    newest = {"cryptography": "50.0.2", "ruff": "0.17.2"}
    assert dependencies.problems(current, {}, newest, None) == []


def test_problems_name_each_dependency_behind() -> None:
    newest = {
        "cryptography": "50.0.2",
        "markdown-it-py": "4.0",
        "textual": "9.1.0",
        "ruff": "0.17.2",
    }
    found = dependencies.problems(
        requirements(), {"textual": hold()}, newest, "textual"
    )
    # markdown-it-py is at its newest, and textual is held below exactly 9.1.0.
    assert found == [
        (
            "cryptography: pyproject.toml asks for 'cryptography>=42', but the "
            "newest version is 50.0.2; run make deps"
        ),
        (
            "ruff: pyproject.toml asks for 'ruff==0.16.7', but the newest version "
            "is 0.17.2; run make deps"
        ),
    ]


def test_a_hold_must_be_for_the_newest_version() -> None:
    textual = [dependencies.parse_requirement("textual>=8.2.8,<9.1.0")]
    assert dependencies.problems(
        textual, {"textual": hold()}, {"textual": "9.2.0"}, "textual"
    ) == [
        (
            "textual: held below 9.1.0, but 9.2.0 is the newest version now; run "
            "make deps to try it"
        )
    ]


def test_a_hold_must_be_in_the_release_notes() -> None:
    textual = [dependencies.parse_requirement("textual>=8.2.8,<9.1.0")]
    newest = {"textual": "9.1.0"}
    held = {"textual": hold()}
    assert dependencies.problems(textual, held, newest, "### Dependencies\n") == [
        "textual: held at 8.2.8, but the release notes do not say so"
    ]
    assert dependencies.problems(textual, held, newest, None) != []
    assert dependencies.problems(textual, held, newest, "**Held: textual**") == []


def test_hold_problems_find_hand_edits() -> None:
    capped = [dependencies.parse_requirement("textual>=8.2.8,<9")]
    assert "has an upper bound" in dependencies.hold_problems(capped, {})[0]
    assert (
        "not the hold's 'textual>=8.2.8,<9.1.0'"
        in (dependencies.hold_problems(capped, {"textual": hold()})[0])
    )
    assert dependencies.hold_problems([], {"textual": hold()}) == [
        "textual: held in dependency-holds.toml, but not a dependency; run make deps"
    ]


def test_failure_reason_names_the_step_and_the_first_failure() -> None:
    log = (
        "uv run pytest\n"
        "FAILED tests/test_tui.py::test_keys - AttributeError: no attribute 'x'\n"
        "FAILED tests/test_tui.py::test_other - KeyError\n"
        "make: *** [Makefile:16: test] Error 1\n"
    )
    assert dependencies.failure_reason(log, "3.11") == (
        "make check (test) fails on Python 3.11: FAILED tests/test_tui.py::test_keys"
        " - AttributeError: no attribute 'x'"
    )
    lint = (
        "src/axlib/x.py:3:1: RUF999 new rule\nmake: *** [Makefile:11: lint] Error 1\n"
    )
    assert dependencies.failure_reason(lint, "3.14") == (
        "make check (lint) fails on Python 3.14: src/axlib/x.py:3:1: RUF999 new rule"
    )


@pytest.mark.parametrize(
    "finding",
    [
        "ICN001 `datetime` should be imported as `dt`",
        "warning[unused-ignore-comment]: Unused `ty: ignore` directive",
    ],
)
def test_failure_reason_reads_ruff_and_ty_findings_in_a_sub_make(finding: str) -> None:
    """make deps runs make check from make, so make calls itself make[1]."""
    log = (
        f"uv run ruff check .\n{finding}\n  --> tools/x.py:45:8\n   |\n"
        "make[1]: *** [Makefile:12: lint] Error 1\n"
    )
    assert dependencies.failure_reason(log, "3.11") == (
        f"make check (lint) fails on Python 3.11: {finding} (tools/x.py:45:8)"
    )


def test_release_notes_section_lists_upgrades_and_holds() -> None:
    outcome = dependencies.Outcome(
        before={"cryptography": "50.0.1", "textual": "8.2.8", "pycparser": "3.0"},
        after={"cryptography": "50.0.2", "textual": "8.2.8", "pycparser": "3.12"},
        direct=["cryptography", "textual"],
        holds={"textual": hold()},
        others_failed=None,
        pythons=["3.11", "3.12", "3.14"],
    )
    assert dependencies.render_notes(outcome) == (
        "### Dependencies\n\n"
        "Every dependency is at its newest version, except the held ones below, "
        "tested with `make check` on Python 3.11, 3.12, and 3.14.\n\n"
        "- Upgraded: cryptography 50.0.1 to 50.0.2.\n"
        "- **Held: textual 8.2.8.** Its newest version, 9.1.0, did not pass: tests "
        "fail.  Held since 2026-10-26; the next release tries the newest version "
        "again.\n"
        "- Also upgraded in `uv.lock`: pycparser.\n"
    )


def test_this_projects_holds_match_its_pyproject() -> None:
    """A hand-made upper bound or a hand-edited hold fails make check."""
    project = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    holds = (ROOT / "tools" / "dependency-holds.toml").read_text(encoding="utf-8")
    assert (
        dependencies.hold_problems(
            dependencies.direct_requirements(project), dependencies.read_holds(holds)
        )
        == []
    )


def test_main_needs_a_command(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exit_info:
        dependencies.main([])
    assert exit_info.value.code == 2
    assert "check,upgrade" in capsys.readouterr().err
