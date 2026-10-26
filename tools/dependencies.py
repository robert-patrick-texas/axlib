# Copyright 2026 Robert Patrick
# SPDX-License-Identifier: Apache-2.0

"""Keep every dependency at its newest version, and hold back only what fails.

Each release references, and is tested with, the newest version of every
direct dependency in ``pyproject.toml`` (the runtime dependencies, the
optional extras, and the dev group), even one published the same day.  A
dependency stays on an older version only when its newest version fails
``make check``.  That is a *hold*: recorded in ``tools/dependency-holds.toml``,
called out in the release notes, and tried again at the next release.

``upgrade`` (``make deps``) is the first step of a release:

1. uv resolves a copy of ``pyproject.toml`` without the holds' upper bounds,
   which gives the newest version of each dependency that every supported
   Python can use.
2. Every requirement is raised to its newest version (``>=``, or ``==`` for an
   exact pin such as ruff's), ``uv lock --upgrade`` updates every package in
   ``uv.lock``, and ``make check`` runs on each Python version named in the
   classifiers.
3. If that fails, the files go back to how they were, and the dependencies are
   tried one at a time.  One whose newest version fails is held at the version
   that passed, below the failing one.  Then the other packages in
   ``uv.lock`` are upgraded if ``make check`` still passes.
4. The release notes' Dependencies section is printed to standard output.

``check`` (``make deps-check``) is the release gate in
``.github/workflows/release.yml``.  It fails when a dependency has a newer
version than ``pyproject.toml`` asks for and no hold for exactly that
version, when a requirement has an upper bound that no hold explains, or when
the version's ``RELEASE_NOTES.md`` section does not name a held dependency.

Dependencies:
    Python standard library only; runs ``uv`` and ``make``.

Example:
    ``python tools/dependencies.py upgrade > dependencies.md``
    ``python tools/dependencies.py check``
"""

from __future__ import annotations

import argparse
import datetime as dt
import importlib.util
import json
import os
import re
import subprocess
import sys
import tempfile
import tomllib
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType

ROOT = Path(__file__).resolve().parents[1]
PYPROJECT = ROOT / "pyproject.toml"
LOCK = ROOT / "uv.lock"
HOLDS = ROOT / "tools" / "dependency-holds.toml"
NOTES = ROOT / "RELEASE_NOTES.md"
# make check output for each attempt, and one environment per Python version.
WORK = ROOT / "build" / "dependencies"

REQUIREMENT = re.compile(
    r"^(?P<name>[A-Za-z0-9][A-Za-z0-9._-]*)\s*(?P<extras>\[[^\]]*\])?\s*"
    r"(?P<specifiers>[^;]*?)\s*(?P<marker>;.*)?$"
)
CLAUSE = re.compile(r"^(===|~=|==|!=|<=|>=|<|>)\s*(\S+)$")
# Clauses that keep a dependency below some version: a hold's, or a mistake.
UPPER_BOUNDS = {"<", "<=", "!=", "~="}
PYTHON_CLASSIFIER = re.compile(r"^Programming Language :: Python :: (3\.\d+)$")

HOLDS_HEADER = """\
# Dependencies held below their newest version, written by `make deps`
# (tools/dependencies.py).  Each one's newest version, `failed`, made
# `make check` fail, so pyproject.toml keeps it at `held_at` (below `failed`)
# and the release notes say why.  `make deps` tries the newest version again
# at every release and removes the hold once it passes.  Do not edit by hand.
"""


def normalize(name: str) -> str:
    """Return a package name in its normal form, as PEP 503 compares names.

    Args:
        name (str): A package name as written, such as ``"Markdown_It.py"``.

    Returns:
        str: The lowercase name with runs of ``-``, ``_``, and ``.`` as ``-``.
    """
    return re.sub(r"[-_.]+", "-", name).lower()


