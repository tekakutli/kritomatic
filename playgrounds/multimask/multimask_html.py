"""Multimask HTML assembly and module manifest."""

import sys

from .pg_base           import PG_CSS, PG_PANEL_BODY
from .pg_tuning         import TUNING_JS
from .pg_state          import STATE_JS
from .pg_pixels         import PIXELS_JS
from .pg_areas          import AREAS_JS
from .pg_view_image     import VIEW_IMAGE_JS
from .pg_view           import VIEW_JS
from .pg_dispatch       import DISPATCH_JS
from .pg_client         import CLIENT_JS
from .pg_panel_fields   import PANEL_FIELDS_JS
from .pg_panel_lists    import PANEL_LISTS_JS
from .pg_panel_bindings import PANEL_BINDINGS_JS

from pgkit.html_shell import page
from pgkit.js_check   import JsSyntaxError
from pgkit.modules    import pgkit_modules


_BOOT_JS = r"""
import { installShellControls } from "./pgkit/shell.js";
import {
  setRemoteDraw, setRemoteSelect, setRemoteDeselect,
} from "./pgkit/remote_client.js";
import { setPanelDraw } from "./pgkit/panel_core.js";
import {
  installPanelBindings, pgkitHandleSelect, multimaskBootSync,
} from "./pg_panel_bindings.js";
import { draw } from "./pg_dispatch.js";
import { resize, _setDraw } from "./pg_state.js";
import { syncAreaList } from "./pg_panel_lists.js";
import { syncPanelFields } from "./pg_panel_fields.js";
import { selectArea } from "./pg_areas.js";

(async () => {
  console.log("[boot] modules loaded");
  try {
    _setDraw(draw);                     console.log("[boot] _setDraw");
    setPanelDraw(draw);                 console.log("[boot] setPanelDraw");
    setRemoteDraw(draw);                console.log("[boot] setRemoteDraw");
    setRemoteSelect(pgkitHandleSelect); console.log("[boot] setRemoteSelect");
    setRemoteDeselect(() => {
      selectArea(null);
      syncAreaList();
      syncPanelFields();
    });

    installShellControls();
    installPanelBindings();
    await multimaskBootSync();
    resize();
    console.log("[boot] BOOT COMPLETE");
  } catch (e) {
    console.error("[boot] FAILED:", e);
    console.error(e && e.stack);
  }
})();
"""


_MODULE_SOURCES = {
    **pgkit_modules(),
    "pg_tuning.js":         TUNING_JS,
    "pg_state.js":          STATE_JS,
    "pg_pixels.js":         PIXELS_JS,
    "pg_areas.js":          AREAS_JS,
    "pg_view_image.js":     VIEW_IMAGE_JS,
    "pg_view.js":           VIEW_JS,
    "pg_dispatch.js":       DISPATCH_JS,
    "pg_client.js":         CLIENT_JS,
    "pg_panel_fields.js":   PANEL_FIELDS_JS,
    "pg_panel_lists.js":    PANEL_LISTS_JS,
    "pg_panel_bindings.js": PANEL_BINDINGS_JS,
    "_boot.js":             _BOOT_JS,
}

BOOT_MODULE = "_boot.js"


def module_sources():
    return dict(_MODULE_SOURCES)


def render():
    try:
        return page(
            title          = "Multimask",
            panel_title    = "Multimask",
            panel_body     = PG_PANEL_BODY,
            module_sources = _MODULE_SOURCES,
            boot_module    = BOOT_MODULE,
            extra_css      = PG_CSS,
            status_text    = "idle",
        )
    except JsSyntaxError as err:
        print(file=sys.stderr)
        print("=" * 60, file=sys.stderr)
        print(f"JS syntax error in {err.label}", file=sys.stderr)
        print(f"  {err.message}", file=sys.stderr)
        print("=" * 60, file=sys.stderr)
        sys.exit(1)