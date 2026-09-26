# Copyright 2026 Robert Patrick
# SPDX-License-Identifier: Apache-2.0

"""Colors and the Textual theme for the axlib credential manager.

Most operators run terminals with a black or near-black background, so every
text color here is bright: white, cyan, blue, magenta, orange, yellow, and
green.  The palette is defined once as named constants and reused both by the
Textual theme (which styles widgets through CSS variables such as ``$primary``)
and by table cells that are colored individually.

Textual detects what the terminal supports.  On a 256- or 16-color terminal it
maps each color to the nearest available one, and when the ``NO_COLOR``
environment variable is set it renders in monochrome -- no code here needs to
check terminal capabilities.

Dependencies:
    ``textual`` (the optional ``tui`` extra: ``uv add 'axlib[tui]'``).

Example:
    >>> from axlib.credentials.tui.theme import AXLIB_DARK
    >>> AXLIB_DARK.name
    'axlib-dark'
"""

from __future__ import annotations

from textual.theme import Theme

from axlib.credentials.admin import NoteKind

# Bright colors that stay readable on a black background.
WHITE = "#ffffff"
GREY = "#c8c8c8"
CYAN = "#00e5ff"
BLUE = "#5aa9ff"
MAGENTA = "#ff6bff"
ORANGE = "#ffa640"
YELLOW = "#ffeb5a"
GREEN = "#5cff8f"
RED = "#ff5c6c"

# Backgrounds: pure black plus a barely lifted panel for headers and dialogs.
BLACK = "#000000"
PANEL = "#12161c"

AXLIB_DARK = Theme(
    name="axlib-dark",
    primary=CYAN,  # borders, focus highlights
    secondary=BLUE,  # selected rows, focused inputs
    accent=ORANGE,  # key hints in the footer
    warning=YELLOW,
    error=RED,
    success=GREEN,
    foreground=WHITE,
    background=BLACK,
    surface=BLACK,
    panel=PANEL,
    dark=True,
    # Theme variables override Textual's generated defaults for specific
    # widgets, keeping those colors in this one module too.  Names starting
    # with "axlib-" are this application's own additions, usable in CSS as
    # $axlib-title and $axlib-muted.
    variables={
        "axlib-title": MAGENTA,
        "axlib-muted": GREY,
        "footer-background": PANEL,
        "footer-key-foreground": ORANGE,
        "footer-description-foreground": WHITE,
        "block-cursor-background": BLUE,
        "block-cursor-foreground": BLACK,
        "block-cursor-text-style": "bold",
        "input-selection-background": f"{BLUE} 40%",
    },
)

# Colors for the NOTES column, keyed by the advice category from the admin API.
NOTE_COLORS: dict[NoteKind, str] = {
    NoteKind.OPERATOR: CYAN,
    NoteKind.SHARED: MAGENTA,
    NoteKind.INCOMPLETE: YELLOW,
    NoteKind.OVERRIDDEN: ORANGE,
}
