# Copyright 2026 Robert Patrick
# SPDX-License-Identifier: Apache-2.0

"""Pop-up dialogs used by the axlib credential manager.

Each dialog is a Textual ``ModalScreen``: it appears above the record list,
takes all keyboard input until closed, and hands one result back to the
application through ``dismiss(result)``.  Dialogs only *collect* decisions --
they never touch a credential store.  The application applies the result, which
keeps each class focused on one job and makes the dialogs easy to test.

* :class:`RecordFormScreen` adds or edits a record.
* :class:`ConfirmScreen` asks the operator to type a word before a
  destructive action such as deleting a record or rotating the key.
* :class:`InitializeScreen` creates or verifies a store.

Secret handling: password boxes are :class:`SecretInput` widgets, every secret
box is emptied as soon as the form closes, and the collected values travel in a
:class:`RecordChange` whose ``repr()`` omits them.

Dependencies:
    ``textual`` (the optional ``tui`` extra: ``uv add 'axlib[tui]'``).

Example:
    Open the add form from inside a running Textual app::

        self.push_screen(RecordFormScreen(existing_services=frozenset()), callback)
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import ClassVar, TypeVar

from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Horizontal, Vertical
from textual.markup import escape
from textual.screen import ModalScreen
from textual.widget import Widget
from textual.widgets import Button, Checkbox, Input, Label, Select, Static
from textual.widgets.button import ButtonVariant

from axlib.credentials.admin import AnnotatedRecord, StoreStatus, prepare_update
from axlib.credentials.manager import normalize_service_for_write
from axlib.credentials.profiles import (
    DEFAULT_PROFILE,
    NOTE_FIELD,
    NOTE_MAX_LENGTH,
    PROFILES,
    RecordProfile,
    validate_values,
)
from axlib.credentials.tui.widgets import SecretInput

ResultT = TypeVar("ResultT")


@dataclass(frozen=True, slots=True)
class RecordChange:
    """A validated add or edit request collected by :class:`RecordFormScreen`.

    Attributes:
        service: Normalized service name.
        profile: Profile that decided which fields were offered.
        creating: ``True`` for a new record, ``False`` for an edit.
        values: Fields to write.  ``repr=False`` keeps these plaintext values
            out of ``repr()``, which debug logs and crash reports use to show
            objects.
        remove: Optional fields to delete from an existing record.
    """

    service: str
    profile: RecordProfile
    creating: bool
    values: dict[str, str] = field(repr=False)
    remove: tuple[str, ...] = ()


class DialogScreen(ModalScreen[ResultT]):
    """Shared layout and keys for every axlib dialog.

    Subclasses compose their widgets inside :meth:`dialog_body` and implement
    :meth:`action_submit`.  ``Escape`` or the *Cancel* button closes the dialog
    with the result ``None``.
    """

    DEFAULT_CSS = """
    DialogScreen {
        align: center middle;
        background: $background 70%;
    }
    DialogScreen #dialog {
        width: 76;
        max-width: 100%;
        height: auto;
        max-height: 100%;
        padding: 0 1;
        background: $panel;
        border: round $primary;
        border-title-color: $accent;
        border-title-style: bold;
    }
    /* Textual's Vertical defaults to height: 1fr (fill the parent); inside
       an auto-height dialog that would stretch it to the full screen. */
    DialogScreen #dialog-body, DialogScreen #fields {
        height: auto;
    }
    DialogScreen .message {
        margin: 1 0;
    }
    DialogScreen .row {
        height: 1;
        margin-bottom: 1;
    }
    DialogScreen .row.paired {
        margin-bottom: 0;
    }
    DialogScreen .row Label {
        width: 20;
        color: $secondary;
        text-style: bold;
    }
    /* A faint blue tint shows where each box is, even while it is empty;
       the focused box is tinted more strongly. */
    DialogScreen Input.-textual-compact {
        width: 1fr;
        background: $secondary 15%;
    }
    DialogScreen Input.-textual-compact:focus {
        background: $secondary 35%;
    }
    DialogScreen Checkbox {
        margin: 0 0 1 20;
        background: $panel;
    }
    DialogScreen #generate-key {
        margin-left: 0;
    }
    DialogScreen .hint {
        color: $text-muted;
        margin-bottom: 1;
    }
    DialogScreen #error {
        color: $error;
        text-style: bold;
        height: auto;
    }
    DialogScreen .buttons {
        height: auto;
        align-horizontal: right;
        margin-top: 1;
    }
    DialogScreen .buttons Button {
        margin-left: 2;
    }
    """

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("escape", "cancel", "Cancel"),
    ]

    def __init__(
        self, title: str, submit_label: str, *, submit_variant: ButtonVariant
    ) -> None:
        """Remember the dialog title and the label of its main button.

        Args:
            title (str): Text shown in the dialog's top border.
            submit_label (str): Label of the button that accepts the dialog.
            submit_variant (ButtonVariant): Textual button style, such as
                ``"success"``, or ``"error"`` for a destructive action.

        Returns:
            None: Initializers configure the screen in place.

        Raises:
            None: Only attributes are stored.
        """
        super().__init__()
        self.dialog_title = title
        self.submit_label = submit_label
        self.submit_variant = submit_variant

    def compose(self) -> ComposeResult:
        """Build the frame, the subclass body, the error line, and buttons.

        Args:
            None: Content comes from :meth:`dialog_body`.

        Returns:
            ComposeResult: Widgets that make up the dialog.

        Raises:
            None: Composition only creates widget objects.
        """
        with Vertical(id="dialog") as dialog:
            dialog.border_title = self.dialog_title
            with Vertical(id="dialog-body"):
                yield from self.dialog_body()
            yield Static("", id="error")
            with Horizontal(classes="buttons"):
                yield Button("Cancel", id="cancel", compact=True)
                yield Button(
                    self.submit_label,
                    id="submit",
                    variant=self.submit_variant,
                    compact=True,
                )

    def dialog_body(self) -> ComposeResult:
        """Yield the widgets specific to one dialog.

        Args:
            None: Subclasses decide the content.

        Returns:
            ComposeResult: Widgets placed above the error line.

        Raises:
            NotImplementedError: If a subclass does not provide a body.
        """
        raise NotImplementedError

    def action_submit(self) -> None:
        """Accept the dialog; subclasses validate and call ``dismiss``.

        Args:
            None: Values are read from the dialog's widgets.

        Returns:
            None: Subclasses dismiss the screen with a result.

        Raises:
            NotImplementedError: If a subclass does not implement it.
        """
        raise NotImplementedError

    def action_cancel(self) -> None:
        """Close the dialog without a result.

        Args:
            None: Bound to ``Escape`` and the *Cancel* button.

        Returns:
            None: The screen is dismissed with ``None``.

        Raises:
            None: Dismissing a modal screen cannot fail.
        """
        self.dismiss(None)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        """Route the two dialog buttons to their actions.

        Args:
            event (Button.Pressed): Textual event naming the pressed button.

        Returns:
            None: The matching action runs.

        Raises:
            None: Unknown buttons are ignored.
        """
        if event.button.id == "cancel":
            self.action_cancel()
        elif event.button.id == "submit":
            self.action_submit()

    def show_error(self, message: str) -> None:
        """Display a validation problem without closing the dialog.

        Args:
            message (str): Operator-facing explanation.  Markup characters are
                escaped so text such as ``[x]`` is shown literally.

        Returns:
            None: The error line is updated.

        Raises:
            None: Updating a label cannot fail.
        """
        self.query_one("#error", Static).update(escape(message))


class RecordFormScreen(DialogScreen[RecordChange]):
    """Collect the fields for a new record or changes to an existing one.

    Passwords are typed twice and compared.  When editing, a blank password
    means "keep the current value", non-secret fields such as the username and
    the note are pre-filled, an optional password can be ticked for removal,
    and clearing the note removes it.
    """

    def __init__(
        self,
        *,
        existing_services: frozenset[str],
        record: AnnotatedRecord | None = None,
        visible_values: Mapping[str, str] | None = None,
    ) -> None:
        """Prepare an add form (no ``record``) or an edit form.

        Args:
            existing_services (frozenset[str]): Services already in the store,
                used to warn before a duplicate is submitted.
            record (AnnotatedRecord | None): Record being edited, or ``None``
                to add a new one.
            visible_values (Mapping[str, str] | None): Current non-secret values
                used to pre-fill an edit form.

        Returns:
            None: Initializers configure the screen in place.

        Raises:
            None: Only attributes are stored.
        """
        self.existing_services = existing_services
        self.record = record
        self.visible_values = dict(visible_values or {})
        self.profile = DEFAULT_PROFILE if record is None else record.profile
        if record is None:
            super().__init__(
                "Add a credential record", "Save", submit_variant="success"
            )
        else:
            super().__init__(
                f"Edit {record.record.service}", "Save", submit_variant="success"
            )

    @property
    def creating(self) -> bool:
        """Report whether this form adds a new record.

        Args:
            None: Derived from the constructor arguments.

        Returns:
            bool: ``True`` for an add form, ``False`` for an edit form.

        Raises:
            None: Only an attribute is read.
        """
        return self.record is None

    def dialog_body(self) -> ComposeResult:
        """Yield the service, profile, and field inputs.

        Args:
            None: Content depends on add versus edit mode.

        Returns:
            ComposeResult: Widgets for the form body.

        Raises:
            None: Composition only creates widget objects.
        """
        if self.record is None:
            with Horizontal(classes="row paired"):
                yield Label("Service")
                yield Input(
                    placeholder="for example first.last or network-shared",
                    compact=True,
                    id="service",
                )
            yield Static("", id="service-preview", classes="hint")
            with Horizontal(classes="row"):
                yield Label("Profile")
                yield Select(
                    [(profile.label, profile.name) for profile in PROFILES.values()],
                    value=self.profile.name,
                    allow_blank=False,
                    compact=True,
                    id="profile",
                )
        else:
            yield Static(
                f"[b]{escape(self.profile.label)}[/b] record.  Leave a password "
                "blank to keep its current value; clear the note to remove it.",
                classes="hint",
            )
        yield Vertical(*self._field_rows(self.profile), id="fields")

    def _field_rows(self, profile: RecordProfile) -> list[Widget]:
        """Build one row per field (and a confirmation row per secret).

        Args:
            profile (RecordProfile): Profile whose fields are shown.

        Returns:
            list[Widget]: Rows ready to mount inside the ``#fields`` container.

        Raises:
            None: Only widget objects are created.
        """
        present = set() if self.record is None else set(self.record.record.fields)
        rows: list[Widget] = []
        for spec in profile.fields:
            # A trailing "*" marks fields that a new record must include.
            label = f"{spec.label}{' *' if spec.required and self.creating else ''}"
            if self.creating:
                hint = "required" if spec.required else "optional"
            elif spec.secret or spec.required:
                hint = "leave blank to keep"
            else:
                hint = "optional; clear to remove"
            if spec.secret:
                entry: Input = SecretInput(placeholder=hint, id=spec.name)
                confirm = SecretInput(
                    placeholder="type it again", id=f"{spec.name}-confirm"
                )
                # "paired" removes the gap so each password sits directly
                # above its confirmation box.
                rows.append(Horizontal(Label(label), entry, classes="row paired"))
                rows.append(Horizontal(Label("  confirm"), confirm, classes="row"))
            else:
                entry = Input(
                    value=self.visible_values.get(spec.name, ""),
                    placeholder=hint,
                    compact=True,
                    # 0 means "no limit" to Textual; only the note has one.
                    max_length=NOTE_MAX_LENGTH if spec.name == NOTE_FIELD else 0,
                    id=spec.name,
                )
                rows.append(Horizontal(Label(label), entry, classes="row"))
            # A visible optional field is removed by clearing it, so only an
            # optional password, which is never shown, needs a checkbox.
            if (
                not self.creating
                and spec.secret
                and not spec.required
                and spec.name in present
            ):
                rows.append(
                    Checkbox(
                        f"Remove {spec.label.lower()}",
                        compact=True,
                        id=f"{spec.name}-remove",
                    )
                )
        return rows

    def on_mount(self) -> None:
        """Put the cursor in the first box the operator needs.

        Args:
            None: Called by Textual after the dialog is displayed.

        Returns:
            None: Keyboard focus moves to the first input.

        Raises:
            None: The form always contains at least one input.
        """
        self.query(Input).first().focus()

    def on_input_changed(self, event: Input.Changed) -> None:
        """Preview the stored service name while it is being typed.

        Args:
            event (Input.Changed): Textual event for any input in the form.

        Returns:
            None: The preview line is updated for the service input only.

        Raises:
            None: Invalid names are reported in the preview, not raised.
        """
        if event.input.id != "service":
            return
        preview = self.query_one("#service-preview", Static)
        if not event.value.strip():
            preview.update("")
            return
        try:
            service = normalize_service_for_write(event.value)
        except ValueError as exc:
            preview.update(f"[$warning]{escape(str(exc))}[/]")
            return
        if service in self.existing_services:
            preview.update(f"[$warning]{service} already exists; use Edit instead[/]")
        else:
            preview.update(f"stored as [$success]{service}[/]")

    async def on_select_changed(self, event: Select.Changed) -> None:
        """Swap the field rows when a different profile is chosen.

        Args:
            event (Select.Changed): Textual event carrying the profile name.

        Returns:
            None: The ``#fields`` container is rebuilt.

        Raises:
            None: Profile names come from :data:`PROFILES`.
        """
        if not isinstance(event.value, str) or event.value == self.profile.name:
            return
        self.profile = PROFILES[event.value]
        fields = self.query_one("#fields", Vertical)
        await fields.remove_children()
        await fields.mount_all(self._field_rows(self.profile))

    def on_input_submitted(self, event: Input.Submitted) -> None:
        """Make ``Enter`` move to the next box, and save from the last one.

        Args:
            event (Input.Submitted): Textual event for the input that had focus.

        Returns:
            None: Focus moves on, or the form is submitted.

        Raises:
            None: Navigation cannot fail.
        """
        if event.input is self.query(Input).last():
            self.action_submit()
        else:
            self.focus_next()

    def action_submit(self) -> None:
        """Validate the form and close it with a :class:`RecordChange`.

        Args:
            None: Values are read from the form's inputs.

        Returns:
            None: The dialog closes on success or shows why it cannot.

        Raises:
            None: Every error is displayed in the dialog instead.  Textual's
                crash report prints local variables, which here could include
                typed passwords, so no exception may escape this method.
        """
        try:
            change = self._collect()
        except ValueError as exc:
            self.show_error(str(exc))
            return
        except Exception as exc:  # noqa: BLE001 - see the Raises note above.
            self._clear_secrets()
            self.show_error(f"Unexpected {type(exc).__name__}: {exc}")
            return
        self._clear_secrets()
        self.dismiss(change)

    def _collect(self) -> RecordChange:
        """Read, compare, and validate every input.

        Args:
            None: Values are read from the form's inputs.

        Returns:
            RecordChange: The validated request.

        Raises:
            ValueError: If the name is invalid or duplicated, a password and
                its confirmation differ, or the fields fail profile validation.
        """
        profile = self.profile
        if self.record is None:
            service = normalize_service_for_write(
                self.query_one("#service", Input).value
            )
            if service in self.existing_services:
                raise ValueError(f"{service} already exists; cancel and use Edit.")
        else:
            service = self.record.record.service

        values: dict[str, str] = {}
        remove: list[str] = []
        for spec in profile.fields:
            value = self.query_one(f"#{spec.name}", Input).value
            if spec.secret:
                confirm = self.query_one(f"#{spec.name}-confirm", Input).value
                if value != confirm:
                    raise ValueError(f"The two {spec.label.lower()} entries differ.")
            elif not spec.required and not value.strip():
                # Clearing a pre-filled optional field such as the note
                # removes it; a field that was already empty stays absent.
                if spec.name in self.visible_values:
                    remove.append(spec.name)
                continue
            # Blank means "keep the current value", and re-sending an
            # unchanged pre-filled value would only rewrite the same data.
            if value and value != self.visible_values.get(spec.name):
                values[spec.name] = value
            # query() returns an empty result when a field has no checkbox.
            removal = self.query(f"#{spec.name}-remove").results(Checkbox)
            if any(checkbox.value for checkbox in removal):
                remove.append(spec.name)

        if self.creating:
            return RecordChange(
                service=service,
                profile=profile,
                creating=True,
                values=validate_values(values, profile, creating=True),
            )
        prepared, removing = prepare_update(values, remove, profile)
        return RecordChange(
            service=service,
            profile=profile,
            creating=False,
            values=prepared,
            remove=removing,
        )

    def _clear_secrets(self) -> None:
        """Empty every password box so typed secrets do not linger on screen.

        Args:
            None: All :class:`SecretInput` widgets in the form are cleared.

        Returns:
            None: Input values are replaced with empty strings.

        Raises:
            None: Setting an input value cannot fail.
        """
        for secret_input in self.query(SecretInput):
            secret_input.value = ""


class ConfirmScreen(DialogScreen[bool]):
    """Require the operator to type a word before a destructive action."""

    def __init__(
        self,
        *,
        title: str,
        message: str,
        confirm_text: str,
        action_label: str,
    ) -> None:
        """Describe the action and the word that confirms it.

        Args:
            title (str): Dialog title, such as ``"Delete jsmith"``.
            message (str): Explanation shown above the input; may use markup.
            confirm_text (str): Exact text the operator must type.
            action_label (str): Label of the destructive button.

        Returns:
            None: Initializers configure the screen in place.

        Raises:
            None: Only attributes are stored.
        """
        super().__init__(title, action_label, submit_variant="error")
        self.message = message
        self.confirm_text = confirm_text

    def dialog_body(self) -> ComposeResult:
        """Yield the explanation and the confirmation input.

        Args:
            None: Content comes from the constructor arguments.

        Returns:
            ComposeResult: Widgets for the dialog body.

        Raises:
            None: Composition only creates widget objects.
        """
        yield Static(self.message, classes="message")
        with Horizontal(classes="row"):
            yield Label("Type to confirm")
            yield Input(placeholder=self.confirm_text, compact=True, id="confirm")

    def on_mount(self) -> None:
        """Focus the input and disable the button until the word matches.

        Args:
            None: Called by Textual after the dialog is displayed.

        Returns:
            None: Focus and button state are set.

        Raises:
            None: Both widgets always exist.
        """
        self.query_one("#submit", Button).disabled = True
        self.query_one("#confirm", Input).focus()

    def on_input_changed(self, event: Input.Changed) -> None:
        """Enable the destructive button only when the typed word matches.

        Args:
            event (Input.Changed): Textual event for the confirmation input.

        Returns:
            None: The button's ``disabled`` state is updated.

        Raises:
            None: Comparing strings cannot fail.
        """
        matches = event.value.strip() == self.confirm_text
        self.query_one("#submit", Button).disabled = not matches

    def on_input_submitted(self, event: Input.Submitted) -> None:
        """Treat ``Enter`` in the input like pressing the button.

        Args:
            event (Input.Submitted): Textual event for the confirmation input.

        Returns:
            None: The dialog is submitted.

        Raises:
            None: Submission validates the text itself.
        """
        self.action_submit()

    def action_submit(self) -> None:
        """Close with ``True`` when the confirmation text matches.

        Args:
            None: The typed text is read from the input.

        Returns:
            None: The dialog closes, or an error explains what to type.

        Raises:
            None: Comparing strings cannot fail.
        """
        if self.query_one("#confirm", Input).value.strip() == self.confirm_text:
            self.dismiss(result=True)
        else:
            self.show_error(f"Type {self.confirm_text} exactly to continue.")


class InitializeScreen(DialogScreen[bool]):
    """Create a missing store, or verify an existing one.

    The dialog closes with the "generate a key file" choice (``True`` or
    ``False``), or ``None`` when cancelled.
    """

    def __init__(self, status: StoreStatus) -> None:
        """Describe the store that would be created or verified.

        Args:
            status (StoreStatus): Current status of the selected store.

        Returns:
            None: Initializers configure the screen in place.

        Raises:
            None: Only attributes are stored.
        """
        super().__init__(
            f"Initialize the {status.kind.label} store",
            "Initialize",
            submit_variant="success",
        )
        self.status = status

    def dialog_body(self) -> ComposeResult:
        """Yield an explanation of what initialization will do.

        Args:
            None: Content comes from the store status.

        Returns:
            ComposeResult: Widgets for the dialog body.

        Raises:
            None: Composition only creates widget objects.
        """
        status = self.status
        key_file = escape(str(status.key_file))
        verb = "Verify the existing" if status.data_exists else "Create a new"
        data_path = escape(str(status.data_path))
        lines = [f"{verb} {status.kind.label} store at [b]{data_path}[/b]."]
        if status.key_source == "environment":
            lines.append(f"The AES key comes from [b]{status.kind.key_env_var}[/b].")
        elif status.key_source == "file" and status.key_file_exists:
            lines.append(f"The AES key is read from [b]{key_file}[/b].")
        elif status.key_source == "file":
            lines.append(f"The key file [b]{key_file}[/b] does not exist yet.")
        else:
            lines.append(
                "[$warning]No AES key is configured.  Set "
                f"{status.kind.config_section}.key_file in the axlib TOML file.[/]"
            )
        lines.append("Initialization never overwrites an existing key or store.")
        yield Static("\n".join(lines), classes="message")
        if status.can_generate_key:
            yield Checkbox(
                "Generate a new random AES-256 key file",
                value=True,
                compact=True,
                id="generate-key",
            )

    def on_mount(self) -> None:
        """Focus the main button so ``Enter`` initializes immediately.

        Args:
            None: Called by Textual after the dialog is displayed.

        Returns:
            None: Keyboard focus moves to the *Initialize* button.

        Raises:
            None: The button always exists.
        """
        self.query_one("#submit", Button).focus()

    def action_submit(self) -> None:
        """Close with the operator's key-generation choice.

        Args:
            None: The checkbox (when shown) is read.

        Returns:
            None: The dialog closes with ``True`` or ``False``.

        Raises:
            None: The checkbox query tolerates its absence.
        """
        choice = self.query("#generate-key").results(Checkbox)
        self.dismiss(any(checkbox.value for checkbox in choice))
