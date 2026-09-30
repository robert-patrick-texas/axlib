# Copyright 2026 Robert Patrick
# SPDX-License-Identifier: Apache-2.0

"""Static checks for the shell scripts that install and maintain a shared host.

``install.sh`` needs root and a network, so it is exercised end to end in a
container rather than here; these tests catch syntax errors and the common
shell mistakes that shellcheck reports.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = [ROOT / "install.sh", ROOT / "scripts" / "axuv"]


@pytest.mark.parametrize("script", SCRIPTS, ids=lambda path: path.name)
def test_shell_script_syntax(script: Path) -> None:
    subprocess.run(["bash", "-n", str(script)], check=True)  # noqa: S603, S607


@pytest.mark.parametrize("script", SCRIPTS, ids=lambda path: path.name)
def test_shell_script_passes_shellcheck(script: Path) -> None:
    shellcheck = shutil.which("shellcheck")
    if shellcheck is None:
        pytest.skip("shellcheck is not installed")
    result = subprocess.run(  # noqa: S603
        [shellcheck, str(script)], capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stdout


@pytest.mark.skipif(os.geteuid() == 0, reason="checks the non-root refusal")
def test_axuv_refuses_to_run_without_root() -> None:
    result = subprocess.run(  # noqa: S603
        [str(ROOT / "scripts" / "axuv"), "tree"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 1
    assert "run as root" in result.stderr
