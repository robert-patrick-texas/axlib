# Copyright 2026 Robert Patrick
# SPDX-License-Identifier: Apache-2.0

"""The axlib credential manager: a terminal UI over the Python admin API.

This is the "easy button" for operators who run automation scripts built on
``netuser, netpass, netenable = ax.getkeys()`` but do not write Python
themselves.  It lists the services in one encrypted store and lets the operator
add, edit, and delete them, initialize a new store, and rotate its key -- all
through :class:`~axlib.credentials.admin.StoreAdmin`, the same API the
command-line tools use, so the rules cannot differ between them.

How the pieces fit together:

* ``compose()`` builds the screen: a bordered frame holding a status line, a
  filter box, the record table (or setup guidance), and a key-hint footer.
* Key bindings call ``action_*`` methods.  Actions that change data open a
  dialog from :mod:`.screens`; the dialog returns a result to a callback.
* Store work runs in a background thread (:meth:`_run_in_background`) so the
  screen stays responsive while SQLite or a file lock is busy, and results are
  handed back to the UI thread with ``call_from_thread``.

Safety measures specific to a terminal UI:

* Secret values are never displayed; the table shows field *names* only.
* No exception may escape code that handles secrets, because Textual's crash
  report prints local variables.  Background work reports errors as messages.
* The app closes itself after a period without keyboard or mouse input, in case
  a session is left open on a shared jump host.

Dependencies:
    ``textual`` (the optional ``tui`` extra: ``uv add 'axlib[tui]'``).

Example:
    Start the manager from Python rather than the ``axlib credential-tui``
    command::

        from axlib.credentials import load_settings
        from axlib.credentials.tui.app import CredentialAdminApp

        CredentialAdminApp(load_settings("/etc/axlib/axlib.toml")).run()
"""

from __future__ import annotations

import time
from collections.abc import Callable
from functools import partial
from typing import ClassVar, TypeVar

from rich.text import Text
from textual import events
from textual.app import App, ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Vertical
from textual.markup import escape
from textual.widgets import ContentSwitcher, DataTable, Footer, Input, Static

from axlib.credentials.admin import (
    LOOKUP_ORDER,
    AnnotatedRecord,
    ChangeResult,
    StoreAdmin,
    StoreKind,
    StoreStatus,
    configured_kinds,
)
from axlib.credentials.exceptions import CredentialError
from axlib.credentials.settings import CredentialSettings
from axlib.credentials.tui.screens import (
    ConfirmScreen,
    InitializeScreen,
    RecordChange,
    RecordFormScreen,
)
from axlib.credentials.tui.theme import (
    AXLIB_DARK,
    BLUE,
    GREEN,
    NOTE_COLORS,
    WHITE,
)

ResultT = TypeVar("ResultT")

DEFAULT_IDLE_MINUTES = 5
IDLE_CHECK_SECONDS = 15.0


def short_date(value: str) -> str:
    """Keep only the date of a stored ISO-8601 UTC timestamp.

    The table is narrow on an 80-column terminal; ``axlib credential-db list``
    still shows complete timestamps when the time of day matters.

    Args:
        value (str): Timestamp such as ``2026-09-20T14:02:11Z``.

    Returns:
        str: ``2026-09-20``; text without a ``T`` separator is unchanged.

    Raises:
        None: Only string operations are performed.
    """
    return value.partition("T")[0]


def plural(count: int | None, noun: str) -> str:
    """Format a count with a correctly pluralized noun.

    Args:
        count (int | None): Number of items; ``None`` is shown as ``0``.
        noun (str): Singular noun, such as ``"record"``.

    Returns:
        str: For example ``"1 record"`` or ``"3 records"``.

    Raises:
        None: Only string formatting is performed.
    """
    number = count or 0
    return f"{number} {noun}{'' if number == 1 else 's'}"


