# Copyright 2026 Robert Patrick
# SPDX-License-Identifier: Apache-2.0

"""Tests for the netenv-set and netenv-clear shell-session commands."""

from __future__ import annotations

import os
import pwd
import re
import shutil
import subprocess
import tomllib
from collections.abc import Mapping, Sequence
from io import StringIO
from pathlib import Path

import pytest

from axlib.__main__ import main as axlib_main
from axlib.credentials import (
    CredentialError,
    StoreAdmin,
    StoreKind,
    load_settings,
    netenv,
)
from axlib.credentials.netenv import (
    EXIT_ERROR,
    EXIT_EXPORTED,
    EXIT_NOT_FOUND,
    EXIT_TERMINAL,
    clear_main,
    lookup_network_record,
    operator_service,
    render_exports,
    render_unset,
    set_main,
)

from .conftest import write_store_config

PROJECT_ROOT = Path(__file__).resolve().parents[1]
BASH = shutil.which("bash")

# Everything eval might mistake for code: quotes, command substitution,
# backticks, a separator, a newline, and whitespace that must be preserved.
HOSTILE_PASSWORD = " p'a\"ss $(touch PWNED) `touch PWNED`; exit 1\nline2 "  # noqa: S105 - test data


class TerminalStream(StringIO):
    """A StringIO that claims to be an interactive terminal."""

    def isatty(self) -> bool:
        return True


class FakeStore:
    """StoreProvider test double holding records by normalized service name."""

    def __init__(self, records: Mapping[str, Mapping[str, str]]) -> None:
        self.records = records

    def read(self, service: str, fields: Sequence[str]) -> dict[str, str]:
        record = self.records.get(service, {})
        return {name: record[name] for name in fields if name in record}


class BrokenStore:
    """StoreProvider test double whose reads always fail."""

    def read(self, service: str, fields: Sequence[str]) -> dict[str, str]:
        raise CredentialError("key file is unreadable")


@pytest.fixture
def config(tmp_path: Path) -> Path:
    """A SQLite-backed config holding an operator record and a shared record."""
    path = write_store_config(tmp_path, text_file=False)
    admin = StoreAdmin(load_settings(path, environ={}), StoreKind.SQLITE)
    admin.initialize(generate_key=True)
    admin.add(
        "first.last",
        {"netuser": "first.last", "netpass": HOSTILE_PASSWORD, "netenable": "en"},
    )
    admin.add("lab.user", {"netuser": "lab", "netpass": "labpass"})
    admin.add("network-shared", {"netuser": "shared", "netpass": "sharedpass"})
    return path


def _run_set(
    config: Path, *args: str, user: str = "first.last"
) -> tuple[int, str, str]:
    out, err = StringIO(), StringIO()
    status = set_main(
        ["--config", str(config), *args],
        environ={"USER": user},
        stdout=out,
        stderr=err,
    )
    return status, out.getvalue(), err.getvalue()


def _eval_in_bash(code: str, tmp_path: Path) -> dict[str, str]:
    """Eval ``code`` in a clean bash and return the resulting NET* variables."""
    assert BASH is not None
    script = (
        'eval "$1"; for v in NETUSER NETPASS NETENABLE; do '
        'if [ -n "${!v+x}" ]; then printf "%s=%s\\0" "$v" "${!v}"; fi; done'
    )
    result = subprocess.run(  # noqa: S603 - fixed interpreter and script
        [BASH, "-c", script, "bash", code],
        cwd=tmp_path,
        env={"PATH": os.environ["PATH"], "NETENABLE": "stale"},
        capture_output=True,
        text=True,
        check=True,
    )
    return dict(item.split("=", 1) for item in result.stdout.split("\0") if item)


def test_render_exports_quotes_and_unsets_empty_fields() -> None:
    code = render_exports({"netuser": "ops", "netpass": "a b$c", "netenable": None})
    assert code.splitlines() == [
        "export NETUSER=ops",
        "export NETPASS='a b$c'",
        "unset NETENABLE",
    ]
    assert render_unset() == "unset NETUSER NETPASS NETENABLE"


def test_lookup_ignores_values_already_in_the_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("NETUSER", "stale-user")
    monkeypatch.setenv("NETPASS", "stale-pass")
    settings = load_settings(environ={})

    result = lookup_network_record(
        "first.last", settings=settings, store=FakeStore({}), reporter=print
    )

    assert not result.found
    assert result.missing == ("netuser", "netpass")


def test_lookup_keeps_backend_problems_and_hides_values_from_repr() -> None:
    settings = load_settings(environ={})
    seen: list[str] = []

    broken = lookup_network_record(
        "ops", settings=settings, store=BrokenStore(), reporter=seen.append
    )
    found = lookup_network_record(
        "first.last",
        settings=settings,
        store=FakeStore({"firstlast": {"netuser": "u", "netpass": "secret-pw"}}),
    )

    assert broken.problems == tuple(seen)
    assert "unreadable" in seen[0]
    assert found.found
    assert "secret-pw" not in repr(found)


def test_operator_service_prefers_user_like_getkeys() -> None:
    account = pwd.getpwuid(os.getuid()).pw_name

    assert operator_service({"USER": account}) == (account, None)
    assert operator_service({}) == (
        account,
        f"USER is not set; using login account {account!r}.",
    )
    other, warning = operator_service({"USER": "someone-else"})
    assert other == "someone-else"
    assert warning is not None
    assert "differs" in warning


def test_operator_service_without_any_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def no_entry(uid: int) -> pwd.struct_passwd:
        raise KeyError(uid)

    monkeypatch.setattr(netenv.pwd, "getpwuid", no_entry)

    assert operator_service({"USER": "ops"}) == ("ops", None)
    with pytest.raises(CredentialError):
        operator_service({"USER": " "})