@dataclass(frozen=True)
class Requirement:
    """One dependency as ``pyproject.toml`` writes it.

    Attributes:
        text (str): The requirement exactly as written, such as
            ``"textual>=8.2.8"``.
        name (str): The normalized package name.
        written_name (str): The package name as written.
        extras (str): The extras in brackets, such as ``"[linkify]"``, or ``""``.
        version (str): The version of the lower bound or exact pin, or ``""``.
        pinned (bool): Whether the requirement is an exact ``==`` pin.
        capped (bool): Whether it has an upper bound or exclusion.
        marker (str): The environment marker with its ``;``, or ``""``.
    """

    text: str
    name: str
    written_name: str
    extras: str
    version: str
    pinned: bool
    capped: bool
    marker: str

    def at(self, version: str) -> str:
        """Return this requirement raised to ``version``, without upper bounds.

        Args:
            version (str): The version to require.

        Returns:
            str: ``name>=version``, or ``name==version`` for an exact pin.
        """
        operator = "==" if self.pinned else ">="
        return f"{self.written_name}{self.extras}{operator}{version}{self.marker}"

    def held(self, version: str, failed: str) -> str:
        """Return this requirement held at ``version``, below ``failed``.

        Args:
            version (str): The newest version that passed ``make check``.
            failed (str): The newest version, which failed it.

        Returns:
            str: ``name>=version,<failed``, or ``name==version`` for a pin.
        """
        if self.pinned:
            return self.at(version)
        return f"{self.written_name}{self.extras}>={version},<{failed}{self.marker}"

    def loosened(self) -> str:
        """Return this requirement with its lower bound only, for finding updates.

        Returns:
            str: ``name>=version`` (an exact pin becomes a lower bound), or the
            bare name when there is no version.
        """
        bound = f">={self.version}" if self.version else ""
        return f"{self.written_name}{self.extras}{bound}{self.marker}"


def parse_requirement(text: str) -> Requirement:
    """Parse one requirement string of the forms this project uses.

    Args:
        text (str): A PEP 508 requirement such as ``"rich>=15.0.0"`` or
            ``"markdown-it-py[linkify]>=4.0,<5"``.

    Returns:
        Requirement: The parsed requirement.

    Raises:
        ValueError: If the text is not a requirement, or uses a specifier this
            tool cannot rewrite (``>``, ``===``, a URL, or two lower bounds).
    """
    match = REQUIREMENT.match(text.strip())
    if match is None:
        raise ValueError(f"cannot read the requirement {text!r}")
    version, pinned, capped = "", False, False
    for clause in filter(None, (c.strip() for c in match["specifiers"].split(","))):
        parsed = CLAUSE.match(clause)
        if parsed is None or parsed[1] in {">", "==="}:
            raise ValueError(f"{text!r}: cannot rewrite the specifier {clause!r}")
        operator, number = parsed.groups()
        if operator in {">=", "=="}:
            if version:
                raise ValueError(f"{text!r} has two lower bounds")
            version, pinned = number, operator == "=="
        else:
            capped = True
            if operator == "~=" and not version:
                version = number  # ~=X.Y also means >=X.Y
    return Requirement(
        text=text,
        name=normalize(match["name"]),
        written_name=match["name"],
        extras=match["extras"] or "",
        version=version,
        pinned=pinned,
        capped=capped,
        marker=f" {match['marker']}" if match["marker"] else "",
    )


def direct_requirements(pyproject_text: str) -> list[Requirement]:
    """Return every direct dependency in ``pyproject.toml``, in file order.

    Args:
        pyproject_text (str): The contents of ``pyproject.toml``.

    Returns:
        list[Requirement]: The runtime dependencies, each optional extra's, and
        each dependency group's, without the project itself (the dev group's
        ``axlib[...]``) and without ``{include-group = ...}`` entries.
    """
    data = tomllib.loads(pyproject_text)
    project = data.get("project", {})
    texts = list(project.get("dependencies", []))
    for extra in project.get("optional-dependencies", {}).values():
        texts.extend(extra)
    for group in data.get("dependency-groups", {}).values():
        texts.extend(entry for entry in group if isinstance(entry, str))
    own_name = normalize(project.get("name", ""))
    requirements = [parse_requirement(text) for text in texts]
    return [requirement for requirement in requirements if requirement.name != own_name]


