"""
pg_panel.py — assembler for the pair panel's JavaScript.

The panel was one big module; it is now four focused files, so
editing one concern does not require re-printing the whole panel.
Concatenation order (matters — this is plain JS, not modules):

    pgkit.panel_core        flashStatus and _installScrubInput.
                            The kit supplies these; the pair
                            playground has no additions on top.

    pg_panel_fields.py      _pairInclinationDeg and
                            _syncPairFieldInputs — the two functions
                            that know what a pair's panel fields
                            mean and push the model's state into the
                            DOM each frame.

    pg_panel_lists.py       syncPairList — rebuild the pair list.

    pg_panel_scrubs.py      every scrub _installScrubInput(...) call
                            and every pgkitRegisterField(...) for the
                            seven pair scrub fields.

    pg_panel_bindings.py    the buttons, checkboxes, KRA selects,
                            the help popup, the keydown handler,
                            and the pgkitRegisterField(...) blocks
                            for the four non-scrub value fields.

There is no `pg_panel_core.py` in the pair playground.  An earlier
revision had one, but the only two things it held — flashStatus and
_installScrubInput — turned out to be identical between pair and
cone and moved into `pgkit.panel_core`.  Nothing project-specific
was left to put in the wrapper, so the file was deleted.  Cone will
keep a `pg_panel_core.py` after its own migration, because cone's
panel does have project-specific primitives (the phi-display
conversion, the eleven scrub-rate constants, the slider sync
functions) that need a place to live.

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

from pgkit.panel_core  import PANEL_CORE_JS
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
