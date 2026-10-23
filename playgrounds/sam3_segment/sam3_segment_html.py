"""SAM3 playground HTML assembly and module manifest."""

import os
import sys

_HERE   = os.path.dirname(os.path.abspath(__file__))
_PARENT = os.path.dirname(_HERE)
for _p in (_HERE, _PARENT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from pg_base import PG_CSS, PG_PANEL_BODY
from pg_core import CORE_JS
from pg_view import VIEW_JS
from pg_dispatch import DISPATCH_JS
from pg_comfy_client import COMFY_CLIENT_JS
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
import {
  board, resize, _setDraw,
} from "./pg_core.js";
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
    "pg_core.js":             CORE_JS,
    "pg_view.js":             VIEW_JS,
    "pg_dispatch.js":         DISPATCH_JS,
    "pg_comfy_client.js":     COMFY_CLIENT_JS,
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
