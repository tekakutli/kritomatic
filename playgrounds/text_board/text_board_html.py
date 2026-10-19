"""
text_board_html.py — assemble the text board's HTML page and module dict.
"""

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_PARENT = os.path.dirname(_HERE)
for _p in (_HERE, _PARENT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from tb_base       import TB_CSS, TB_PANEL_BODY
from tb_core       import CORE_JS
from tb_view       import VIEW_JS
from tb_krita      import KRITA_JS
from tb_panel_fields   import TB_PANEL_FIELDS_JS
from tb_panel_lists    import TB_PANEL_LISTS_JS
from tb_panel_scrubs   import TB_PANEL_SCRUBS_JS
from tb_panel_bindings import TB_PANEL_BINDINGS_JS
from tb_scene      import SCENE_JS
from tb_dispatch   import DISPATCH_JS

from pgkit.html_shell  import page
from pgkit.js_check    import JsSyntaxError
from pgkit.modules     import pgkit_modules


_BOOT_JS = r"""
// Side-effect imports.
import "./tb_panel_scrubs.js";

// Named imports.
import { installShellControls }             from "./pgkit/shell.js";
import {
  setRemoteDraw, setRemoteSelect, setRemoteDeselect,
} from "./pgkit/remote_client.js";
import { setPanelDraw }                     from "./pgkit/panel_core.js";
import {
  installEditorBindings, _syncEditor, syncPanelSliders,
} from "./tb_panel_fields.js";
import {
  installPanelBindings, pgkitHandleSelect, pgkitBootSync,
} from "./tb_panel_bindings.js";
import { draw }                             from "./tb_dispatch.js";
import { board, resize, _setDraw }          from "./tb_core.js";
import { syncItemList }                     from "./tb_panel_lists.js";

// Wire kit's injectable slots.
_setDraw(draw);
setPanelDraw(draw);
setRemoteDraw(draw);
setRemoteSelect(pgkitHandleSelect);
setRemoteDeselect(() => {
  board.selectedIdx = -1;
  syncItemList();
  _syncEditor();
});

// Boot.
installShellControls();
installEditorBindings();
installPanelBindings();
pgkitBootSync();
resize();
"""


_MODULE_SOURCES = {
    **pgkit_modules(),
    "tb_core.js":             CORE_JS,
    "tb_view.js":             VIEW_JS,
    "tb_krita.js":            KRITA_JS,
    "tb_panel_fields.js":     TB_PANEL_FIELDS_JS,
    "tb_panel_lists.js":      TB_PANEL_LISTS_JS,
    "tb_panel_scrubs.js":     TB_PANEL_SCRUBS_JS,
    "tb_panel_bindings.js":   TB_PANEL_BINDINGS_JS,
    "tb_scene.js":            SCENE_JS,
    "tb_dispatch.js":         DISPATCH_JS,
    "_boot.js":               _BOOT_JS,
}

BOOT_MODULE = "_boot.js"


def module_sources():
    return dict(_MODULE_SOURCES)


def _report_and_exit(err: JsSyntaxError) -> None:
    print(file=sys.stderr)
    print("=" * 60, file=sys.stderr)
    if err.lineno is not None:
        print(f"JS syntax error in {err.label}, line {err.lineno}:", file=sys.stderr)
    else:
        print(f"JS syntax error in {err.label}:", file=sys.stderr)
    print(file=sys.stderr)
    print(f"  {err.message}", file=sys.stderr)
    print(file=sys.stderr)
    for line in err.raw.splitlines():
        print(f"  {line}", file=sys.stderr)
    print("=" * 60, file=sys.stderr)
    sys.exit(1)


def render():
    try:
        return page(
            title          = "Text board — every text shape on a canvas",
            panel_title    = "Text board",
            panel_body     = TB_PANEL_BODY,
            module_sources = _MODULE_SOURCES,
            boot_module    = BOOT_MODULE,
            extra_css      = TB_CSS,
            status_text    = "ready",
        )
    except JsSyntaxError as err:
        _report_and_exit(err)
