"""
board_html.py — assemble the board's HTML page and module dict.
"""

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_PARENT = os.path.dirname(_HERE)
for _p in (_HERE, _PARENT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from pg_base       import PG_CSS, PG_PANEL_BODY
from pg_core       import CORE_JS
from pg_view_board import VIEW_BOARD_JS
from pg_krita      import KRITA_JS
from pg_panel_fields   import PG_PANEL_FIELDS_JS
from pg_panel_lists    import PG_PANEL_LISTS_JS
from pg_panel_scrubs   import PG_PANEL_SCRUBS_JS
from pg_panel_bindings import PG_PANEL_BINDINGS_JS
from pg_scene      import SCENE_JS
from pg_dispatch   import DISPATCH_JS

from pgkit.html_shell  import page
from pgkit.js_check    import JsSyntaxError
from pgkit.modules     import pgkit_modules


_BOOT_JS = r"""
// Side-effect imports.
import "./pg_panel_scrubs.js";

// Named imports.
import { installShellControls }             from "./pgkit/shell.js";
import {
  setRemoteDraw, setRemoteSelect, setRemoteDeselect,
} from "./pgkit/remote_client.js";
import { setPanelDraw }                     from "./pgkit/panel_core.js";
import {
  installPanelBindings, boardBootSync, pgkitHandleSelect,
} from "./pg_panel_bindings.js";
import { draw }                             from "./pg_dispatch.js";
import { board, resize, _setDraw }          from "./pg_core.js";
import { syncFileList }                     from "./pg_panel_lists.js";

// Wire kit's injectable slots.
_setDraw(draw);
setPanelDraw(draw);
setRemoteDraw(draw);
setRemoteSelect(pgkitHandleSelect);
setRemoteDeselect(() => {
  board.selectedIdx = -1;
  syncFileList();
});

// Boot.
installShellControls();
installPanelBindings();
boardBootSync();
resize();
"""


_MODULE_SOURCES = {
    **pgkit_modules(),
    "pg_core.js":             CORE_JS,
    "pg_view_board.js":       VIEW_BOARD_JS,
    "pg_krita.js":            KRITA_JS,
    "pg_panel_fields.js":     PG_PANEL_FIELDS_JS,
    "pg_panel_lists.js":      PG_PANEL_LISTS_JS,
    "pg_panel_scrubs.js":     PG_PANEL_SCRUBS_JS,
    "pg_panel_bindings.js":   PG_PANEL_BINDINGS_JS,
    "pg_scene.js":            SCENE_JS,
    "pg_dispatch.js":         DISPATCH_JS,
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
            title          = "Board — files on a canvas",
            panel_title    = "Board",
            panel_body     = PG_PANEL_BODY,
            module_sources = _MODULE_SOURCES,
            boot_module    = BOOT_MODULE,
            extra_css      = PG_CSS,
            status_text    = "ready",
        )
    except JsSyntaxError as err:
        _report_and_exit(err)
