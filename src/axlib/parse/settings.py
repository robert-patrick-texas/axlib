# Copyright 2026 Robert Patrick
# SPDX-License-Identifier: Apache-2.0

"""Settings for ``axlib parse``: the default platform and template directories.

Operators add their own TextFSM templates by putting ``.textfsm`` files in a
directory that axlib searches before the ntc-templates collection.  Template
directories come from four places, searched in this order, and the first
template that matches a command wins:

1. ``--template-dir DIR`` on the command line (repeatable).
2. ``AXLIB_TEXTFSM_TEMPLATES``, a list of directories separated by ``:``.
3. ``template_dirs`` in the ``[textfsm]`` table of ``axlib.toml``.  When the
   key is absent, a ``textfsm`` directory beside ``axlib.toml`` is used if it
   exists; ``install.sh --with-textfsm`` creates ``/etc/axlib/textfsm``.
4. The ntc-templates collection (or ``NTC_TEMPLATES_DIR``, as in netmiko).

The sources add up rather than replace each other, so an operator's personal
directory never hides the team's shared one.  The platform (``cisco_ios``,
``cisco_nxos``, ``arista_eos``, ... as netmiko names them) comes from
``--platform``, else ``AXLIB_TEXTFSM_PLATFORM``, else ``platform`` in
``[textfsm]``.

``axlib.toml`` is found the same way as for every axlib command: ``--config``,
else ``AXLIB_CONFIG_FILE``, else the file recorded by ``install.sh``.  Parsing
needs no secrets, so a discovered file that is missing or unreadable (an
operator outside the store's group) is skipped; one named with ``--config``
must exist.

Dependencies:
    Python standard library only.

Example::

    [textfsm]
    platform = "cisco_ios"
    template_dirs = ["textfsm", "/opt/netops/textfsm"]
"""

from __future__ import annotations

import os
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from axlib.credentials.exceptions import CredentialConfigurationError
from axlib.credentials.settings import default_config_file, optional_path

from .errors import ParseConfigurationError

ENV_PLATFORM = "AXLIB_TEXTFSM_PLATFORM"
ENV_TEMPLATES = "AXLIB_TEXTFSM_TEMPLATES"
TOML_TABLE = "textfsm"
# Used when [textfsm] has no template_dirs: a directory beside axlib.toml.
DEFAULT_TEMPLATE_DIR = "textfsm"


@dataclass(frozen=True, slots=True)
class TemplateSource:
    """A directory of operator templates and where it was configured.

    Attributes:
        path: The directory.
        origin: Where it came from, such as ``--template-dir``; shown by
            ``axlib parse templates`` so operators can see why it is searched.
        required: Whether a missing directory is an error.  Only the default
            directory beside ``axlib.toml`` is optional.
    """

    path: Path
    origin: str
    required: bool = True


@dataclass(frozen=True, slots=True)
class ParseSettings:
    """Platform and template directories from the environment and TOML.

    Attributes:
        platform: Default platform, or ``None`` when none is configured.
        template_sources: Operator template directories, in search order.
        config_file: The ``axlib.toml`` that was read, or ``None``.
    """

    platform: str | None = None
    template_sources: tuple[TemplateSource, ...] = ()
    config_file: Path | None = None


def _read_config(path: Path, *, required: bool) -> dict[str, Any]:
    """Read ``axlib.toml``, tolerating a discovered file that cannot be read.

    Args:
        path (pathlib.Path): Configuration file.
        required (bool): ``True`` when the operator named the file with
            ``--config``, so it must exist and be readable.

    Returns:
        dict[str, Any]: Parsed TOML, or ``{}`` for an optional file that is
            missing or unreadable.

    Raises:
        ParseConfigurationError: If a required file cannot be read, or any
            file that was read is not valid TOML.
    """
    try:
        with path.open("rb") as handle:
            return tomllib.load(handle)
    except OSError as exc:
        if required:
            raise ParseConfigurationError(
                f"cannot read configuration file {path}: {exc}"
            ) from exc
        return {}
    except tomllib.TOMLDecodeError as exc:
        raise ParseConfigurationError(f"invalid TOML in {path}: {exc}") from exc


def _textfsm_table(data: Mapping[str, Any], path: Path) -> Mapping[str, Any]:
    """Return the ``[textfsm]`` table, checking its value types.

    Args:
        data (Mapping[str, Any]): Parsed ``axlib.toml``.
        path (pathlib.Path): The file, for error messages.

    Returns:
        Mapping[str, Any]: The table, or ``{}`` when it is absent.

    Raises:
        ParseConfigurationError: If the table, ``platform``, or
            ``template_dirs`` has the wrong type.
    """
    table = data.get(TOML_TABLE, {})
    if not isinstance(table, Mapping):
        raise ParseConfigurationError(f"[{TOML_TABLE}] in {path} must be a table")
    if not isinstance(table.get("platform", ""), str):
        raise ParseConfigurationError(f"[{TOML_TABLE}] platform in {path} must be text")
    directories = table.get("template_dirs", [])
    if not isinstance(directories, list) or not all(
        isinstance(item, str) for item in directories
    ):
        raise ParseConfigurationError(
            f"[{TOML_TABLE}] template_dirs in {path} must be a list of paths"
        )
    return table


def load_parse_settings(
    config_file: str | os.PathLike[str] | None = None,
    *,
    environ: Mapping[str, str] | None = None,
) -> ParseSettings:
    """Load the default platform and operator template directories.

    Args:
        config_file (str | os.PathLike[str] | None): ``axlib.toml`` named by
            the caller (``--config``); it must exist.  When ``None``, the file
            from ``AXLIB_CONFIG_FILE`` or the install record is read if it can
            be.
        environ (Mapping[str, str] | None): Environment, for tests; defaults
            to :data:`os.environ`.

    Returns:
        ParseSettings: Settings with directories in search order (environment
            first, then TOML).  ``--template-dir`` directories are added by the
            caller in front of these.

    Raises:
        ParseConfigurationError: If a named file is missing, a file is not
            valid TOML, ``[textfsm]`` has the wrong types, or the install
            record is malformed.
    """
    environment = os.environ if environ is None else environ
    explicit = optional_path(config_file)
    try:
        selected = explicit or default_config_file(environment)
    except CredentialConfigurationError as exc:
        raise ParseConfigurationError(str(exc)) from exc

    sources = [
        TemplateSource(Path(entry).expanduser(), ENV_TEMPLATES)
        for entry in environment.get(ENV_TEMPLATES, "").split(os.pathsep)
        if entry.strip()
    ]
    platform = (environment.get(ENV_PLATFORM) or "").strip() or None
    if selected is None:
        return ParseSettings(platform, tuple(sources), None)

    table = _textfsm_table(
        _read_config(selected, required=explicit is not None), selected
    )
    platform = platform or (table.get("platform") or "").strip() or None
    # Relative directories are resolved beside axlib.toml, like key files, so
    # cron jobs find them whatever their working directory is.
    base = selected.expanduser().resolve(strict=False).parent
    origin = f"template_dirs in {selected}"
    if "template_dirs" in table:
        sources.extend(
            TemplateSource(base / Path(entry).expanduser(), origin)
            for entry in table["template_dirs"]
        )
    else:
        sources.append(
            TemplateSource(
                base / DEFAULT_TEMPLATE_DIR,
                f"default beside {selected}",
                required=False,
            )
        )
    return ParseSettings(platform, tuple(sources), selected)
