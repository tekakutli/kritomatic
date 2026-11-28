"""rmbg playground HTML assembly and module manifest.

Ordered from leaf to entry point.  `pgkit_modules()` supplies the
shared shell; `pgruntime_modules()` supplies the browser-side
daemon client factory; everything else is a Python-side string
constant imported from its own file.

Single canvas (`#c`) — no overlay canvas."""

import sys

from  .pg_base import PG_CSS, PG_PANEL_BODY
from  .pg_tuning import TUNING_JS
from  .pg_state import STATE_JS
from  .pg_pixels import PIXELS_JS
from  .pg_masks import MASKS_JS
from  .pg_context import CONTEXT_JS
from  .pg_view import VIEW_JS
from  .pg_view_image import VIEW_IMAGE_JS
from  .pg_view_context import VIEW_CONTEXT_JS
from  .pg_dispatch import DISPATCH_JS
from  .pg_sync import SYNC_JS
from  .pg_comfy_client import COMFY_CLIENT_JS
from  .pg_krita_client import KRITA_CLIENT_JS
from  .pg_panel_fields import PANEL_FIELDS_JS
from  .pg_panel_lists import PANEL_LISTS_JS
from  .pg_panel_bindings import PANEL_BINDINGS_JS
from  .pg_autosync import AUTOSYNC_JS

from pgkit.html_shell import page
from pgkit.js_check import JsSyntaxError
from pgkit.modules     import pgkit_modules
from pgruntime.modules import pgruntime_modules


_BOOT_JS = r"""
import { installShellControls } from "./pgkit/shell.js";
import {
  setRemoteDraw, setRemoteSelect, setRemoteDeselect, pgkitNotifyAll,
} from "./pgkit/remote_client.js";
import { setPanelDraw } from "./pgkit/panel_core.js";
import {
  installPanelBindings, pgkitHandleSelect, inpaintBootSync,
} from "./pg_panel_bindings.js";
import { draw } from "./pg_dispatch.js";
import { board, resize, _setDraw } from "./pg_state.js";
import { invalidateDrawSig } from "./pg_view.js";
import { syncMaskList } from "./pg_panel_lists.js";
import { syncPanelFields } from "./pg_panel_fields.js";
import { startAutosync } from "./pg_autosync.js";

console.log("[boot] modules loaded");

try {
  _setDraw(draw);
  setPanelDraw(draw);
  setRemoteDraw(draw);
  setRemoteSelect(pgkitHandleSelect);
  setRemoteDeselect(() => {
    board.selectedMaskIndices = [];
    syncMaskList();
    syncPanelFields();
  });

  installShellControls();
  installPanelBindings();

  // Reset-view button.
  const rv = document.getElementById("resetViewBtn");
  if (rv) {
    rv.addEventListener("click", () => {
      import("./pg_state.js").then(m => {
        m.resetView();
        invalidateDrawSig();
        draw();
      });
    });
  }

  inpaintBootSync();
  startAutosync();
  resize();
  console.log("[boot] BOOT COMPLETE");
} catch (e) {
  console.error("[boot] FAILED:", e);
  console.error(e && e.stack);
}
"""


_MODULE_SOURCES = {
    **pgkit_modules(),
    **pgruntime_modules(),
    "pg_tuning.js":           TUNING_JS,
    "pg_state.js":            STATE_JS,
    "pg_pixels.js":           PIXELS_JS,
    "pg_masks.js":            MASKS_JS,
    "pg_context.js":          CONTEXT_JS,
    "pg_view.js":             VIEW_JS,
    "pg_view_image.js":       VIEW_IMAGE_JS,
    "pg_view_context.js":     VIEW_CONTEXT_JS,
    "pg_dispatch.js":         DISPATCH_JS,
    "pg_sync.js":             SYNC_JS,
    "pg_comfy_client.js":     COMFY_CLIENT_JS,
    "pg_krita_client.js":     KRITA_CLIENT_JS,
    "pg_panel_fields.js":     PANEL_FIELDS_JS,
    "pg_panel_lists.js":      PANEL_LISTS_JS,
    "pg_panel_bindings.js":   PANEL_BINDINGS_JS,
    "pg_autosync.js":         AUTOSYNC_JS,
    "_boot.js":               _BOOT_JS,
}

BOOT_MODULE = "_boot.js"


def module_sources():
    return dict(_MODULE_SOURCES)


def render():
    try:
        return page(
            title          = "RMBG playground",
            panel_title    = "RMBG",
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
