# Copyright 2026 Robert Patrick
# SPDX-License-Identifier: Apache-2.0

"""Headless tests for the optional credential manager TUI.

Textual's ``run_test()`` runs the app without a real terminal and returns a
``Pilot`` that presses keys and clicks buttons, so each test drives the TUI
exactly as an operator would and then checks the credential store directly.
"""

from __future__ import annotations

import asyncio
import dataclasses
from collections.abc import Awaitable, Callable
from io import StringIO
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("textual", reason="the optional 'tui' extra is not installed")

from textual.pilot import Pilot
from textual.widgets import Checkbox, DataTable, Input, Select, Static

from axlib.__main__ import main as axlib_main
from axlib.credentials import (
    NETWORK_PROFILE,
    CredentialSettings,
    StoreAdmin,
    StoreKind,
    load_settings,
)
from axlib.credentials import tui as tui_module
from axlib.credentials.tui import (
    color_system_for,
    environment_problem,
    terminal_problem,
)
from axlib.credentials.tui.app import NOTE_COLUMN_WIDTH, CredentialAdminApp
from axlib.credentials.tui.screens import ConfirmScreen, RecordChange, RecordFormScreen
from axlib.credentials.tui.widgets import SecretInput

from .conftest import write_store_config

SECRET = "Hunter2-do-not-show"  # noqa: S105 - a fake secret to look for leaks
Scenario = Callable[[CredentialAdminApp, Pilot[None]], Awaitable[None]]


def run_app(app: CredentialAdminApp, scenario: Scenario) -> None:
    """Run ``scenario`` against ``app`` in a headless 100x30 terminal."""

    async def drive() -> None:
        async with app.run_test(size=(100, 30)) as pilot:
            await settle(pilot)
            await scenario(app, pilot)

    asyncio.run(drive())


async def settle(pilot: Pilot[None]) -> None:
    """Wait until background store work, and any work it started, is done."""
    for _ in range(20):
        await pilot.app.workers.wait_for_complete()
        await pilot.pause()
        if not any(worker.is_running for worker in pilot.app.workers):
            return


def screen_text(app: CredentialAdminApp) -> str:
    """Return everything currently drawn, as SVG text, for leak checks."""
    return app.export_screenshot()


def ready_settings(tmp_path: Path, *, text_file: bool = False) -> CredentialSettings:
    """Initialize SQLite (and optionally the text file) and return settings."""
    settings = load_settings(
        write_store_config(tmp_path, text_file=text_file), environ={}
    )
    for kind in (
        (StoreKind.SQLITE, StoreKind.FILE) if text_file else (StoreKind.SQLITE,)
    ):
        StoreAdmin(settings, kind).initialize(generate_key=True)
    return settings


def table_rows(app: CredentialAdminApp) -> list[list[str]]:
    """Return the record table's cells as plain strings."""
    table = app.query_one("#records", DataTable)
    return [
        [str(cell) for cell in table.get_row_at(row)] for row in range(table.row_count)
    ]


# --------------------------------------------------------------- launcher


def test_environment_problem_detects_keystroke_logging() -> None:
    assert environment_problem({}) is None
    assert environment_problem({"TEXTUAL": "headless"}) is None
    assert "TEXTUAL_LOG" in (environment_problem({"TEXTUAL_LOG": "t.log"}) or "")
    assert "devtools" in (environment_problem({"TEXTUAL": "devtools,debug"}) or "")


@pytest.mark.parametrize(
    ("environ", "expected"),
    [
        ({"COLORTERM": "truecolor", "TERM": "xterm-256color"}, "truecolor"),
        ({"COLORTERM": "24bit"}, "truecolor"),
        ({"TERM": "xterm-256color"}, "256"),  # typical over SSH
        ({"TERM": "screen-256color"}, "256"),
        ({"TERM": "xterm"}, "standard"),
        ({}, "standard"),
    ],
)
def test_color_system_follows_the_terminal(
    environ: dict[str, str], expected: str
) -> None:
    assert color_system_for(environ) == expected


def test_terminal_problem_requires_interactive_streams() -> None:
    class FakeTerminal(StringIO):
        def isatty(self) -> bool:
            return True

    assert terminal_problem(FakeTerminal(), FakeTerminal()) is None
    assert "ssh -t" in (terminal_problem(StringIO(), FakeTerminal()) or "")


