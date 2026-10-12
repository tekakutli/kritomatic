"""
pg_panel.py — assembler for the panel's JavaScript.

The panel was one 1000-line module; it is now six focused files, so
editing one concern does not require re-printing the whole panel.
Concatenation order (matters — this is plain JS, not modules):

    pg_panel_core.py        scrub rates, φ display conversion,
                            flashStatus, the scrub-input installer,
                            the five graphics sliders' sync.
    pg_panel_fields.py      setters and sync functions for the
                            patch-position / square-size / mirror /
                            warp / slope / visual-bottom fields.
    pg_panel_lists.py       the patch list and the square list.
    pg_panel_help.py        the help popup's HTML card and the
                            openHelpWindow() function.
    pg_panel_scrubs.py      every _installScrubInput(...) call, in
                            one IIFE.  This is the file to edit
                            when a field's read/write/rate/snap
                            changes.
    pg_panel_bindings.py    the graphics sliders, every button,
                            every checkbox, and the keydown handler
                            that is not a scrub input.  This is the
                            file to edit when a control is added,
                            removed, or rewired.

Everything below is JS concatenated into one <script>; there are no
real module boundaries.  The order above is the only contract.

PANEL SHAPE
===========
The panel is a flex column: a pinned title bar and a scrollable
body.  The body's fields are grouped into five collapsible
<details> blocks (View, Patches, Squares, Draw, Export), all open
by default; the HTML for that lives in pg_base.py, not here.

The JavaScript in these six files wires the fields to the model;
it does not create them.  Every DOM element referenced below is
present in pg_base.py's HTML_HEAD.
"""

from pg_panel_core     import PANEL_CORE_JS
from pg_panel_fields   import PANEL_FIELDS_JS
from pg_panel_lists    import PANEL_LISTS_JS
from pg_panel_help     import PANEL_HELP_JS
from pg_panel_scrubs   import PANEL_SCRUBS_JS
from pg_panel_bindings import PANEL_BINDINGS_JS


PANEL_JS = (
    PANEL_CORE_JS
    + PANEL_FIELDS_JS
    + PANEL_LISTS_JS
    + PANEL_HELP_JS
    + PANEL_SCRUBS_JS
    + PANEL_BINDINGS_JS
)
