"""
tb_panel.py — assembler metadata for the text board panel's JS.

Six pieces, concatenated in dependency order.  The kit supplies
`PANEL_CORE_JS` (flashStatus and _installScrubInput); the other five
are text_board's.

    pgkit.panel_core       flashStatus and _installScrubInput.
    tb_panel_fields.py     _syncEditor, _installEditorBindings,
                           _applyItemField, the per-item debounce
                           buffer, and the card-width slider sync.
    tb_panel_lists.py      syncItemList, cursor readout,
                           canvas-JSON export.
    tb_panel_scrubs.py     pgkitRegisterField for view.*, board.*,
                           item.*, plus slider/checkbox bindings.
    tb_panel_bindings.py   buttons, actions, levels,
                           pgkitHandleSelect, pgkitBootSync.

Two exports:

    PANEL_SEGMENTS   [(label, js), ...] — one entry per source file,
                     for callers that want per-file sourceURL
                     attribution in DevTools.  `text_board_html.py`
                     uses this form.

    PANEL_JS         the concatenation of every JS body, for callers
                     that just want one blob.
"""

from pgkit.panel_core  import PANEL_CORE_JS
from tb_panel_fields   import TB_PANEL_FIELDS_JS
from tb_panel_lists    import TB_PANEL_LISTS_JS
from tb_panel_scrubs   import TB_PANEL_SCRUBS_JS
from tb_panel_bindings import TB_PANEL_BINDINGS_JS


PANEL_SEGMENTS = [
    ("pgkit/panel_core.py",  PANEL_CORE_JS),
    ("tb_panel_fields.py",   TB_PANEL_FIELDS_JS),
    ("tb_panel_lists.py",    TB_PANEL_LISTS_JS),
    ("tb_panel_scrubs.py",   TB_PANEL_SCRUBS_JS),
    ("tb_panel_bindings.py", TB_PANEL_BINDINGS_JS),
]

PANEL_JS = "".join(js for _label, js in PANEL_SEGMENTS)