def python_versions(pyproject_text: str) -> list[str]:
    """Return the Python versions the classifiers list, such as ``["3.11"]``.

    Args:
        pyproject_text (str): The contents of ``pyproject.toml``.

    Returns:
        list[str]: Each ``Programming Language :: Python :: 3.X`` version, in
        file order: the versions CI tests and ``make check`` must pass on.
    """
    classifiers = tomllib.loads(pyproject_text)["project"].get("classifiers", [])
    return [
        match[1]
        for match in map(PYTHON_CLASSIFIER.match, classifiers)
        if match is not None
    ]


def rewrite(pyproject_text: str, changes: dict[str, str]) -> str:
    """Replace requirement strings in ``pyproject.toml``, keeping everything else.

    Args:
        pyproject_text (str): The contents of ``pyproject.toml``.
        changes (dict[str, str]): Old requirement text to new requirement text.

    Returns:
        str: The file with each quoted ``"old"`` replaced by ``"new"``.

    Raises:
        ValueError: If an old requirement is not written exactly once.
    """
    for old, new in changes.items():
        quoted = f'"{old}"'
        if pyproject_text.count(quoted) != 1:
            raise ValueError(f"{quoted} is not written exactly once in pyproject.toml")
        pyproject_text = pyproject_text.replace(quoted, f'"{new}"')
    return pyproject_text


def version_key(version: str) -> tuple[int, ...]:
    """Return a sort key that orders release numbers such as ``"0.0.92"``.

    Args:
        version (str): A version string.

    Returns:
        tuple[int, ...]: Its numbers in order; enough to pick the lowest of a
        package's versions in one lock file.
    """
    return tuple(int(number) for number in re.findall(r"\d+", version))


def locked_versions(lock_text: str) -> dict[str, str]:
    """Return the version of each package in a ``uv.lock`` file.

    Args:
        lock_text (str): The contents of ``uv.lock``.

    Returns:
        dict[str, str]: Normalized package name to version.  A package locked
        at several versions (for different Pythons) gives the lowest.
    """
    versions: dict[str, list[str]] = {}
    for package in tomllib.loads(lock_text).get("package", []):
        versions.setdefault(normalize(package["name"]), []).append(package["version"])
    return {name: min(found, key=version_key) for name, found in versions.items()}


