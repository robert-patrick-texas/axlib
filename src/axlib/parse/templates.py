# Copyright 2026 Robert Patrick
# SPDX-License-Identifier: Apache-2.0

r"""Find the TextFSM template for a command: operator templates, then ntc-templates.

A TextFSM template parses the output of one command on one kind of device, so
before parsing, axlib has to answer "which template parses ``sh cdp nei`` on
``cisco_ios``?".  This module answers that question from an ordered list of
template directories (see :mod:`axlib.parse.settings` for where the list comes
from).  A directory provides templates in one of two ways:

* **By file name** (the easy way for operators).  A directory without an
  ``index`` file offers each ``<platform>_<command>.textfsm`` file in it, the
  naming convention of ntc-templates: ``cisco_ios_show_ip_pim_neighbor.textfsm``
  parses ``show ip pim neighbor`` on ``cisco_ios``.  Copying an ntc template
  into the directory under the same name and fixing it overrides the original.
* **By index** (the ntc-templates way).  A directory with an ``index`` file
  uses only the rows of that file: ``Template, Hostname, Platform, Command``,
  where Platform, Hostname, and Command are regular expressions and
  ``sh[[ow]]`` accepts ``sh``, ``sho``, or ``show``.  As in
  :mod:`textfsm.clitable`, a row matches the *start* of the command and the
  first matching row wins, so longer commands must come first.  Use an index
  for commands with arguments or for a template only some hosts need.

Directories are searched in order.  In each one axlib first looks for a
template that matches the command as written, then for one whose command the
capture abbreviates word by word (``sh cdp nei`` abbreviates ``show cdp
neighbors``; ntc-templates' own index needs at least ``neig``).  The first
directory with an answer wins, so an operator's template is used however the
command was abbreviated.  An abbreviation that fits two commands in the same
directory is reported as ambiguous, as a device would.

Output filtered with ``|`` (``| include``, ``| begin``) is refused, because a
template cannot tell which lines are missing.  A trailing ``| no-more``
(Junos) changes nothing and is ignored.

Dependencies:
    Python standard library only.  Templates are run by
    :mod:`axlib.parse.snapshot`, which needs TextFSM.

CLI example:
    ``axlib parse templates -p cisco_ios sh cdp nei`` shows which template
    parses a command and where it came from.

Python example:
    >>> expand_completion("sh[[ow]] cdp")
    'sh(o(w)?)? cdp'
    >>> spell_out("sh[[ow]] c[[dp]] neig[[hbors]]")
    'show cdp neighbors'
"""

from __future__ import annotations

import argparse
import difflib
import importlib.metadata
import importlib.util
import json
import os
import re
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from axlib.credentials.settings import CONFIG_OPTION_HELP

from .errors import (
    INSTALL_HINT,
    AmbiguousCommandError,
    ParseConfigurationError,
    ParseDependencyError,
    ParseError,
    TemplateNotFoundError,
)
from .settings import TemplateSource, load_parse_settings

INDEX_FILE = "index"
TEMPLATE_SUFFIX = ".textfsm"

# "abc[[xyz]]" -> "abc(x(y(z)?)?)?", exactly as textfsm.clitable expands it.
_COMPLETION = re.compile(r"\[\[(.+?)\]\]")
# Any of these left after removing [[ ]] means the command is a real regex.
_REGEX_SYNTAX = re.compile(r"[\\^$.|?*+()\[\]{}]")
# Junos users add this to turn the pager off; it does not change the output.
_NO_MORE = re.compile(r"\s*\|\s*no-more$")
# Cisco's "do" runs an exec command from a configuration mode; its output is
# the command's own, so "do show ip int br" is parsed as "show ip int br".
_DO_PREFIX = re.compile(r"^do\s+", re.IGNORECASE)
# A plain platform name, as opposed to a pattern such as "(hp|aruba)_procurve".
_PLAIN_NAME = re.compile(r"\w+")
# Hints for platform names that ntc-templates spells differently.
_PLATFORM_HINTS = {
    "cisco_xe": "ntc-templates parses IOS-XE output with platform cisco_ios",
}


