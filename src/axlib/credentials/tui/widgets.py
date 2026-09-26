# Copyright 2026 Robert Patrick
# SPDX-License-Identifier: Apache-2.0

"""Reusable widgets for the axlib credential manager.

:class:`SecretInput` is a password box that cannot leak what is typed into it.
Textual's standard ``Input(password=True)`` hides the characters on screen, but
its copy and cut actions still send selected text to the clipboard.  Over SSH,
Textual's clipboard support forwards that text to the operator's *desktop*
clipboard, where clipboard managers may keep a history.  Overriding the two
actions closes that path, which is a small example of the general technique:
subclass a library widget and replace just the behavior you need to change.

Dependencies:
    ``textual`` (the optional ``tui`` extra: ``uv add 'axlib[tui]'``).

Example:
    >>> from axlib.credentials.tui.widgets import SecretInput
    >>> SecretInput(id="netpass").password
    True
"""

from __future__ import annotations

from textual.widgets import Input


class SecretInput(Input):
    """A compact masked input whose contents cannot be copied or cut."""

    def __init__(self, *, placeholder: str = "", id: str | None = None) -> None:  # noqa: A002
        """Create a masked, compact input.

        Args:
            placeholder (str): Hint shown while the input is empty.
            id (str | None): Widget identifier used by CSS and queries.  The
                name ``id`` shadows a Python built-in, but it is Textual's
                standard parameter name, so it is kept for consistency.

        Returns:
            None: Initializers configure the widget in place.

        Raises:
            None: Textual accepts these fixed options.
        """
        super().__init__(placeholder=placeholder, password=True, compact=True, id=id)

    def action_copy(self) -> None:
        """Refuse to copy a secret to the clipboard.

        Args:
            None: Bound to Textual's copy key (normally ``ctrl+c``).

        Returns:
            None: The clipboard is left unchanged.

        Raises:
            None: The terminal bell is the only effect.
        """
        self.app.bell()

    def action_cut(self) -> None:
        """Refuse to cut a secret to the clipboard.

        Args:
            None: Bound to Textual's cut key (normally ``ctrl+x``).

        Returns:
            None: The clipboard and the input are left unchanged.

        Raises:
            None: The terminal bell is the only effect.
        """
        self.app.bell()