class CredentialAdminApp(App[None]):
    """Browse and maintain the records of one credential store at a time."""

    TITLE = "axlib credential manager"
    # The command palette offers generic Textual commands (themes, screenshots)
    # that an operator does not need; turning it off keeps the tool simple.
    ENABLE_COMMAND_PALETTE: ClassVar[bool] = False
    AUTO_FOCUS: ClassVar[str | None] = "#records"

    CSS = """
    /* One blank line above and below the frame, one space left and right,
       then a rounded border with one space of padding inside it. */
    #frame {
        margin: 1 1;
        padding: 0 1;
        border: round $primary;
        border-title-color: $axlib-title;
        border-title-style: bold;
        border-subtitle-color: $secondary;
    }
    #status {
        height: auto;
        margin-bottom: 1;
    }
    #filter {
        margin-bottom: 1;
        background: $panel;
    }
    #filter:focus {
        background: $secondary 30%;
    }
    #body {
        height: 1fr;
    }
    #records {
        height: 1fr;
        background: $background;
    }
    #records > .datatable--header {
        background: $panel;
        color: $warning;
        text-style: bold;
    }
    #setup {
        height: 1fr;
        padding: 1 2;
    }
    """

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("a", "add", "Add"),
        Binding("e", "edit", "Edit"),
        Binding("d", "delete", "Delete"),
        Binding("slash", "filter", "Filter"),
        Binding("i", "initialize", "Initialize"),
        Binding("s", "switch_store", "Store"),
        Binding("k", "rotate_key", "Rotate key"),
        Binding("r", "refresh", "Refresh"),
        Binding("escape", "focus_records", "Records", show=False),
        Binding("q", "quit", "Quit"),
    ]

    def __init__(
        self,
        settings: CredentialSettings,
        *,
        kind: StoreKind | None = None,
        operator: str | None = None,
        idle_minutes: float = DEFAULT_IDLE_MINUTES,
    ) -> None:
        """Prepare the application without touching any store yet.

        Args:
            settings (CredentialSettings): Loaded axlib settings.
            kind (StoreKind | None): Store to open first; defaults to the first
                configured store in lookup order (SQLite before text file).
            operator (str | None): Login name of the person using the app,
                normally ``$USER``, used to mark their own record.
            idle_minutes (float): Close after this long without input; ``0``
                disables the timeout.

        Returns:
            None: Initializers configure the object in place.

        Raises:
            CredentialConfigurationError: If a configured file mode is malformed.
        """
        super().__init__()
        # The theme must be active before the CSS is parsed, because the CSS
        # uses the theme's custom $axlib-* variables; on_mount would be too late.
        self.register_theme(AXLIB_DARK)
        self.theme = AXLIB_DARK.name
        self.settings = settings
        self.operator = operator
        self.idle_seconds = idle_minutes * 60
        configured = configured_kinds(settings)
        first = kind or (configured[0] if configured else StoreKind.SQLITE)
        # Stores the operator can switch between, always in lookup order.
        self.kinds = tuple(k for k in LOOKUP_ORDER if k in configured or k is first)
        self.admin = StoreAdmin(settings, first)
        self.store_status: StoreStatus | None = None
        self.records: list[AnnotatedRecord] = []
        self.shown: list[AnnotatedRecord] = []
        self._last_input = time.monotonic()

    # ----------------------------------------------------------------- layout

    def compose(self) -> ComposeResult:
        """Build the bordered frame and everything inside it.

        Args:
            None: Layout is fixed; content is filled in by :meth:`_show`.

        Returns:
            ComposeResult: Widgets that make up the main screen.

        Raises:
            None: Composition only creates widget objects.
        """
        with Vertical(id="frame"):
            yield Static(id="status")
            yield Input(placeholder="/ filter services", compact=True, id="filter")
            # A ContentSwitcher shows exactly one of its children: the record
            # table when the store is ready, otherwise setup guidance.
            with ContentSwitcher(initial="records", id="body"):
                yield DataTable(id="records", cursor_type="row", zebra_stripes=True)
                yield Static(id="setup")
            yield Footer(compact=True)

    def on_mount(self) -> None:
        """Label the frame, prepare the table, and load the first store.

        Args:
            None: Called by Textual once the screen exists.

        Returns:
            None: Loading continues in the background.

        Raises:
            None: Store problems are shown on screen, not raised.
        """
        self.query_one("#frame").border_title = self.TITLE
        table = self.query_one("#records", DataTable)
        table.add_columns("SERVICE", "FIELDS", "UPDATED", "NOTES")
        if self.idle_seconds > 0:
            self.set_interval(
                min(IDLE_CHECK_SECONDS, self.idle_seconds), self._close_if_idle
            )
        self.action_refresh()

    # ------------------------------------------------------ background work

    def _run_in_background(
        self,
        description: str,
        task: Callable[[], ResultT],
        on_success: Callable[[ResultT], object],
    ) -> None:
        """Run blocking store work in a thread, then continue on the UI thread.

        Args:
            description (str): Short label shown in Textual's worker tools.
                It is passed explicitly because Textual would otherwise build
                one from the arguments, which could include secret values.
            task (Callable[[], ResultT]): Work to run, usually a
                :func:`functools.partial` of a :class:`StoreAdmin` method.
            on_success (Callable[[ResultT], object]): Called with the result on
                the UI thread.

        Returns:
            None: The worker starts immediately.

        Raises:
            None: Errors are reported by :meth:`_guarded`.
        """
        self.run_worker(
            partial(self._guarded, task, on_success),
            name=description,
            description=description,
            group="store",
            thread=True,
            exit_on_error=False,
        )

    def _guarded(
        self,
        task: Callable[[], ResultT],
        on_success: Callable[[ResultT], object],
    ) -> None:
        """Run ``task`` in the worker thread and route the outcome back.

        Args:
            task (Callable[[], ResultT]): Work to run.
            on_success (Callable[[ResultT], object]): Called with the result.

        Returns:
            None: Exactly one of ``on_success`` or :meth:`_show_error` runs on
                the UI thread.

        Raises:
            None: Every exception is converted to an on-screen message.  The
                catch-all is deliberate: an escaping exception would make
                Textual print local variables, which may hold secrets.
        """
        try:
            result = task()
        except (CredentialError, ValueError) as exc:
            self.call_from_thread(self._show_error, str(exc))
        except Exception as exc:  # noqa: BLE001 - see the Raises note above.
            self.call_from_thread(
                self._show_error, f"Unexpected {type(exc).__name__}: {exc}"
            )
        else:
            self.call_from_thread(on_success, result)

    def _show_error(self, message: str) -> None:
        """Show a problem as a red pop-up notification.

        Args:
            message (str): Operator-facing explanation.

        Returns:
            None: A notification is displayed for a few seconds.

        Raises:
            None: Notifications cannot fail.
        """
        self.notify(
            message, title="Problem", severity="error", timeout=12, markup=False
        )

    # ---------------------------------------------------- loading & display

    def _load(self, admin: StoreAdmin) -> tuple[StoreStatus, list[AnnotatedRecord]]:
        """Read status and annotated records (runs in a worker thread).

        Args:
            admin (StoreAdmin): Store to read.

        Returns:
            tuple[StoreStatus, list[AnnotatedRecord]]: Status, plus records when
                the store is ready (otherwise an empty list).

        Raises:
            CredentialError: If the store changes state between the two reads.
        """
        status = admin.status()
        if not status.ready:
            return status, []
        return status, admin.annotate(admin.list_records(), operator=self.operator)

    def _show(
        self,
        admin: StoreAdmin,
        loaded: tuple[StoreStatus, list[AnnotatedRecord]],
    ) -> None:
        """Display freshly loaded status and records.

        Args:
            admin (StoreAdmin): Store the data came from.
            loaded (tuple[StoreStatus, list[AnnotatedRecord]]): Result of
                :meth:`_load`.

        Returns:
            None: The status line, table or setup text, and footer are updated.

        Raises:
            None: Only widgets are updated.
        """
        if admin is not self.admin:
            return  # The operator switched stores while this was loading.
        status, records = loaded
        self.store_status = status
        self.records = records
        self.query_one("#status", Static).update(self._status_text(status))
        self.query_one("#frame").border_subtitle = f"{status.kind.label} store"
        # Filtering only makes sense when there are records to filter.
        self.query_one("#filter", Input).display = status.ready
        body = self.query_one("#body", ContentSwitcher)
        if status.ready:
            body.current = "records"
            self._fill_table()
        else:
            body.current = "setup"
            self.query_one("#setup", Static).update(self._setup_text(status))
        # Bindings depend on the store state (see check_action), so the footer
        # must be redrawn whenever that state changes.
        self.refresh_bindings()

    def _status_text(self, status: StoreStatus) -> str:
        """Describe the current store in two short lines.

        Args:
            status (StoreStatus): Status of the displayed store.

        Returns:
            str: Textual markup for the status widget.

        Raises:
            None: Only string formatting is performed.
        """
        path = "not configured" if status.data_path is None else str(status.data_path)
        first = f"[b $primary]{status.kind.label} store[/]  {escape(path)}"
        if status.key_source == "environment":
            key = f"key [$success]{status.kind.key_env_var}[/]"
        elif status.key_source == "file":
            key = f"key [$success]{escape(str(status.key_file))}[/]"
        else:
            key = "[$warning]no key configured[/]"
        details = [key]
        if status.ready:
            details.append(f"[$warning]{plural(status.record_count, 'record')}[/]")
        if self.settings.shared_service:
            shared = escape(self.settings.shared_service)
            details.append(f"shared [$axlib-title]{shared}[/]")
        if status.data_path is not None and not status.enabled:
            details.append("[$warning]disabled: ax.getkeys() skips this store[/]")
        return f"{first}\n{'  [$axlib-muted]·[/]  '.join(details)}"

    def _setup_text(self, status: StoreStatus) -> str:
        """Explain what to do when the store is not ready.

        Args:
            status (StoreStatus): Status of the displayed store.

        Returns:
            str: Textual markup for the setup panel.

        Raises:
            None: Only string formatting is performed.
        """
        label = status.kind.label
        if status.state == "not-configured":
            lines = [
                f"[b $warning]No {label} store is configured.[/]",
                "",
                (
                    f"Add a [b]\\[{status.kind.config_section}][/b] section to the "
                    "axlib TOML file, then start this tool with --config PATH (or "
                    "set AXLIB_CONFIG_FILE)."
                ),
            ]
        elif status.state == "not-initialized":
            lines = [
                f"[b $warning]The {label} store has not been created yet.[/]",
                "",
                f"It will live at [b]{escape(str(status.data_path))}[/b].",
            ]
        else:
            lines = [
                f"[b $error]The {label} store could not be opened.[/]",
                "",
                escape(status.error or "Unknown problem."),
            ]
        hints = ["Press [b $accent]r[/] to check again."]
        if status.can_initialize:
            hints.insert(0, "Press [b $accent]i[/] to initialize it.")
        if len(self.kinds) > 1:
            hints.append("Press [b $accent]s[/] to switch to the other store.")
        return "\n".join([*lines, "", *hints])

    def _fill_table(self) -> None:
        """Show the records that match the filter, keeping the selection.

        Args:
            None: Records and the filter text are read from the app.

        Returns:
            None: The table is cleared and refilled.

        Raises:
            None: Only widgets are updated.
        """
        table = self.query_one("#records", DataTable)
        selected = self._selected()
        needle = self.query_one("#filter", Input).value.strip().casefold()
        self.shown = [
            item for item in self.records if needle in item.record.service.casefold()
        ]
        table.clear()
        for item in self.shown:
            table.add_row(*self._row_cells(item), key=item.record.service)
        if selected is not None:
            for index, item in enumerate(self.shown):
                if item.record.service == selected.record.service:
                    table.move_cursor(row=index)
                    break

    @staticmethod
    def _row_cells(item: AnnotatedRecord) -> tuple[Text, ...]:
        """Build the colored cells for one table row.

        Rich ``Text`` objects carry a style without interpreting markup, so a
        service name containing ``[`` cannot change the display, and the table
        can measure them to size its columns.

        Args:
            item (AnnotatedRecord): Record and notes to display.

        Returns:
            tuple[Text, ...]: Service, fields, updated date, and notes.

        Raises:
            None: Only display objects are created.
        """
        record = item.record
        # Show fields in the profile's order (netuser, netpass, netenable),
        # followed by any fields the profile does not know about.
        known = [name for name in item.profile.field_names if name in record.fields]
        extra = [name for name in record.fields if name not in known]
        notes = Text("  ").join(
            Text(note.text, style=NOTE_COLORS[note.kind]) for note in item.notes
        )
        return (
            Text(record.service, style=f"bold {WHITE}"),
            Text(",".join(known + extra), style=GREEN),
            Text(short_date(record.updated_at), style=BLUE),
            notes,
        )

    def _selected(self) -> AnnotatedRecord | None:
        """Return the record under the table cursor.

        Args:
            None: The cursor position is read from the table.

        Returns:
            AnnotatedRecord | None: Highlighted record, or ``None`` when the
                table is empty.

        Raises:
            None: An empty table yields ``None``.
        """
        row = self.query_one("#records", DataTable).cursor_row
        return self.shown[row] if 0 <= row < len(self.shown) else None

    # ------------------------------------------------------- key bindings

    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool | None:
        """Hide key bindings that make no sense in the current state.

        Textual calls this before running an action and when drawing the
        footer; returning ``False`` both disables the key and hides its hint.

        Args:
            action (str): Action name, such as ``"edit"``.
            parameters (tuple[object, ...]): Action parameters (unused).

        Returns:
            bool | None: ``True`` to allow the action, ``False`` to hide it.

        Raises:
            None: Only application state is inspected.
        """
        status = self.store_status
        ready = status is not None and status.ready
        if action in {"add", "filter", "rotate_key"}:
            return ready
        if action in {"edit", "delete"}:
            return ready and self._selected() is not None
        if action == "initialize":
            return status is not None and status.can_initialize
        if action == "switch_store":
            return len(self.kinds) > 1
        return True

    def action_refresh(self) -> None:
        """Reload the current store's status and records.

        Args:
            None: The current store is read.

        Returns:
            None: Loading happens in the background.

        Raises:
            None: Problems are shown on screen.
        """
        admin = self.admin
        self._run_in_background(
            "load records", partial(self._load, admin), partial(self._show, admin)
        )

    def action_filter(self) -> None:
        """Move the cursor to the filter box.

        Args:
            None: Bound to ``/``.

        Returns:
            None: Keyboard focus moves.

        Raises:
            None: The filter box always exists.
        """
        self.query_one("#filter", Input).focus()

    def action_focus_records(self) -> None:
        """Move the cursor back to the record table.

        Args:
            None: Bound to ``Escape``.

        Returns:
            None: Keyboard focus moves.

        Raises:
            None: The table always exists.
        """
        self.query_one("#records", DataTable).focus()

    def action_add(self) -> None:
        """Open the form for a new record.

        Args:
            None: Bound to ``a``.

        Returns:
            None: The form's result is handled by :meth:`_save`.

        Raises:
            None: Opening a dialog cannot fail.
        """
        existing = frozenset(item.record.service for item in self.records)
        self.push_screen(RecordFormScreen(existing_services=existing), self._save)

    def action_edit(self) -> None:
        """Open the form for the selected record, pre-filling safe fields.

        Args:
            None: Bound to ``e`` and to ``Enter`` on a table row.

        Returns:
            None: The non-secret values are read in the background first.

        Raises:
            None: Problems are shown on screen.
        """
        item = self._selected()
        if item is None:
            return
        self._run_in_background(
            "read record",
            partial(self.admin.read_non_secret, item.record.service, item.profile),
            partial(self._open_edit_form, item),
        )

    def _open_edit_form(self, item: AnnotatedRecord, visible: dict[str, str]) -> None:
        """Show the edit form once the non-secret values have been read.

        Args:
            item (AnnotatedRecord): Record being edited.
            visible (dict[str, str]): Current non-secret values.

        Returns:
            None: The form's result is handled by :meth:`_save`.

        Raises:
            None: Opening a dialog cannot fail.
        """
        existing = frozenset(record.record.service for record in self.records)
        form = RecordFormScreen(
            existing_services=existing, record=item, visible_values=visible
        )
        self.push_screen(form, self._save)

    def _save(self, change: RecordChange | None) -> None:
        """Apply a submitted add or edit form.

        Args:
            change (RecordChange | None): Form result; ``None`` if cancelled.

        Returns:
            None: The change is written in the background.

        Raises:
            None: Problems are shown on screen.
        """
        if change is None:
            return
        if change.creating:
            task = partial(
                self.admin.add, change.service, change.values, profile=change.profile
            )
        else:
            task = partial(
                self.admin.update,
                change.service,
                change.values,
                remove=change.remove,
                profile=change.profile,
            )
        self._run_in_background("save record", task, self._report_change)

    def action_delete(self) -> None:
        """Ask for confirmation, then delete the selected record.

        Args:
            None: Bound to ``d``.

        Returns:
            None: Deletion happens after the operator types the service name.

        Raises:
            None: Opening a dialog cannot fail.
        """
        item = self._selected()
        if item is None:
            return
        service = item.record.service
        message = (
            f"Delete [b]{escape(service)}[/b] and its fields "
            f"({escape(', '.join(item.record.fields))}) from the "
            f"{self.admin.kind.label} store?\n\nScripts that use this service "
            "will fall back to the shared account, or fail if there is none."
        )
        confirm = ConfirmScreen(
            title=f"Delete {service}",
            message=message,
            confirm_text=service,
            action_label="Delete",
        )
        self.push_screen(confirm, partial(self._delete_confirmed, service))

    def _delete_confirmed(self, service: str, confirmed: bool | None) -> None:
        """Delete ``service`` if the operator confirmed.

        Args:
            service (str): Service to delete.
            confirmed (bool | None): Dialog result.

        Returns:
            None: Deletion happens in the background.

        Raises:
            None: Problems are shown on screen.
        """
        if confirmed:
            self._run_in_background(
                "delete record",
                partial(self.admin.delete, service),
                self._report_change,
            )

    def _report_change(self, result: ChangeResult) -> None:
        """Confirm a completed change and reload the table.

        Args:
            result (ChangeResult): What the admin API changed.

        Returns:
            None: Notifications are shown and the records reloaded.

        Raises:
            None: Notifications cannot fail.
        """
        details = []
        if result.fields:
            details.append(f"set {', '.join(result.fields)}")
        if result.removed:
            details.append(f"removed {', '.join(result.removed)}")
        suffix = f": {'; '.join(details)}" if details else ""
        self.notify(
            f"{result.action.capitalize()} {result.service}{suffix}",
            title="Saved",
            markup=False,
        )
        if result.cache_error:
            self.notify(
                result.cache_error,
                title="Redis cache not cleared",
                severity="warning",
                timeout=15,
                markup=False,
            )
        self.action_refresh()

    def action_initialize(self) -> None:
        """Offer to create or verify the current store.

        Args:
            None: Bound to ``i`` (shown only when the store is not ready).

        Returns:
            None: Initialization happens after the operator confirms.

        Raises:
            None: Opening a dialog cannot fail.
        """
        if self.store_status is not None:
            self.push_screen(
                InitializeScreen(self.store_status), self._initialize_confirmed
            )

    def _initialize_confirmed(self, generate_key: bool | None) -> None:
        """Initialize the store with the operator's key choice.

        Args:
            generate_key (bool | None): Dialog result; ``None`` if cancelled.

        Returns:
            None: Initialization happens in the background.

        Raises:
            None: Problems are shown on screen.
        """
        if generate_key is None:
            return
        self._run_in_background(
            "initialize store",
            partial(self.admin.initialize, generate_key=generate_key),
            self._report_initialized,
        )

    def _report_initialized(self, _result: None) -> None:
        """Confirm initialization and load the new store.

        Args:
            _result (None): :meth:`StoreAdmin.initialize` returns nothing.

        Returns:
            None: A notification is shown and the records reloaded.

        Raises:
            None: Notifications cannot fail.
        """
        self.notify(f"The {self.admin.kind.label} store is ready.", title="Initialized")
        self.action_refresh()

    def action_rotate_key(self) -> None:
        """Ask for confirmation, then re-encrypt the store under a new key.

        Args:
            None: Bound to ``k``.

        Returns:
            None: Rotation happens after the operator types ``rotate``.

        Raises:
            None: Problems are shown on screen.
        """
        status = self.store_status
        if status is None:
            return
        if status.key_source != "file":
            self._show_error(
                f"Key rotation needs a key file, but this store's key comes from "
                f"{status.kind.key_env_var}."
            )
            return
        key_file = escape(str(status.key_file))
        message = (
            f"Re-encrypt {plural(status.record_count, 'record')} in the "
            f"{status.kind.label} store under a new random AES-256 key, then "
            f"replace [b]{key_file}[/b].\n\nAnything still holding a copy of the "
            "old key will stop working.  If rotation is interrupted, the new "
            f"key is kept at [b]{key_file}.rotating[/b]."
        )
        confirm = ConfirmScreen(
            title="Rotate the encryption key",
            message=message,
            confirm_text="rotate",
            action_label="Rotate key",
        )
        self.push_screen(confirm, self._rotate_confirmed)

    def _rotate_confirmed(self, confirmed: bool | None) -> None:
        """Rotate the key if the operator confirmed.

        Args:
            confirmed (bool | None): Dialog result.

        Returns:
            None: Rotation happens in the background.

        Raises:
            None: Problems are shown on screen.
        """
        if confirmed:
            self._run_in_background(
                "rotate key", self.admin.rotate_key, self._report_rotation
            )

    def _report_rotation(self, count: int) -> None:
        """Confirm a completed key rotation.

        Args:
            count (int): Number of records re-encrypted.

        Returns:
            None: A notification is shown and the records reloaded.

        Raises:
            None: Notifications cannot fail.
        """
        self.notify(
            f"Re-encrypted {plural(count, 'record')} under the new key.",
            title="Key rotated",
        )
        self.action_refresh()

    def action_switch_store(self) -> None:
        """Show the other configured store.

        Args:
            None: Bound to ``s`` (shown only when two stores are configured).

        Returns:
            None: The other store loads in the background.

        Raises:
            None: Problems are shown on screen.
        """
        position = self.kinds.index(self.admin.kind)
        next_kind = self.kinds[(position + 1) % len(self.kinds)]
        try:
            self.admin = StoreAdmin(self.settings, next_kind)
        except CredentialError as exc:
            self._show_error(str(exc))
            return
        self.store_status = None
        self.records = []
        self.action_refresh()

    # ------------------------------------------------------------- events

    def on_input_changed(self, event: Input.Changed) -> None:
        """Re-filter the table as the filter text changes.

        Args:
            event (Input.Changed): Textual event for any input, including those
                inside dialogs; only the filter box is handled here.

        Returns:
            None: The table is refilled.

        Raises:
            None: Only widgets are updated.
        """
        if event.input.id == "filter":
            self._fill_table()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        """Return to the table when ``Enter`` is pressed in the filter box.

        Args:
            event (Input.Submitted): Textual event for any input.

        Returns:
            None: Keyboard focus moves to the table.

        Raises:
            None: The table always exists.
        """
        if event.input.id == "filter":
            self.action_focus_records()

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        """Open the edit form when ``Enter`` is pressed on a row.

        Args:
            event (DataTable.RowSelected): Textual event for the chosen row.

        Returns:
            None: The edit form opens.

        Raises:
            None: Problems are shown on screen.
        """
        self.action_edit()

    async def on_event(self, event: events.Event) -> None:
        """Note the time of every key press or mouse action.

        Args:
            event (events.Event): Any event delivered to the application.

        Returns:
            None: Textual's normal event handling continues afterwards.

        Raises:
            None: Textual's own handler decides what to do with the event.
        """
        if isinstance(event, events.InputEvent):
            self._last_input = time.monotonic()
        await super().on_event(event)

    def _close_if_idle(self) -> None:
        """Exit when there has been no input for the idle timeout.

        Args:
            None: Called periodically by a Textual timer.

        Returns:
            None: The app exits, or nothing happens.

        Raises:
            None: Exiting cannot fail.
        """
        if time.monotonic() - self._last_input >= self.idle_seconds:
            minutes = self.idle_seconds / 60
            self.exit(
                message=(
                    f"axlib credential manager closed after {minutes:g} minute(s) "
                    "without input."
                )
            )