def expand_completion(pattern: str) -> str:
    """Expand ``[[...]]`` completions in an index Command into a regex.

    Args:
        pattern (str): Command column text, such as ``sh[[ow]] ver[[sion]]``.

    Returns:
        str: The pattern with each ``[[xyz]]`` replaced by ``(x(y(z)?)?)?``,
            so any leading part of the bracketed letters may be typed.

    Raises:
        None: Only string substitution is performed.
    """

    def complete(match: re.Match[str]) -> str:
        """Build the nested optional groups for one ``[[...]]`` run.

        Args:
            match (re.Match[str]): Match whose group 1 is the bracketed text.

        Returns:
            str: Nested optional groups for those letters.

        Raises:
            None: Only string joining is performed.
        """
        letters = match.group(1)
        return "(" + "(".join(letters) + ")?" * len(letters)

    return _COMPLETION.sub(complete, pattern)


def spell_out(pattern: str) -> str | None:
    """Return the full command an index Command describes, if it is plain.

    Args:
        pattern (str): Command column text.

    Returns:
        str | None: Lower-case command with ``[[``/``]]`` removed, or ``None``
            when the text also uses regular-expression syntax.

    Raises:
        None: Only string operations are performed.
    """
    text = pattern.replace("[[", "").replace("]]", "")
    if _REGEX_SYNTAX.search(text):
        return None
    return " ".join(text.lower().split())


def normalize_command(command: str) -> str:
    """Collapse whitespace and drop a leading ``do`` and a trailing ``| no-more``.

    Args:
        command (str): Command as captured.

    Returns:
        str: The command with single spaces, as it would be typed at the
            exec prompt.

    Raises:
        None: Only string operations are performed.
    """
    return _DO_PREFIX.sub("", _NO_MORE.sub("", " ".join(command.split())))


def _abbreviates(typed: Sequence[str], full: Sequence[str]) -> bool:
    """Report whether each typed word starts the matching full word.

    Args:
        typed (Sequence[str]): Lower-case words as captured.
        full (Sequence[str]): Lower-case words of a template's command.

    Returns:
        bool: ``True`` when both have the same number of words and every
            typed word is a prefix of its full word.

    Raises:
        None: Only string comparison is performed.
    """
    return len(typed) == len(full) and all(
        word.startswith(part) for part, word in zip(typed, full, strict=True)
    )


def _optional_pattern(text: str | None) -> re.Pattern[str] | None:
    """Compile an index Platform or Hostname cell, or ``None`` when it is empty.

    Args:
        text (str | None): Cell text, or ``None`` when the column is absent.

    Returns:
        re.Pattern[str] | None: Compiled pattern, or ``None`` for "any".

    Raises:
        re.error: If the text is not a valid regular expression.
    """
    return re.compile(text) if text else None


@dataclass(frozen=True, slots=True)
class IndexRow:
    """One row of an ``index`` file.

    Attributes:
        templates: Template file names; several (``a:b`` in the file) are
            merged by TextFSM on their ``Key`` values.
        command: Compiled Command pattern, matched at the start.
        spelled: The full command when the pattern is plain text.
        platform: Compiled Platform pattern, or ``None`` for any platform.
        hostname: Compiled Hostname pattern, or ``None`` for any host.
        line: Line number in the index file.
    """

    templates: tuple[str, ...]
    command: re.Pattern[str]
    spelled: str | None
    platform: re.Pattern[str] | None
    hostname: re.Pattern[str] | None
    line: int


