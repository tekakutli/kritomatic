"""
pg_panel.py — assembler metadata for the board panel's JS.

Five pieces, concatenated in dependency order.  The kit supplies
`PANEL_CORE_JS` (flashStatus and _installScrubInput); the other four
are board's.

    pgkit.panel_core       flashStatus and _installScrubInput.
    pg_panel_fields.py     selectedFile, _syncCardFields.
    pg_panel_lists.py      syncFileList.
    pg_panel_scrubs.py     syncPanelSliders / updatePanelLabels,
                           DOM bindings, field registrations.
    pg_panel_bindings.py   buttons, actions, levels,
                           pgkitHandleSelect, boardBootSync.

Two exports:

    PANEL_SEGMENTS   [(label, js), ...] — one entry per source file,
                     for callers that want per-file sourceURL
                     attribution in DevTools.  `board_html.py` uses
                     this form.

    PANEL_JS         the concatenation of every JS body, for callers
                     that just want one blob.
"""

from pgkit.panel_core  import PANEL_CORE_JS
from pg_panel_fields   import PG_PANEL_FIELDS_JS
from pg_panel_lists    import PG_PANEL_LISTS_JS
from pg_panel_scrubs   import PG_PANEL_SCRUBS_JS
from pg_panel_bindings import PG_PANEL_BINDINGS_JS


PANEL_SEGMENTS = [
    ("pgkit/panel_core.py",  PANEL_CORE_JS),
    ("pg_panel_fields.py",   PG_PANEL_FIELDS_JS),
    ("pg_panel_lists.py",    PG_PANEL_LISTS_JS),
    ("pg_panel_scrubs.py",   PG_PANEL_SCRUBS_JS),
    ("pg_panel_bindings.py", PG_PANEL_BINDINGS_JS),
]

PANEL_JS = "".join(js for _label, js in PANEL_SEGMENTS)