@pytest.mark.skipif(BASH is None, reason="bash is not installed")
def test_set_output_round_trips_through_bash_eval(config: Path, tmp_path: Path) -> None:
    status, out, err = _run_set(config)

    variables = _eval_in_bash(out, tmp_path)

    assert status == EXIT_EXPORTED
    assert variables == {
        "NETUSER": "first.last",
        "NETPASS": HOSTILE_PASSWORD,
        "NETENABLE": "en",
    }
    assert not (tmp_path / "PWNED").exists()
    assert HOSTILE_PASSWORD not in err
    assert "exported NETUSER, NETPASS, NETENABLE" in err


@pytest.mark.skipif(BASH is None, reason="bash is not installed")
def test_record_without_enable_unsets_a_stale_netenable(
    config: Path, tmp_path: Path
) -> None:
    status, out, _ = _run_set(config, "--service", "lab.user", "--quiet")

    assert status == EXIT_EXPORTED
    assert _eval_in_bash(out, tmp_path) == {"NETUSER": "lab", "NETPASS": "labpass"}


def test_missing_record_prints_nothing_and_does_not_use_shared(
    config: Path,
) -> None:
    status, out, err = _run_set(config, user="new.hire")

    assert status == EXIT_NOT_FOUND
    assert out == ""
    assert "was not found" in err
    assert "shared" not in err


def test_quiet_missing_record_is_silent(config: Path) -> None:
    status = _run_set(config, "--quiet", "--service", "new.hire")
    assert status == (EXIT_NOT_FOUND, "", "")


def test_allow_shared_falls_back_with_a_warning(config: Path) -> None:
    status, out, err = _run_set(config, "--allow-shared", "-q", user="new.hire")

    assert status == EXIT_EXPORTED
    assert "export NETUSER=shared" in out
    assert "using shared service 'network-shared'" in err


def test_check_reports_presence_on_stderr_only(config: Path) -> None:
    status, out, err = _run_set(config, "--check")

    assert status == EXIT_EXPORTED
    assert out == ""
    assert "NETPASS=set" in err
    assert "NETENABLE=set" in err
    assert HOSTILE_PASSWORD not in err


def test_set_refuses_to_print_secrets_to_a_terminal(config: Path) -> None:
    out, err = TerminalStream(), StringIO()

    status = set_main(
        ["--config", str(config)],
        environ={"USER": "first.last"},
        stdout=out,
        stderr=err,
    )

    assert status == EXIT_TERMINAL
    assert out.getvalue() == ""
    assert 'eval "$(axlib netenv-set)"' in err.getvalue()


def test_set_without_an_enabled_store_is_a_configuration_error() -> None:
    out, err = StringIO(), StringIO()

    status = set_main([], environ={"USER": "ops"}, stdout=out, stderr=err)

    assert status == EXIT_ERROR
    assert out.getvalue() == ""
    assert "AXLIB_CONFIG_FILE" in err.getvalue()


def test_missing_config_file_is_a_configuration_error(tmp_path: Path) -> None:
    status, out, err = _run_set(tmp_path / "absent.toml")

    assert (status, out) == (EXIT_ERROR, "")
    assert "does not exist" in err


def test_unreadable_store_is_an_error_not_a_miss(
    config: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken_lookup(service: str, **kwargs: object) -> netenv.NetworkLookup:
        return netenv.NetworkLookup(
            service, dict.fromkeys(netenv.ENV_VARIABLES), problems=("bad key",)
        )

    monkeypatch.setattr(netenv, "lookup_network_record", broken_lookup)

    status, out, err = _run_set(config, "--quiet")

    assert (status, out) == (EXIT_ERROR, "")
    assert "could not be read" in err


def test_clear_prints_unset_and_refuses_a_terminal() -> None:
    out = StringIO()
    assert clear_main([], stdout=out) == EXIT_EXPORTED
    assert out.getvalue() == "unset NETUSER NETPASS NETENABLE\n"

    err = StringIO()
    assert clear_main([], stdout=TerminalStream(), stderr=err) == EXIT_TERMINAL
    assert "netenv-clear" in err.getvalue()


def test_dispatchers_route_both_commands(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert axlib_main(["netenv-clear"]) == EXIT_EXPORTED
    assert capsys.readouterr().out == "unset NETUSER NETPASS NETENABLE\n"

    assert netenv.main(["clear"]) == EXIT_EXPORTED
    assert capsys.readouterr().out.startswith("unset ")

    with pytest.raises(SystemExit) as exc:
        netenv.main(["bogus"])
    assert exc.value.code == EXIT_ERROR


@pytest.mark.parametrize("action", ["set", "clear"])
def test_uv_launchers_point_at_this_checkout(action: str) -> None:
    script = PROJECT_ROOT / "scripts" / f"netenv-{action}"
    text = script.read_text(encoding="utf-8")
    block = re.search(r"^# /// script\n(.*?)^# ///$", text, re.MULTILINE | re.DOTALL)

    assert text.startswith("#!/usr/bin/env -S uv run --quiet --script\n")
    assert os.access(script, os.X_OK)
    assert block is not None
    metadata = tomllib.loads(
        "\n".join(
            line.removeprefix("#").removeprefix(" ")
            for line in block.group(1).splitlines()
        )
    )
    source = metadata["tool"]["uv"]["sources"]["axlib"]["path"]
    assert (script.parent / source).resolve() == PROJECT_ROOT
    assert f"{action}_main" in text
