"""SAM3 playground HTML assembly and module manifest.

MODULE MANIFEST
===============
The module list is ordered from leaf to entry point.  `pgkit_modules()`
supplies the shared shell.  Every other module is a Python-side string
constant (the `_JS` suffix) imported from its own Python file; the
file name in `_MODULE_SOURCES` is the `.js` name the browser sees.

The order in the manifest does not affect runtime — every module is an
ES module with its own imports resolved at load time.  It only affects
the readability of `curl /modules/pg_state.js` debug output.

This playground is segmentation-only.  Inpainting lives in
`playgrounds/inpaint`; its `pg_inpaint.js` and `pg_view_inpaint.js`
modules are not part of this manifest."""

import os
import sys

_HERE   = os.path.dirname(os.path.abspath(__file__))
_PARENT = os.path.dirname(_HERE)
for _p in (_HERE, _PARENT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from pg_base import PG_CSS, PG_PANEL_BODY
from pg_tuning import TUNING_JS
from pg_state import STATE_JS
from pg_pixels import PIXELS_JS
from pg_masks import MASKS_JS
from pg_region import REGION_JS
from pg_oplog import OPLOG_JS
from pg_composite import COMPOSITE_JS
from pg_view import VIEW_JS
from pg_view_image import VIEW_IMAGE_JS
from pg_view_points import VIEW_POINTS_JS
from pg_view_region import VIEW_REGION_JS
from pg_view_brush import VIEW_BRUSH_JS
from pg_dispatch import DISPATCH_JS
from pg_comfy_client import COMFY_CLIENT_JS
from pg_krita_client import KRITA_CLIENT_JS
from pg_panel_fields import PANEL_FIELDS_JS
from pg_panel_lists import PANEL_LISTS_JS
from pg_panel_scrubs import PANEL_SCRUBS_JS
from pg_panel_bindings import PANEL_BINDINGS_JS

from pgkit.html_shell import page
from pgkit.js_check import JsSyntaxError
from pgkit.modules import pgkit_modules


_BOOT_JS = r"""
import "./pg_panel_scrubs.js";

import { installShellControls }             from "./pgkit/shell.js";
import {
  setRemoteDraw, setRemoteSelect, setRemoteDeselect, pgkitNotifyAll,
} from "./pgkit/remote_client.js";
import { setPanelDraw }                     from "./pgkit/panel_core.js";
import {
  installPanelBindings, pgkitHandleSelect, samBootSync,
} from "./pg_panel_bindings.js";
import { draw }                             from "./pg_dispatch.js";
import { board, resize, _setDraw }          from "./pg_state.js";
import { syncMaskList }                     from "./pg_panel_lists.js";
import { syncPanelFields }                  from "./pg_panel_fields.js";

console.log("[boot] modules loaded");

try {
  _setDraw(draw);                     console.log("[boot] _setDraw");
  setPanelDraw(draw);                 console.log("[boot] setPanelDraw");
  setRemoteDraw(draw);                console.log("[boot] setRemoteDraw");
  setRemoteSelect(pgkitHandleSelect); console.log("[boot] setRemoteSelect");
  setRemoteDeselect(() => {
    board.selectedMaskIdx = -1;
    syncMaskList();
    syncPanelFields();
  });

  installShellControls();     console.log("[boot] installShellControls");
  installPanelBindings();     console.log("[boot] installPanelBindings");
  samBootSync();              console.log("[boot] samBootSync");
  resize();                   console.log("[boot] BOOT COMPLETE");
} catch (e) {
  console.error("[boot] FAILED:", e);
  console.error(e && e.stack);
}
"""


_MODULE_SOURCES = {
    **pgkit_modules(),
    "pg_tuning.js":           TUNING_JS,
    "pg_state.js":            STATE_JS,
    "pg_pixels.js":           PIXELS_JS,
    "pg_masks.js":            MASKS_JS,
    "pg_region.js":           REGION_JS,
    "pg_oplog.js":            OPLOG_JS,
    "pg_composite.js":        COMPOSITE_JS,
    "pg_view.js":             VIEW_JS,
    "pg_view_image.js":       VIEW_IMAGE_JS,
    "pg_view_points.js":      VIEW_POINTS_JS,
    "pg_view_region.js":      VIEW_REGION_JS,
    "pg_view_brush.js":       VIEW_BRUSH_JS,
    "pg_dispatch.js":         DISPATCH_JS,
    "pg_comfy_client.js":     COMFY_CLIENT_JS,
    "pg_krita_client.js":     KRITA_CLIENT_JS,
    "pg_panel_fields.js":     PANEL_FIELDS_JS,
    "pg_panel_lists.js":      PANEL_LISTS_JS,
    "pg_panel_scrubs.js":     PANEL_SCRUBS_JS,
    "pg_panel_bindings.js":   PANEL_BINDINGS_JS,
    "_boot.js":               _BOOT_JS,
}

BOOT_MODULE = "_boot.js"


def module_sources():
    return dict(_MODULE_SOURCES)


def render():
    try:
        return page(
            title          = "SAM3 segmentation playground",
            panel_title    = "SAM3 Segment",
            panel_body     = PG_PANEL_BODY,
            module_sources = _MODULE_SOURCES,
            boot_module    = BOOT_MODULE,
            extra_css      = PG_CSS,
            status_text    = "drop an image to begin",
        )
    except JsSyntaxError as err:
        print(file=sys.stderr)
        print("=" * 60, file=sys.stderr)
        print(f"JS syntax error in {err.label}", file=sys.stderr)
        print(f"  {err.message}", file=sys.stderr)
        print("=" * 60, file=sys.stderr)
        sys.exit(1)
