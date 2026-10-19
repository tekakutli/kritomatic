"""
pair_html.py — assemble the pair playground's HTML page and module dict.
"""

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_PARENT = os.path.dirname(_HERE)
for _p in (_HERE, _PARENT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from pg_base           import PAIR_CSS, PAIR_PANEL_BODY
from pg_core           import CORE_JS
from pg_view_canvas    import CANVAS_VIEW_JS
from pg_view_segments  import SEGMENTS_VIEW_JS
from pg_scene          import SCENE_JS
from pg_export         import EXPORT_JS
from pg_kra            import KRA_JS
from pg_panel_fields   import PAIR_PANEL_FIELDS_JS
from pg_panel_lists    import PAIR_PANEL_LISTS_JS
from pg_panel_scrubs   import PAIR_PANEL_SCRUBS_JS
from pg_panel_bindings import PAIR_PANEL_BINDINGS_JS
from pg_dispatch       import DISPATCH_JS

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
  pgkitNotifyAll,
} from "./pgkit/remote_client.js";
import { setPanelDraw }                     from "./pgkit/panel_core.js";
import {
  installPanelBindings, pgkitHandleSelect, pairBootSync,
} from "./pg_panel_bindings.js";
import { draw }                             from "./pg_dispatch.js";
import {
  resize, setAfterChange, pairState, _setDraw,
} from "./pg_core.js";
import { syncPairList }                     from "./pg_panel_lists.js";

// Wire kit's injectable slots.
_setDraw(draw);
setPanelDraw(draw);
setRemoteDraw(draw);
setRemoteSelect(pgkitHandleSelect);
setRemoteDeselect(() => {
  pairState.selectedPair = -1;
  pairState.selectedSegment = null;
  syncPairList();
});

// Wire pg_core's after-change hook.
setAfterChange(() => {
  syncPairList();
  draw();
  pgkitNotifyAll();
});

// Boot.
installShellControls();
installPanelBindings();
pairBootSync();
resize();
"""


_MODULE_SOURCES = {
    **pgkit_modules(),
    "pg_core.js":             CORE_JS,
    "pg_view_canvas.js":      CANVAS_VIEW_JS,
    "pg_view_segments.js":    SEGMENTS_VIEW_JS,
    "pg_scene.js":            SCENE_JS,
    "pg_export.js":           EXPORT_JS,
    "pg_kra.js":              KRA_JS,
    "pg_panel_fields.js":     PAIR_PANEL_FIELDS_JS,
    "pg_panel_lists.js":      PAIR_PANEL_LISTS_JS,
    "pg_panel_scrubs.js":     PAIR_PANEL_SCRUBS_JS,
    "pg_panel_bindings.js":   PAIR_PANEL_BINDINGS_JS,
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
            title          = "Pairs — viewer",
            panel_title    = "Pairs",
            panel_body     = PAIR_PANEL_BODY,
            module_sources = _MODULE_SOURCES,
            boot_module    = BOOT_MODULE,
            extra_css      = PAIR_CSS,
            status_text    = "drag pairs &middot; drag segment edges below",
        )
    except JsSyntaxError as err:
        _report_and_exit(err)
