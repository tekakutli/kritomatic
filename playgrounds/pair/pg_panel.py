"""
pg_panel.py — assembler for the panel's JavaScript.

The panel was one big module; it is now six focused files, so
editing one concern does not require re-printing the whole panel.
Concatenation order (matters — this is plain JS, not modules):

    pg_panel_core.py        flashStatus, _installScrubInput, the
                            scrub-drag threshold, and any other
                            panel-wide primitive.
    pg_panel_fields.py      _pairInclinationDeg and
                            _syncPairFieldInputs — the two functions
                            that know what a pair's panel fields
                            mean and push the model's state into the
                            DOM each frame.
    pg_panel_lists.py       syncPairList — rebuild the pair list.
    pg_panel_scrubs.py      every _installScrubInput(...) call, in
                            one IIFE.  This is the file to edit when
                            a field's read/write/rate/snap changes.
    pg_panel_bindings.py    the buttons, the help popup, and the
                            keydown handler that is not a scrub
                            input.  This is the file to edit when a
                            control is added, removed, or rewired.

Everything below is JS concatenated into one <script>; there are no
real module boundaries.  The order above is the only contract.

PANEL SHAPE
===========
The panel is a flex column: a pinned title bar and a scrollable
body.  The body's fields are grouped into four collapsible
<details> blocks (Pair, Segments, Export, KRA), all open by
default; the HTML for that lives in pg_base.py, not here.

The JavaScript in these five files wires the fields to the model;
it does not create them.  Every DOM element referenced below is
present in pg_base.py's HTML_HEAD.

WHY THIS SPLIT
==============
cone_playground made the same split of its panel: the primitives,
the field semantics, the lists, the scrub bindings, and the
remaining bindings live in separate files.  When you are iterating
on a single scrub field's rate, only pg_panel_scrubs.py changes.
When you are iterating on the meaning of a field's value, only
pg_panel_fields.py changes.  When you are adding a button,
pg_panel_bindings.py.  The rest of the panel — and every other JS
file in the project — stays untouched.
"""

from pg_panel_core     import PANEL_CORE_JS
from pg_panel_fields   import PANEL_FIELDS_JS
from pg_panel_lists    import PANEL_LISTS_JS
from pg_panel_scrubs   import PANEL_SCRUBS_JS
from pg_panel_bindings import PANEL_BINDINGS_JS


PANEL_JS = (
    PANEL_CORE_JS
    + PANEL_FIELDS_JS
    + PANEL_LISTS_JS
    + PANEL_SCRUBS_JS
    + PANEL_BINDINGS_JS
)