def read_index(path: Path) -> tuple[IndexRow, ...]:
    """Read an ntc-templates style ``index`` file.

    Lines that are blank or start with ``#`` are skipped.  The first other
    line is the header and must name ``Template`` and ``Command`` columns.
    Unlike :mod:`textfsm.clitable`, which silently drops a row with the wrong
    number of fields, a bad row is an error, so a stray comma cannot quietly
    disable an operator's template.

    Args:
        path (pathlib.Path): The index file.

    Returns:
        tuple[IndexRow, ...]: Rows in file order.

    Raises:
        ParseConfigurationError: If the file cannot be read, has no valid
            header, has a row with the wrong number of fields, or has an
            invalid regular expression.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise ParseConfigurationError(f"cannot read {path}: {exc}") from exc

    header: list[str] | None = None
    rows: list[IndexRow] = []
    for number, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        fields = [cell.strip() for cell in line.split(",")]
        if header is None:
            if not {"Template", "Command"} <= set(fields):
                raise ParseConfigurationError(
                    f"{path}:{number}: the header must name Template and Command "
                    "columns, for example: Template, Hostname, Platform, Command"
                )
            header = fields
            continue
        if len(fields) != len(header):
            raise ParseConfigurationError(
                f"{path}:{number}: {len(fields)} comma-separated fields, but the "
                f"header has {len(header)}"
            )
        values = dict(zip(header, fields, strict=True))
        try:
            rows.append(
                IndexRow(
                    templates=tuple(
                        name.strip() for name in values["Template"].split(":")
                    ),
                    command=re.compile(expand_completion(values["Command"])),
                    spelled=spell_out(values["Command"]),
                    platform=_optional_pattern(values.get("Platform")),
                    hostname=_optional_pattern(values.get("Hostname")),
                    line=number,
                )
            )
        except re.error as exc:
            raise ParseConfigurationError(
                f"{path}:{number}: invalid regular expression: {exc}"
            ) from exc
    return tuple(rows)


@dataclass(frozen=True, slots=True)
class TemplateEntry:
    """A template that applies to one platform: which files parse which command.

    Attributes:
        directory: Directory holding the template files.
        origin: Where the directory was configured.
        templates: Template file names, in merge order.
        command: Pattern for the command as written.
        full_command: The command spelled out (``show cdp neighbors``), used
            for abbreviations and to pair captures in ``axlib parse diff``;
            ``None`` when it cannot be known.
    """

    directory: Path
    origin: str
    templates: tuple[str, ...]
    command: re.Pattern[str]
    full_command: str | None

    @property
    def paths(self) -> tuple[Path, ...]:
        """Return the template files as full paths.

        Args:
            None: Uses the entry's own fields.

        Returns:
            tuple[pathlib.Path, ...]: One path per template file.

        Raises:
            None: Only path joining is performed.
        """
        return tuple(self.directory / name for name in self.templates)


def _command_from_file_name(name: str, platform: str) -> str | None:
    """Spell out the command a ``<platform>_<command>.textfsm`` file parses.

    Args:
        name (str): Template file name.
        platform (str): Platform name.

    Returns:
        str | None: The command, such as ``show cdp neighbors``, or ``None``
            when the file is named for another platform.

    Raises:
        None: Only string operations are performed.
    """
    stem = name.removesuffix(TEMPLATE_SUFFIX)
    prefix = f"{platform}_"
    if not stem.startswith(prefix) or stem == prefix:
        return None
    return stem.removeprefix(prefix).replace("_", " ").lower()


@dataclass(frozen=True, slots=True)
class TemplateDirectory:
    """A searched directory and the templates it offers.

    Attributes:
        path: The directory.
        origin: Where it was configured.
        rows: Rows of its ``index`` file, or ``None`` when templates are found
            by file name.
        files: ``.textfsm`` file names, for a directory without an index.
    """

    path: Path
    origin: str
    rows: tuple[IndexRow, ...] | None = None
    files: tuple[str, ...] = ()

    @classmethod
    def load(cls, source: TemplateSource) -> TemplateDirectory | None:
        """Read a template directory and its index, if it has one.

        Args:
            source (TemplateSource): Directory and where it was configured.

        Returns:
            TemplateDirectory | None: The directory, or ``None`` when an
                optional directory does not exist.

        Raises:
            ParseConfigurationError: If a required directory is missing, the
                path is not a directory, or its index is invalid.
        """
        path = source.path
        if not path.exists():
            if not source.required:
                return None
            raise ParseConfigurationError(
                f"template directory {path} ({source.origin}) does not exist"
            )
        if not path.is_dir():
            raise ParseConfigurationError(
                f"template directory {path} ({source.origin}) is not a directory"
            )
        index = path / INDEX_FILE
        if index.is_file():
            return cls(path, source.origin, rows=read_index(index))
        files = tuple(
            sorted(
                file.name for file in path.glob(f"*{TEMPLATE_SUFFIX}") if file.is_file()
            )
        )
        return cls(path, source.origin, files=files)

    def describe(self) -> str:
        """Summarize how the directory offers templates.

        Args:
            None: Uses the directory's own fields.

        Returns:
            str: For example ``index with 1035 rows`` or ``3 templates by
                file name``.

        Raises:
            None: Only string formatting is performed.
        """
        if self.rows is not None:
            return f"index with {len(self.rows)} rows"
        count = len(self.files)
        return f"{count} template{'s' * (count != 1)} by file name"

    def entries_for(
        self, platform: str, host: str | None = None
    ) -> list[TemplateEntry]:
        """List the templates this directory offers for a platform and host.

        Args:
            platform (str): Platform name, such as ``cisco_ios``.
            host (str | None): Host name, checked against an index's Hostname
                column; ``None`` matches every row.

        Returns:
            list[TemplateEntry]: Entries in search order.

        Raises:
            None: Patterns were compiled when the directory was loaded.
        """
        if self.rows is None:
            entries = []
            for name in self.files:
                full = _command_from_file_name(name, platform)
                if full is not None:
                    # A file name gives the bare command, so it must match all
                    # of it; arguments need an index row.
                    pattern = re.compile(re.escape(full) + "$", re.IGNORECASE)
                    entries.append(
                        TemplateEntry(self.path, self.origin, (name,), pattern, full)
                    )
            return entries

        return [
            TemplateEntry(
                self.path,
                self.origin,
                row.templates,
                row.command,
                # The ntc file name spells the command out even when the index
                # pattern is a regex such as "sh[[ow]] int[[erfaces]](?: \S+)?".
                _command_from_file_name(row.templates[0], platform) or row.spelled,
            )
            for row in self.rows
            if (row.platform is None or row.platform.match(platform))
            and (host is None or row.hostname is None or row.hostname.match(host))
        ]

    def platforms(self) -> set[str]:
        """Return the plain platform names in this directory's index.

        Args:
            None: Uses the directory's index rows.

        Returns:
            set[str]: Names such as ``cisco_ios``; empty without an index.

        Raises:
            None: Only pattern text is inspected.
        """
        return {
            row.platform.pattern
            for row in self.rows or ()
            if row.platform is not None and _PLAIN_NAME.fullmatch(row.platform.pattern)
        }


@dataclass(frozen=True, slots=True)
class TemplateMatch:
    """The template chosen for one command.

    Attributes:
        entry: The template entry that matched.
        command: The command as captured, with whitespace normalized.
        platform: The platform it was matched for.
        abbreviated: ``True`` when it matched as a word-by-word abbreviation.
    """

    entry: TemplateEntry
    command: str
    platform: str
    abbreviated: bool = False

    @property
    def template_command(self) -> str:
        """Return the command spelled out, or as captured when unknown.

        Args:
            None: Uses the match's own fields.

        Returns:
            str: ``show cdp neighbors`` for ``sh cdp nei``.  Snapshots record
                this so ``axlib parse diff`` pairs differently abbreviated
                captures of the same command.

        Raises:
            None: Only attribute access is performed.
        """
        return self.entry.full_command or self.command.lower()


def _match_in(
    entries: Sequence[TemplateEntry], command: str, platform: str
) -> TemplateMatch | None:
    """Find a command's template among one directory's entries.

    Args:
        entries (Sequence[TemplateEntry]): One directory's entries, in order.
        command (str): Normalized command.
        platform (str): Platform name.

    Returns:
        TemplateMatch | None: The first entry whose pattern matches the
            command, else the single command it abbreviates, else ``None``.

    Raises:
        AmbiguousCommandError: If the abbreviation fits several commands.
    """
    for entry in entries:
        if entry.command.match(command):
            return TemplateMatch(entry, command, platform)

    typed = command.lower().split()
    candidates = [
        entry
        for entry in entries
        if entry.full_command and _abbreviates(typed, entry.full_command.split())
    ]
    spelled = list(dict.fromkeys(entry.full_command or "" for entry in candidates))
    if len(spelled) > 1:
        raise AmbiguousCommandError(command, platform, spelled)
    if candidates:
        return TemplateMatch(candidates[0], command, platform, abbreviated=True)
    return None


def bundled_template_dir(
    environ: Mapping[str, str] | None = None,
) -> TemplateSource | None:
    """Locate the ntc-templates collection without importing it.

    ``NTC_TEMPLATES_DIR`` replaces the collection, as it does for netmiko and
    ntc-templates themselves; that directory needs its own ``index``.

    Args:
        environ (Mapping[str, str] | None): Environment, for tests; defaults
            to :data:`os.environ`.

    Returns:
        TemplateSource | None: The collection's ``templates`` directory, or
            ``None`` when ntc-templates is not installed.

    Raises:
        None: A missing package is reported by returning ``None``.
    """
    environment = os.environ if environ is None else environ
    override = environment.get("NTC_TEMPLATES_DIR")
    if override:
        return TemplateSource(Path(override).expanduser(), "NTC_TEMPLATES_DIR")
    # find_spec locates a top-level package without running its __init__.
    spec = importlib.util.find_spec("ntc_templates")
    if spec is None or not spec.submodule_search_locations:
        return None
    package = Path(next(iter(spec.submodule_search_locations)))
    try:
        origin = f"ntc-templates {importlib.metadata.version('ntc-templates')}"
    except importlib.metadata.PackageNotFoundError:
        origin = "ntc-templates"
    return TemplateSource(package / "templates", origin)


@dataclass(frozen=True)
class TemplateCatalog:
    """Every searched template directory, in order, plus a default platform.

    Attributes:
        directories: Template directories in search order.
        default_platform: Platform used when a caller names none.
    """

    directories: tuple[TemplateDirectory, ...]
    default_platform: str | None = None
    # Entries per (directory, platform, host), so a directory of a thousand
    # captures does not re-filter a thousand index rows for every command.
    _entries: dict[tuple[int, str, str | None], list[TemplateEntry]] = field(
        default_factory=dict, compare=False, repr=False
    )

    @classmethod
    def load(
        cls,
        sources: Sequence[TemplateSource] = (),
        *,
        platform: str | None = None,
        include_bundled: bool = True,
        environ: Mapping[str, str] | None = None,
    ) -> TemplateCatalog:
        """Load template directories, adding ntc-templates last.

        Args:
            sources (Sequence[TemplateSource]): Operator directories in search
                order.
            platform (str | None): Default platform for :meth:`platform_for`.
            include_bundled (bool): Search the ntc-templates collection after
                the operator directories.
            environ (Mapping[str, str] | None): Environment, for tests.

        Returns:
            TemplateCatalog: The catalog.

        Raises:
            ParseConfigurationError: If a directory or index is invalid.
            ParseDependencyError: If there are no directories at all because
                ntc-templates is not installed and none were configured.
        """
        directories = [
            directory
            for source in sources
            if (directory := TemplateDirectory.load(source)) is not None
        ]
        bundled = bundled_template_dir(environ) if include_bundled else None
        if bundled is not None and (directory := TemplateDirectory.load(bundled)):
            directories.append(directory)
        if not directories:
            raise ParseDependencyError(
                "no TextFSM templates are available: install the ntc-templates "
                f"collection with {INSTALL_HINT}, or give a --template-dir"
            )
        return cls(tuple(directories), platform)

    @classmethod
    def default(
        cls,
        config_file: str | os.PathLike[str] | None = None,
        *,
        template_dirs: Sequence[str | os.PathLike[str]] = (),
        platform: str | None = None,
        environ: Mapping[str, str] | None = None,
    ) -> TemplateCatalog:
        """Load the catalog from ``--template-dir`` values and axlib settings.

        Args:
            config_file (str | os.PathLike[str] | None): ``axlib.toml`` named
                by the caller.
            template_dirs (Sequence[str | os.PathLike[str]]): Directories
                searched before every configured one.
            platform (str | None): Platform that overrides the configured one.
            environ (Mapping[str, str] | None): Environment, for tests.

        Returns:
            TemplateCatalog: Operator directories, then ntc-templates.

        Raises:
            ParseConfigurationError: If settings, a directory, or an index is
                invalid.
            ParseDependencyError: If no templates are available at all.
        """
        settings = load_parse_settings(config_file, environ=environ)
        sources = [
            TemplateSource(Path(directory).expanduser(), "--template-dir")
            for directory in template_dirs
        ]
        sources.extend(settings.template_sources)
        return cls.load(
            sources, platform=platform or settings.platform, environ=environ
        )

    def platform_for(self, platform: str | None) -> str:
        """Return the given platform, or the default one.

        Args:
            platform (str | None): Platform named by the caller.

        Returns:
            str: The platform to use.

        Raises:
            ParseConfigurationError: If neither is set.
        """
        selected = platform or self.default_platform
        if not selected:
            raise ParseConfigurationError(
                "no platform given: use --platform (for example cisco_ios), "
                "AXLIB_TEXTFSM_PLATFORM, or platform in [textfsm] of axlib.toml"
            )
        return selected

    def entries_for(
        self, directory: int, platform: str, host: str | None = None
    ) -> list[TemplateEntry]:
        """Return one directory's entries for a platform and host, cached.

        Args:
            directory (int): Position of the directory in :attr:`directories`.
            platform (str): Platform name.
            host (str | None): Host name, or ``None``.

        Returns:
            list[TemplateEntry]: The entries, in search order.

        Raises:
            IndexError: If ``directory`` is out of range.
        """
        key = (directory, platform, host)
        if key not in self._entries:
            self._entries[key] = self.directories[directory].entries_for(platform, host)
        return self._entries[key]

    def _unknown_platform(self, platform: str) -> TemplateNotFoundError:
        """Build the error for a platform that no directory knows.

        Args:
            platform (str): The unknown platform.

        Returns:
            TemplateNotFoundError: Error naming similar known platforms.

        Raises:
            None: The error is returned, not raised.
        """
        known = set().union(*(directory.platforms() for directory in self.directories))
        hint = _PLATFORM_HINTS.get(platform)
        if hint is None and (close := difflib.get_close_matches(platform, known, n=3)):
            hint = "did you mean " + " or ".join(close) + "?"
        return TemplateNotFoundError(
            f"no templates for platform {platform!r}" + (f"; {hint}" if hint else "")
        )

    def resolve(
        self, command: str, platform: str | None = None, *, host: str | None = None
    ) -> TemplateMatch:
        """Choose the template that parses a command.

        Args:
            command (str): Command as captured, possibly abbreviated.
            platform (str | None): Platform; ``None`` uses the default one.
            host (str | None): Host name, for index rows that name hosts.

        Returns:
            TemplateMatch: The template from the first directory that has one.

        Raises:
            ParseConfigurationError: If no platform is given or configured.
            TemplateNotFoundError: If the command is empty, filtered with
                ``|``, or has no template.
            AmbiguousCommandError: If an abbreviation fits several commands
                in the first directory where it fits any.
        """
        selected = self.platform_for(platform)
        normalized = normalize_command(command)
        if not normalized:
            raise TemplateNotFoundError("empty command")
        if "|" in normalized:
            raise TemplateNotFoundError(
                f"{normalized!r} has filtered output, which cannot be parsed; "
                "capture the command without '|'"
            )

        any_entries = False
        for position in range(len(self.directories)):
            entries = self.entries_for(position, selected, host)
            any_entries = any_entries or bool(entries)
            match = _match_in(entries, normalized, selected)
            if match is not None:
                return match
        if not any_entries:
            raise self._unknown_platform(selected)
        raise TemplateNotFoundError(f"no template for {normalized!r} on {selected}")


def add_template_arguments(parser: argparse.ArgumentParser) -> None:
    """Add the platform, template-directory, and configuration options.

    Args:
        parser (argparse.ArgumentParser): Parser to extend.

    Returns:
        None: Arguments are added in place.

    Raises:
        argparse.ArgumentError: If an option name is already defined.
    """
    parser.add_argument(
        "-p",
        "--platform",
        metavar="NAME",
        help="Device platform as netmiko and ntc-templates name it: cisco_ios "
        "(also IOS-XE), cisco_nxos, cisco_xr, arista_eos, juniper_junos, ... "
        "(default: $AXLIB_TEXTFSM_PLATFORM, else platform in [textfsm]).",
    )
    parser.add_argument(
        "-t",
        "--template-dir",
        action="append",
        default=[],
        metavar="DIR",
        help="Search DIR for .textfsm templates before the configured "
        "directories and ntc-templates. Repeatable.",
    )
    parser.add_argument("--config", metavar="FILE", help=CONFIG_OPTION_HELP)


def catalog_from_arguments(args: argparse.Namespace) -> TemplateCatalog:
    """Load the catalog named by the options of :func:`add_template_arguments`.

    Args:
        args (argparse.Namespace): Parsed options.

    Returns:
        TemplateCatalog: The catalog, with ``--platform`` as its default.

    Raises:
        ParseConfigurationError: If settings, a directory, or an index is
            invalid.
        ParseDependencyError: If no templates are available.
    """
    return TemplateCatalog.default(
        args.config, template_dirs=args.template_dir, platform=args.platform
    )


def _describe_match(match: TemplateMatch) -> dict[str, object]:
    """Return a JSON-ready description of a resolved template.

    Args:
        match (TemplateMatch): The resolved template.

    Returns:
        dict[str, object]: Command, platform, spelled-out command, template
            paths, origin, and whether it matched as an abbreviation.

    Raises:
        None: Only attribute access is performed.
    """
    return {
        "command": match.command,
        "platform": match.platform,
        "template_command": match.template_command,
        "templates": [str(path) for path in match.entry.paths],
        "origin": match.entry.origin,
        "abbreviated": match.abbreviated,
    }


def _print_catalog(
    catalog: TemplateCatalog, platform: str | None, *, as_json: bool
) -> None:
    """Print the search path and, for a platform, the commands it can parse.

    Args:
        catalog (TemplateCatalog): The catalog.
        platform (str | None): Platform whose commands are listed, if any.
        as_json (bool): Print one JSON object instead of text.

    Returns:
        None: Output goes to standard output.

    Raises:
        None: Entries were validated when the catalog was loaded.
    """
    directories = [
        {"path": str(item.path), "origin": item.origin, "contents": item.describe()}
        for item in catalog.directories
    ]
    commands = []
    if platform:
        for position in range(len(catalog.directories)):
            commands.extend(
                {
                    "command": entry.full_command or entry.command.pattern,
                    "templates": list(entry.templates),
                    "origin": entry.origin,
                }
                for entry in catalog.entries_for(position, platform)
            )
    if as_json:
        print(
            json.dumps(
                {
                    "directories": directories,
                    "platform": platform,
                    "commands": commands,
                },
                indent=2,
            )
        )
        return

    print("Template directories, searched in this order:")
    for number, item in enumerate(directories, start=1):
        print(f"  {number}. {item['path']}")
        print(f"     {item['origin']}; {item['contents']}")
    if platform:
        print(f"\nCommands with a template on {platform} (first one wins):")
        width = max((len(str(item["command"])) for item in commands), default=0)
        for item in commands:
            print(f"  {item['command']:<{width}}  {':'.join(item['templates'])}")


def build_arg_parser() -> argparse.ArgumentParser:
    """Build the parser for ``axlib parse templates``.

    Args:
        None: Options are defined by this module.

    Returns:
        argparse.ArgumentParser: Parser for the templates command.

    Raises:
        argparse.ArgumentError: If option names conflict.
    """
    parser = argparse.ArgumentParser(
        prog="axlib parse templates",
        description="Show the template directories, the commands a platform "
        "can parse, or which template parses one command.",
    )
    add_template_arguments(parser)
    parser.add_argument(
        "command",
        nargs="*",
        metavar="WORD",
        help="A command, such as: sh cdp nei.  Without one, list the template "
        "directories (and with --platform, every command).",
    )
    parser.add_argument("--json", action="store_true", help="Print JSON.")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run ``axlib parse templates``.

    Args:
        argv (Sequence[str] | None): Arguments excluding the program name.

    Returns:
        int: ``0`` on success, ``1`` when the command has no template or is
            ambiguous, ``2`` for configuration or dependency errors.

    Raises:
        None: Expected errors are printed and turned into the exit status.
    """
    args = build_arg_parser().parse_args(argv)
    try:
        catalog = catalog_from_arguments(args)
        if not args.command:
            _print_catalog(catalog, catalog.default_platform, as_json=args.json)
            return 0
        match = catalog.resolve(" ".join(args.command))
    except (TemplateNotFoundError, AmbiguousCommandError) as exc:
        print(f"axlib parse templates: {exc}", file=sys.stderr)
        return 1
    except ParseError as exc:
        print(f"axlib parse templates: {exc}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(_describe_match(match), indent=2))
        return 0
    how = "is an abbreviation of" if match.abbreviated else "matches"
    print(f"{match.command!r} on {match.platform} {how} {match.template_command!r}")
    for path in match.entry.paths:
        print(f"  template: {path}")
    print(f"  from:     {match.entry.origin}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