def test_launcher_refuses_to_start_when_unsafe_or_impossible(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("TEXTUAL_LOG", "textual.log")
    assert tui_module.main([]) == 1
    assert "TEXTUAL_LOG" in capsys.readouterr().err

    monkeypatch.delenv("TEXTUAL_LOG")
    monkeypatch.setattr(tui_module.importlib.util, "find_spec", lambda _name: None)
    assert tui_module.main([]) == 1
    assert "uv add 'axlib[tui]'" in capsys.readouterr().err

    monkeypatch.undo()
    monkeypatch.delenv("TEXTUAL_LOG", raising=False)
    # pytest captures stdin/stdout, so they are not terminals.
    assert tui_module.main([]) == 1
    assert "interactive terminal" in capsys.readouterr().err

    with pytest.raises(SystemExit):
        tui_module.main(["--idle-timeout", "-1"])


def test_top_level_dispatcher_offers_credential_tui(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as exited:
        axlib_main(["credential-tui", "--help"])
    assert exited.value.code == 0
    assert "--idle-timeout" in capsys.readouterr().out


# ------------------------------------------------------------ application


def test_uninitialized_store_can_be_initialized(tmp_path: Path) -> None:
    settings = load_settings(write_store_config(tmp_path, text_file=False), environ={})

    async def scenario(app: CredentialAdminApp, pilot: Pilot[None]) -> None:
        assert "has not been created" in str(app.query_one("#setup", Static).render())
        await pilot.press("i")
        await pilot.press("enter")  # "Initialize" is focused and key generation ticked.
        await settle(pilot)
        assert app.store_status is not None
        assert app.store_status.ready

    run_app(CredentialAdminApp(settings, idle_minutes=0), scenario)
    assert (tmp_path / "keys" / "sqlite.key").exists()


def test_add_record_from_the_keyboard_never_shows_the_secret(tmp_path: Path) -> None:
    settings = ready_settings(tmp_path)

    async def scenario(app: CredentialAdminApp, pilot: Pilot[None]) -> None:
        await pilot.press("a")
        await pilot.press(*"j.smith", "tab", "tab")  # service, then past the profile
        await pilot.press(*"jsmith", "enter")  # Enter moves to the next box
        await pilot.press(*SECRET, "enter", *SECRET, "enter")
        assert SECRET not in screen_text(app)
        await pilot.press("enter", "enter")  # skip the optional enable secret
        await pilot.press(*"core routers", "enter")  # the note, then save
        await settle(pilot)
        assert not isinstance(app.screen, RecordFormScreen)
        assert table_rows(app)[0][:2] == ["jsmith", "netuser,netpass"]
        assert table_rows(app)[0][4] == "core routers"
        assert SECRET not in screen_text(app)

    run_app(CredentialAdminApp(settings, idle_minutes=0), scenario)
    stored = StoreAdmin(settings, StoreKind.SQLITE).store.read(
        "jsmith", ["netpass", "note"]
    )
    assert stored == {"netpass": SECRET, "note": "core routers"}


def test_edit_changes_and_clearing_removes_the_note(tmp_path: Path) -> None:
    settings = ready_settings(tmp_path)
    admin = StoreAdmin(settings, StoreKind.SQLITE)
    admin.add("ops", {"netuser": "ops", "netpass": "pw", "note": "old note"})

    async def change_note(app: CredentialAdminApp, pilot: Pilot[None]) -> None:
        assert table_rows(app)[0][4] == "old note"
        await pilot.press("enter")
        await settle(pilot)
        form = app.screen
        assert isinstance(form, RecordFormScreen)
        assert form.query_one("#note", Input).value == "old note"
        # A visible field is removed by clearing it, so it has no checkbox.
        assert not form.query("#note-remove")
        form.query_one("#note", Input).value = "new note"
        await pilot.click("#submit")
        await settle(pilot)
        assert table_rows(app)[0][4] == "new note"

    run_app(CredentialAdminApp(settings, idle_minutes=0), change_note)
    assert admin.store.read("ops", ["note", "netpass"]) == {
        "note": "new note",
        "netpass": "pw",
    }

    async def clear_note(app: CredentialAdminApp, pilot: Pilot[None]) -> None:
        await pilot.press("enter")
        await settle(pilot)
        app.screen.query_one("#note", Input).value = "  "
        await pilot.click("#submit")
        await settle(pilot)
        row = table_rows(app)[0]
        assert (row[1], row[4]) == ("netuser,netpass", "")

    run_app(CredentialAdminApp(settings, idle_minutes=0), clear_note)
    assert admin.store.read("ops", ["note"]) == {}


def test_filter_matches_notes_and_long_notes_are_shortened(tmp_path: Path) -> None:
    settings = ready_settings(tmp_path)
    admin = StoreAdmin(settings, StoreKind.SQLITE)
    admin.add("alpha", {"netuser": "a", "netpass": "pw", "note": "DC2 jump host"})
    admin.add("bravo", {"netuser": "b", "netpass": "pw", "note": "x" * 60})

    async def scenario(app: CredentialAdminApp, pilot: Pilot[None]) -> None:
        bravo_note = table_rows(app)[1][4]
        assert len(bravo_note) == NOTE_COLUMN_WIDTH
        assert bravo_note.endswith("…")
        await pilot.press("slash", *"jump")
        assert [row[0] for row in table_rows(app)] == ["alpha"]
        # The full note of the highlighted record is shown below the table.
        detail = app.query_one("#note-detail", Static)
        assert str(detail.render()) == "Note  DC2 jump host"
        await pilot.press("x")  # "jumpx" matches nothing
        assert table_rows(app) == []
        assert str(detail.render()) == ""

    run_app(CredentialAdminApp(settings, idle_minutes=0), scenario)


def test_choosing_the_infoblox_profile_swaps_the_fields(tmp_path: Path) -> None:
    settings = ready_settings(tmp_path)

    async def scenario(app: CredentialAdminApp, pilot: Pilot[None]) -> None:
        await pilot.press("a")
        form = app.screen
        form.query_one("#profile", Select).value = "infoblox"
        await pilot.pause()
        assert not form.query("#netuser")
        form.query_one("#service", Input).value = "infoblox"
        form.query_one("#ibgrid", Input).value = "gm.example.net"
        form.query_one("#ibuser", Input).value = "api"
        form.query_one("#ibpass", Input).value = SECRET
        form.query_one("#ibpass-confirm", Input).value = SECRET
        await pilot.click("#submit")
        await settle(pilot)
        assert table_rows(app)[0][:2] == ["infoblox", "ibgrid,ibuser,ibpass"]

    run_app(CredentialAdminApp(settings, idle_minutes=0), scenario)
    stored = StoreAdmin(settings, StoreKind.SQLITE).store.read("infoblox", ["ibpass"])
    assert stored == {"ibpass": SECRET}


def test_mismatched_passwords_keep_the_form_open(tmp_path: Path) -> None:
    settings = ready_settings(tmp_path)

    async def scenario(app: CredentialAdminApp, pilot: Pilot[None]) -> None:
        await pilot.press("a")
        form = app.screen
        form.query_one("#service", Input).value = "ops"
        form.query_one("#netuser", Input).value = "ops"
        form.query_one("#netpass", Input).value = "one"
        form.query_one("#netpass-confirm", Input).value = "two"
        await pilot.click("#submit")
        await pilot.pause()
        assert app.screen is form
        assert "differ" in str(form.query_one("#error", Static).render())

    run_app(CredentialAdminApp(settings, idle_minutes=0), scenario)
    assert StoreAdmin(settings, StoreKind.SQLITE).list_records() == []


def test_edit_prefills_username_and_removes_optional_field(tmp_path: Path) -> None:
    settings = ready_settings(tmp_path)
    admin = StoreAdmin(settings, StoreKind.SQLITE)
    admin.add("ops", {"netuser": "ops-login", "netpass": "old", "netenable": "en"})

    async def scenario(app: CredentialAdminApp, pilot: Pilot[None]) -> None:
        await pilot.press("enter")  # Enter on a row opens the edit form.
        await settle(pilot)
        form = app.screen
        assert isinstance(form, RecordFormScreen)
        assert form.query_one("#netuser", Input).value == "ops-login"
        assert form.query_one("#netpass", Input).value == ""
        form.query_one("#netpass", Input).value = SECRET
        form.query_one("#netpass-confirm", Input).value = SECRET
        form.query_one("#netenable-remove", Checkbox).value = True
        await pilot.click("#submit")
        await settle(pilot)
        assert table_rows(app)[0][1] == "netuser,netpass"

    run_app(CredentialAdminApp(settings, idle_minutes=0), scenario)
    assert admin.store.read("ops", ["netuser", "netpass", "netenable"]) == {
        "netuser": "ops-login",
        "netpass": SECRET,
    }


def test_delete_requires_typing_the_service_name(tmp_path: Path) -> None:
    settings = ready_settings(tmp_path)
    admin = StoreAdmin(settings, StoreKind.SQLITE)
    admin.add("ops", {"netuser": "ops", "netpass": "pw"})

    async def scenario(app: CredentialAdminApp, pilot: Pilot[None]) -> None:
        await pilot.press("d")
        assert isinstance(app.screen, ConfirmScreen)
        await pilot.press(*"opz", "enter")
        assert isinstance(app.screen, ConfirmScreen)
        await pilot.press("backspace", "s", "enter")
        await settle(pilot)
        assert table_rows(app) == []

    run_app(CredentialAdminApp(settings, idle_minutes=0), scenario)
    assert admin.list_records() == []


def test_rotate_key_needs_a_key_file_and_confirmation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = ready_settings(tmp_path)
    key_file = tmp_path / "keys" / "sqlite.key"
    old_key = key_file.read_text()

    async def scenario(app: CredentialAdminApp, pilot: Pilot[None]) -> None:
        await pilot.press("k", *"rotate", "enter")
        await settle(pilot)

    run_app(CredentialAdminApp(settings, idle_minutes=0), scenario)
    assert key_file.read_text() != old_key

    # The same key injected through the environment opens the store, but
    # rotation is refused because axlib cannot rewrite the caller's environment.
    env_settings = dataclasses.replace(
        settings, sqlite_key=key_file.read_text().strip()
    )
    messages: list[str] = []

    async def refused(app: CredentialAdminApp, pilot: Pilot[None]) -> None:
        def record(message: str, **_kwargs: Any) -> None:
            messages.append(message)

        monkeypatch.setattr(app, "notify", record)
        assert app.store_status is not None
        assert app.store_status.key_source == "environment"
        await pilot.press("k")
        assert not isinstance(app.screen, ConfirmScreen)

    run_app(CredentialAdminApp(env_settings, idle_minutes=0), refused)
    assert any("needs a key file" in message for message in messages)


def test_switching_stores_shows_which_fields_sqlite_overrides(tmp_path: Path) -> None:
    settings = ready_settings(tmp_path, text_file=True)
    StoreAdmin(settings, StoreKind.SQLITE).add("ops", {"netuser": "a", "netpass": "b"})
    StoreAdmin(settings, StoreKind.FILE).add("ops", {"netuser": "a", "netpass": "c"})

    async def scenario(app: CredentialAdminApp, pilot: Pilot[None]) -> None:
        assert app.admin.kind is StoreKind.SQLITE
        await pilot.press("s")
        await settle(pilot)
        assert app.admin.kind is StoreKind.FILE
        assert table_rows(app)[0][3] == "SQLite overrides netpass,netuser"

    run_app(CredentialAdminApp(settings, idle_minutes=0), scenario)


def test_filter_narrows_the_table(tmp_path: Path) -> None:
    settings = ready_settings(tmp_path)
    admin = StoreAdmin(settings, StoreKind.SQLITE)
    for service in ("alpha", "bravo", "charlie"):
        admin.add(service, {"netuser": service, "netpass": "pw"})

    async def scenario(app: CredentialAdminApp, pilot: Pilot[None]) -> None:
        await pilot.press("slash", *"rav")
        assert [row[0] for row in table_rows(app)] == ["bravo"]
        await pilot.press("escape")
        assert app.focused is app.query_one("#records", DataTable)

    run_app(CredentialAdminApp(settings, idle_minutes=0), scenario)


def test_idle_timeout_closes_the_app(tmp_path: Path) -> None:
    settings = ready_settings(tmp_path)

    async def scenario(app: CredentialAdminApp, pilot: Pilot[None]) -> None:
        await pilot.pause(0.5)
        assert app.return_code == 0

    # 0.002 minutes is about a tenth of a second.
    run_app(CredentialAdminApp(settings, idle_minutes=0.002), scenario)


def test_secret_input_refuses_to_copy_or_cut(tmp_path: Path) -> None:
    settings = ready_settings(tmp_path)

    async def scenario(app: CredentialAdminApp, pilot: Pilot[None]) -> None:
        await pilot.press("a")
        secret = app.screen.query_one("#netpass", SecretInput)
        secret.value = SECRET
        secret.select_all()
        secret.action_copy()
        secret.action_cut()
        assert app.clipboard != SECRET
        assert secret.value == SECRET

    run_app(CredentialAdminApp(settings, idle_minutes=0), scenario)


def test_record_change_repr_omits_values() -> None:
    change = RecordChange(
        service="ops",
        profile=NETWORK_PROFILE,
        creating=True,
        values={"netpass": SECRET},
    )
    assert SECRET not in repr(change)
