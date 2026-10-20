# Copyright 2026 Robert Patrick
# SPDX-License-Identifier: Apache-2.0

"""Shared fixtures for credential administration tests."""

from __future__ import annotations

import grp
import os
from pathlib import Path

import pytest

from axlib.credentials import CredentialSettings, load_settings
from axlib.credentials import settings as settings_module


def pytest_configure(config: pytest.Config) -> None:
    """Hide any real install.sh record before test modules are imported.

    ``axlib.config`` loads settings when it is first imported, so this runs as
    a configure hook (before collection) rather than as a fixture.  Tests of
    the record itself point ``INSTALL_RECORD`` at their own temporary file.
    """
    settings_module.INSTALL_RECORD = Path("/nonexistent/axlib-tests/install.env")


def write_store_config(
    directory: Path,
    *,
    sqlite: bool = True,
    text_file: bool = True,
    shared_service: str | None = "network-shared",
) -> Path:
    """Write an axlib TOML file with temporary stores under ``directory``.

    The current user's primary group is used so file-ownership checks pass
    without root privileges.
    """
    group = grp.getgrgid(os.getgid()).gr_name
    sections = []
    if shared_service is not None:
        sections.append(f'[credentials]\nshared_service = "{shared_service}"\n')
    if sqlite:
        sections.append(
            "[sqlite]\nenabled = true\n"
            'database = "credentials.db"\nkey_file = "keys/sqlite.key"\n'
            f'timeout = 1.0\ngroup = "{group}"\n'
        )
    if text_file:
        sections.append(
            "[credential_file]\nenabled = true\n"
            'file = "credentials.axc"\nkey_file = "keys/file.key"\n'
            f'lock_timeout = 1.0\ngroup = "{group}"\n'
        )
    config = directory / "axlib.toml"
    config.write_text("\n".join(sections), encoding="utf-8")
    return config


@pytest.fixture
def store_settings(tmp_path: Path) -> CredentialSettings:
    """Settings for uninitialized SQLite and text-file stores in ``tmp_path``."""
    return load_settings(write_store_config(tmp_path), environ={})


@pytest.fixture
def clean_parse_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """Hide the developer's own axlib parse settings from a test.

    ``axlib parse`` reads its platform and template directories from these
    variables and from the configuration file ``AXLIB_CONFIG_FILE`` names, so
    tests that rely on the defaults must not see a real setup.
    """
    for name in (
        "AXLIB_CONFIG_FILE",
        "AXLIB_TEXTFSM_PLATFORM",
        "AXLIB_TEXTFSM_TEMPLATES",
        "NTC_TEMPLATES_DIR",
    ):
        monkeypatch.delenv(name, raising=False)