def uv(*arguments: str, cwd: Path = ROOT) -> None:
    """Run uv, and raise with its error output if it fails.

    Args:
        *arguments (str): The arguments after ``uv``, such as ``"lock"``.
        cwd (Path): The project directory.

    Raises:
        RuntimeError: If uv exits with a nonzero status.
    """
    # A lock that must not change would stop every command here.
    environment = {
        key: value
        for key, value in os.environ.items()
        if key not in {"UV_LOCKED", "UV_FROZEN"}
    }
    result = subprocess.run(  # noqa: S603
        ["uv", *arguments, "--quiet"],  # noqa: S607
        cwd=cwd,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(f"uv {' '.join(arguments)} failed:\n{result.stderr.strip()}")


def newest_versions(
    pyproject_text: str, requirements: list[Requirement]
) -> dict[str, str]:
    """Return the newest version of each dependency that uv can resolve.

    uv locks a copy of ``pyproject.toml`` in which every requirement keeps
    only its lower bound, so a hold's upper bound or an exact pin does not hide
    a newer release.  A version that no supported Python can use is not
    offered, because uv resolves for every Python in ``requires-python``.

    Args:
        pyproject_text (str): The contents of ``pyproject.toml``.
        requirements (list[Requirement]): Its direct dependencies.

    Returns:
        dict[str, str]: Normalized package name to newest version.

    Raises:
        RuntimeError: If uv cannot resolve the copy, such as without network.
    """
    loosened = rewrite(
        pyproject_text,
        {r.text: r.loosened() for r in requirements if r.text != r.loosened()},
    )
    with tempfile.TemporaryDirectory() as directory:
        Path(directory, "pyproject.toml").write_text(loosened, encoding="utf-8")
        uv("lock", "--upgrade", cwd=Path(directory))
        versions = locked_versions(
            Path(directory, "uv.lock").read_text(encoding="utf-8")
        )
    return {r.name: versions[r.name] for r in requirements}


@dataclass(frozen=True)
class Hold:
    """A dependency kept below its newest version because that version failed.

    Attributes:
        name (str): The normalized package name.
        held_at (str): The version kept: the newest one that passed.
        failed (str): The newest version, which failed ``make check``.
        since (str): The date the dependency was first held, ``YYYY-MM-DD``.
        reason (str): One line on what failed.
    """

    name: str
    held_at: str
    failed: str
    since: str
    reason: str


def read_holds(text: str) -> dict[str, Hold]:
    """Read the holds file.

    Args:
        text (str): The contents of ``tools/dependency-holds.toml``.

    Returns:
        dict[str, Hold]: Normalized package name to its hold.
    """
    return {
        normalize(name): Hold(normalize(name), **fields)
        for name, fields in tomllib.loads(text).items()
    }


def render_holds(holds: dict[str, Hold]) -> str:
    """Write the holds file: the header, then one table per held dependency.

    Args:
        holds (dict[str, Hold]): The holds to record.

    Returns:
        str: TOML text that ``read_holds`` reads back unchanged.
    """
    lines = [HOLDS_HEADER]
    for hold in sorted(holds.values(), key=lambda hold: hold.name):
        lines.append(f"\n[{hold.name}]\n")
        for field in ("held_at", "failed", "since", "reason"):
            # A JSON string is a TOML basic string once control characters,
            # which TOML forbids even escaped as JSON leaves DEL, are gone.
            value = "".join(c for c in getattr(hold, field) if c.isprintable())
            lines.append(f"{field} = {json.dumps(value)}\n")
    return "".join(lines)


def release_notes_section(version: str) -> str | None:
    """Return the ``RELEASE_NOTES.md`` section of a version, if there is one.

    Args:
        version (str): The release number in ``pyproject.toml``.

    Returns:
        str | None: The section's text, or ``None`` when it has none.
    """
    release_notes = _load_sibling("release_notes")
    try:
        return release_notes.release_section(NOTES.read_text(encoding="utf-8"), version)
    except LookupError:
        return None


def _load_sibling(name: str) -> ModuleType:
    """Load another standalone script in ``tools/``, which is not a package.

    Args:
        name (str): The script's name without ``.py``.

    Returns:
        ModuleType: The loaded module.
    """
    spec = importlib.util.spec_from_file_location(
        name, Path(__file__).with_name(f"{name}.py")
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def hold_problems(requirements: list[Requirement], holds: dict[str, Hold]) -> list[str]:
    """Return where ``pyproject.toml`` and the holds file disagree.

    This needs no network, so ``make check`` runs it on every push: an upper
    bound added by hand, or a hold edited by hand, is found at once.

    Args:
        requirements (list[Requirement]): The direct dependencies.
        holds (dict[str, Hold]): The recorded holds.

    Returns:
        list[str]: One line per problem: an upper bound without a hold, a
        requirement that is not its hold's, or a hold of no dependency.
    """
    found = []
    for requirement in requirements:
        hold = holds.get(requirement.name)
        if hold is None:
            if requirement.capped:
                found.append(
                    f"{requirement.name}: {requirement.text!r} has an upper bound "
                    f"that no hold in {HOLDS.name} explains; run make deps"
                )
            continue
        expected = requirement.held(hold.held_at, hold.failed)
        if requirement.text != expected:
            found.append(
                f"{requirement.name}: pyproject.toml asks for {requirement.text!r}, "
                f"not the hold's {expected!r}"
            )
    names = {requirement.name for requirement in requirements}
    found.extend(
        f"{name}: held in {HOLDS.name}, but not a dependency; run make deps"
        for name in sorted(set(holds) - names)
    )
    return found


def problems(
    requirements: list[Requirement],
    holds: dict[str, Hold],
    newest: dict[str, str],
    notes: str | None,
) -> list[str]:
    """Return what keeps the dependencies from meeting the release policy.

    Args:
        requirements (list[Requirement]): The direct dependencies.
        holds (dict[str, Hold]): The recorded holds.
        newest (dict[str, str]): The newest version of each dependency.
        notes (str | None): The release's ``RELEASE_NOTES.md`` section, or
            ``None`` when it has none.

    Returns:
        list[str]: One line per problem (``hold_problems`` first); empty when
        every dependency is at its newest version, or held below exactly that
        version and named in the release notes.
    """
    found = hold_problems(requirements, holds)
    for requirement in requirements:
        name, latest = requirement.name, newest[requirement.name]
        hold = holds.get(name)
        if hold is None:
            if not requirement.capped and requirement.text != requirement.at(latest):
                found.append(
                    f"{name}: pyproject.toml asks for {requirement.text!r}, but the "
                    f"newest version is {latest}; run make deps"
                )
        elif hold.failed != latest:
            found.append(
                f"{name}: held below {hold.failed}, but {latest} is the newest "
                "version now; run make deps to try it"
            )
        elif notes is None or name not in notes.lower():
            found.append(
                f"{name}: held at {hold.held_at}, but the release notes do not say so"
            )
    return found


def failure_reason(log: str, python: str) -> str:
    """Summarize a failed ``make check`` in one line.

    Args:
        log (str): Everything ``make check`` printed.
        python (str): The Python version it ran on.

    Returns:
        str: The failing step, the Python version, and the first line that
        names a failure (a pytest ``FAILED`` line, a ruff or ty finding).
    """
    step = re.search(r"make(?:\[\d+\])?: \*\*\* \[Makefile:\d+: ([\w-]+)\]", log)
    # A pytest summary line, ruff's or ty's first finding (with the location
    # on the line after it, or before it in ruff's concise format), or a file
    # ruff would reformat.
    detail = re.search(
        r"^(?:FAILED |ERROR |(?:error|warning)\[|[A-Z]+\d+ |\S+\.py:\d+:\d+: "
        r"|Would reformat).*"
        r"(?:\n\s*--> (\S+))?",
        log,
        re.MULTILINE,
    )
    summary = (
        f"make check ({step[1] if step else 'unknown step'}) fails on Python {python}"
    )
    if detail:
        summary += f": {detail[0].splitlines()[0].strip()}"
        if detail[1]:
            summary += f" ({detail[1]})"
    return summary[:300]


def say(message: str) -> None:
    """Report progress on standard error, keeping standard output for the notes.

    Args:
        message (str): One line.
    """
    print(f"==> {message}", file=sys.stderr)


@dataclass(frozen=True)
class Failure:
    """A ``make check`` run that failed.

    Attributes:
        python (str): The Python version it ran on.
        reason (str): One line on what failed (``failure_reason``).
    """

    python: str
    reason: str


@dataclass(frozen=True)
class State:
    """The three files ``upgrade`` changes, which must always agree.

    ``make check`` compares ``pyproject.toml`` with the holds file, so a hold
    is removed from both at once, or added to both at once.

    Attributes:
        pyproject (str): The contents of ``pyproject.toml``.
        lock (str): The contents of ``uv.lock``.
        holds (dict[str, Hold]): What ``tools/dependency-holds.toml`` records.
    """

    pyproject: str
    lock: str
    holds: dict[str, Hold]


class Project:
    """The project's files, and how to test them.

    Attributes:
        pythons (list[str]): The Python versions ``make check`` runs on.
        accepted (State): The last files that passed; they are written back
            when a change fails or anything goes wrong.
        holds (dict[str, Hold]): The holds written to the holds file now.
        attempt (int): How many times the checks have run, for log names.
    """

    def __init__(self, pythons: list[str]) -> None:
        """Remember the files as they are now.

        Args:
            pythons (list[str]): The Python versions to test on.
        """
        self.pythons = pythons
        holds = read_holds(HOLDS.read_text(encoding="utf-8")) if HOLDS.exists() else {}
        self.accepted = State(
            PYPROJECT.read_text(encoding="utf-8"),
            LOCK.read_text(encoding="utf-8"),
            holds,
        )
        self.holds = holds
        self.attempt = 0

    def write(
        self, pyproject_text: str, holds: dict[str, Hold], lock_text: str | None = None
    ) -> None:
        """Write ``pyproject.toml`` and the holds file, and ``uv.lock`` when given.

        Args:
            pyproject_text (str): The new ``pyproject.toml``.
            holds (dict[str, Hold]): The holds that ``pyproject.toml`` now has.
            lock_text (str | None): The new ``uv.lock``, or ``None`` to leave it.
        """
        PYPROJECT.write_text(pyproject_text, encoding="utf-8")
        HOLDS.write_text(render_holds(holds), encoding="utf-8")
        self.holds = holds
        if lock_text is not None:
            LOCK.write_text(lock_text, encoding="utf-8")

    def restore(self) -> None:
        """Write back the last files that passed."""
        self.write(self.accepted.pyproject, self.accepted.holds, self.accepted.lock)

    def accept(self) -> None:
        """Make the files as they are now the last ones that passed."""
        self.accepted = State(
            PYPROJECT.read_text(encoding="utf-8"),
            LOCK.read_text(encoding="utf-8"),
            dict(self.holds),
        )

    def locked(self) -> dict[str, str]:
        """Return the package versions in ``uv.lock`` now.

        Returns:
            dict[str, str]: Normalized package name to version.
        """
        return locked_versions(LOCK.read_text(encoding="utf-8"))

    def run_checks(
        self, label: str, pythons: list[str] | None = None
    ) -> Failure | None:
        """Run ``make check`` on each Python version, stopping at a failure.

        A failing run is repeated once, and only a second failure counts: a
        test that fails now and then (one that depends on timing, say) must
        not hold back a dependency that had nothing to do with it.

        Args:
            label (str): What is being tried, for the log file names.
            pythons (list[str] | None): The versions to test; all by default.

        Returns:
            Failure | None: ``None`` when every run passes, otherwise the first
            run that failed twice.
        """
        self.attempt += 1
        for python in pythons or self.pythons:
            name = f"{self.attempt:02d}-{label}-{python}"
            reason = make_check(python, WORK / f"{name}.log")
            if reason is not None:
                say(f"{label}: {reason}; running it again")
                if make_check(python, WORK / f"{name}-again.log") is not None:
                    logs = WORK.relative_to(ROOT) / f"{name}*.log"
                    say(f"{label}: failed again (logs: {logs})")
                    return Failure(python, reason)
                say(f"{label}: passed the second time, so the failure is not counted")
            say(f"{label}: make check passes on Python {python}")
        return None


def make_check(python: str, log: Path) -> str | None:
    """Run ``make check`` once on one Python version, writing its output to a log.

    The Python version gets its own environment under ``build/dependencies``,
    so the developer's ``.venv`` is left alone.

    Args:
        python (str): The Python version, such as ``"3.11"``.
        log (Path): Where to write everything ``make check`` prints.

    Returns:
        str | None: ``None`` when it passes, otherwise ``failure_reason``.
    """
    log.parent.mkdir(parents=True, exist_ok=True)
    # VIRTUAL_ENV from `uv run` names .venv, not this environment, and the
    # MAKE variables of `make deps` would turn make check into a sub-make.
    unwanted = {"VIRTUAL_ENV", "MAKELEVEL", "MAKEFLAGS", "MFLAGS"}
    environment = {k: v for k, v in os.environ.items() if k not in unwanted} | {
        "UV_PYTHON": python,
        "UV_PROJECT_ENVIRONMENT": str(WORK / f"venv-{python}"),
        # The lock was just written; make check must use it unchanged.
        "UV_LOCKED": "1",
        # Wide enough that pytest's summary lines are not cut short.
        "COLUMNS": "200",
    }
    with log.open("w", encoding="utf-8") as output:
        result = subprocess.run(
            ["make", "check"],  # noqa: S607
            cwd=ROOT,
            env=environment,
            stdout=output,
            stderr=subprocess.STDOUT,
            check=False,
        )
    if result.returncode == 0:
        return None
    return failure_reason(log.read_text(encoding="utf-8"), python)


@dataclass
class Outcome:
    """What ``upgrade`` changed, for the release notes.

    Attributes:
        before (dict[str, str]): Locked versions before the upgrade.
        after (dict[str, str]): Locked versions after it.
        direct (list[str]): The direct dependencies' names, in file order.
        holds (dict[str, Hold]): The holds now in force.
        others_failed (str | None): Why the other packages in ``uv.lock`` were
            not upgraded, or ``None`` when they were.
        pythons (list[str]): The Python versions that were tested.
    """

    before: dict[str, str]
    after: dict[str, str]
    direct: list[str]
    holds: dict[str, Hold]
    others_failed: str | None
    pythons: list[str]


def render_notes(outcome: Outcome) -> str:
    """Write the Dependencies section for ``RELEASE_NOTES.md``.

    Args:
        outcome (Outcome): What ``upgrade`` did.

    Returns:
        str: Markdown: a ``### Dependencies`` heading and a short list.
    """
    tested = ", ".join(outcome.pythons[:-1]) + f", and {outcome.pythons[-1]}"
    if len(outcome.pythons) == 1:
        tested = outcome.pythons[0]
    exception = ", except the held ones below" if outcome.holds else ""
    lines = [
        "### Dependencies\n\n",
        (
            f"Every dependency is at its newest version{exception}, tested with "
            f"`make check` on Python {tested}.\n\n"
        ),
    ]
    upgraded = [
        f"{name} {outcome.before[name]} to {outcome.after[name]}"
        for name in outcome.direct
        if outcome.before.get(name) != outcome.after.get(name)
    ]
    lines.append(
        f"- Upgraded: {', '.join(upgraded)}.\n" if upgraded else "- Upgraded: none.\n"
    )
    lines.extend(
        f"- **Held: {hold.name} {hold.held_at}.** Its newest version, {hold.failed}, "
        f"did not pass: {hold.reason}.  Held since {hold.since}; the next release "
        "tries the newest version again.\n"
        for hold in outcome.holds.values()
    )
    others = sorted(
        name
        for name in set(outcome.before) & set(outcome.after)
        if name not in outcome.direct and outcome.before[name] != outcome.after[name]
    )
    if outcome.others_failed:
        lines.append(
            "- The other packages in `uv.lock` keep their versions: upgrading them "
            f"fails: {outcome.others_failed}.\n"
        )
    elif others:
        lines.append(f"- Also upgraded in `uv.lock`: {', '.join(others)}.\n")
    return "".join(lines)


def raise_all(
    pyproject_text: str, requirements: list[Requirement], newest: dict[str, str]
) -> str:
    """Return ``pyproject.toml`` with every requirement at its newest version.

    Args:
        pyproject_text (str): The contents of ``pyproject.toml``.
        requirements (list[Requirement]): The requirements to raise.
        newest (dict[str, str]): The newest version of each dependency.

    Returns:
        str: The file with each requirement rewritten by ``Requirement.at``.
    """
    changes = {r.text: r.at(newest[r.name]) for r in requirements}
    return rewrite(
        pyproject_text, {old: new for old, new in changes.items() if old != new}
    )


def upgrade(pythons: list[str]) -> int:
    """Raise every dependency to its newest version that passes ``make check``.

    Args:
        pythons (list[str]): The Python versions to test on.

    Returns:
        int: ``0`` when done (holds included); ``1`` when ``make check`` fails
        both with every dependency at its newest version and as the files
        were, so no failure can be blamed on one dependency.
    """
    project = Project(pythons)
    start = project.accepted
    requirements = direct_requirements(start.pyproject)
    names = list(dict.fromkeys(requirement.name for requirement in requirements))
    say("Finding the newest version of each dependency")
    newest = newest_versions(start.pyproject, requirements)
    others: Failure | None = None
    try:
        say("Trying every dependency at its newest version at once")
        project.write(raise_all(start.pyproject, requirements, newest), {})
        uv("lock", "--upgrade")
        failed = project.run_checks("all")
        if failed is None:
            project.accept()
        else:
            project.restore()
            say("Checking the files as they were, to try one dependency at a time")
            before = project.run_checks("before", [failed.python])
            if before is not None:
                # Then no failure can be blamed on one dependency.  The code
                # may already expect a newer version of a held dependency.
                say(
                    "make check fails both with every dependency at its newest "
                    f"version ({failed.reason}) and as the files are "
                    f"({before.reason}); fix one and run make deps again"
                )
                return 1
            for name in names:
                _try_one(project, name, requirements, newest, start.holds)
            # Only the packages no requirement names: a hold's upper bound
            # would let `uv lock --upgrade` pick an untested held version.
            say("Trying the other packages in uv.lock at their newest versions")
            own = normalize(tomllib.loads(start.pyproject)["project"]["name"])
            transitive = sorted(set(project.locked()) - set(names) - {own})
            uv("lock", *(f"--upgrade-package={package}" for package in transitive))
            others = project.run_checks("others")
            if others is None:
                project.accept()
    finally:
        project.restore()
    outcome = Outcome(
        before=locked_versions(start.lock),
        after=project.locked(),
        direct=names,
        holds=project.accepted.holds,
        others_failed=others.reason if others else None,
        pythons=pythons,
    )
    sys.stdout.write(render_notes(outcome))
    return 0


def _try_one(
    project: Project,
    name: str,
    requirements: list[Requirement],
    newest: dict[str, str],
    old_holds: dict[str, Hold],
) -> None:
    """Raise one dependency to its newest version, or hold it if that fails.

    Either way the result becomes the project's accepted files.

    Args:
        project (Project): The files, starting from the last ones that passed.
        name (str): The dependency's normalized name.
        requirements (list[Requirement]): Every direct dependency; all of this
            one's requirements change together.
        newest (dict[str, str]): The newest version of each dependency.
        old_holds (dict[str, Hold]): The holds before this upgrade, which keep
            their ``since`` date when the same dependency is held again.
    """
    latest = newest[name]
    mine = [requirement for requirement in requirements if requirement.name == name]
    state = project.accepted
    others = {held: hold for held, hold in state.holds.items() if held != name}
    project.write(raise_all(state.pyproject, mine, newest), others)
    uv("lock", "--upgrade-package", name)
    # Raising a lower bound to the version already locked changes no package,
    # so there is nothing new to test.
    if project.locked() == locked_versions(state.lock):
        project.accept()
        return
    failed = project.run_checks(name)
    if failed is None:
        project.accept()
        return
    project.restore()
    kept = project.locked()[name]
    held = {r.text: r.held(kept, latest) for r in mine}
    previous = old_holds.get(name)
    today = dt.datetime.now(tz=dt.UTC).date().isoformat()
    hold = Hold(
        name, kept, latest, previous.since if previous else today, failed.reason
    )
    project.write(
        rewrite(state.pyproject, {old: new for old, new in held.items() if old != new}),
        others | {name: hold},
    )
    uv("lock")
    project.accept()
    say(f"Holding {name} at {kept}: {latest} fails")


def check() -> int:
    """Report whether every dependency meets the release policy.

    Returns:
        int: ``0`` when it does, ``1`` when it does not (each problem printed).
    """
    pyproject_text = PYPROJECT.read_text(encoding="utf-8")
    requirements = direct_requirements(pyproject_text)
    holds = read_holds(HOLDS.read_text(encoding="utf-8")) if HOLDS.exists() else {}
    newest = newest_versions(pyproject_text, requirements)
    version = tomllib.loads(pyproject_text)["project"]["version"]
    notes = release_notes_section(version) if holds else None
    found = problems(requirements, holds, newest, notes)
    for problem in found:
        print(f"dependencies.py: {problem}", file=sys.stderr)
    if not found:
        held = f", {len(holds)} held and named in the release notes" if holds else ""
        print(f"All {len(newest)} dependencies are at their newest versions{held}.")
    return 1 if found else 0


def main(argv: Sequence[str] | None = None) -> int:
    """Run ``check`` or ``upgrade`` from the command line.

    Args:
        argv (Sequence[str] | None): The arguments; defaults to ``sys.argv[1:]``.

    Returns:
        int: The exit status of the command: ``0`` success, ``1`` failure.
    """
    parser = argparse.ArgumentParser(
        prog="dependencies.py",
        description="Keep dependencies at their newest versions; hold back what fails.",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser(
        "check", help="fail when a dependency is not at its newest version"
    )
    upgrade_parser = commands.add_parser(
        "upgrade",
        help="raise every dependency to its newest version that passes make check",
    )
    upgrade_parser.add_argument(
        "--python",
        action="append",
        metavar="VERSION",
        help="test on this Python (repeatable; default: each one in the classifiers)",
    )
    arguments = parser.parse_args(argv)
    try:
        if arguments.command == "check":
            return check()
        pythons = arguments.python or python_versions(
            PYPROJECT.read_text(encoding="utf-8")
        )
        return upgrade(pythons)
    except (OSError, RuntimeError, ValueError) as error:
        print(f"dependencies.py: error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
