"""Controlnets HTML assembly and module manifest.

Ordered leaf → entry point.  `pgkit_modules()` supplies the shared
shell.  Every other module is a Python-side string constant imported
from its own file; the key in `_MODULE_SOURCES` is the URL path the
browser sees under `/modules/`.
"""

import sys

from .pg_base       import PG_CSS, PG_PANEL_BODY
from .pg_tuning     import TUNING_JS
from .pg_state      import STATE_JS
from .pg_client     import CLIENT_JS
from .pg_view       import VIEW_JS
from .pg_view_image import VIEW_IMAGE_JS
from .pg_dispatch   import DISPATCH_JS
from .pg_panel      import PANEL_JS
from .pg_autosync   import AUTOSYNC_JS

from pgkit.html_shell import page
from pgkit.js_check   import JsSyntaxError
from pgkit.modules     import pgkit_modules
from pgruntime.modules import pgruntime_modules


_BOOT_JS = r"""
import { installShellControls } from "./pgkit/shell.js";
import {
  setRemoteDraw, setRemoteSelect, setRemoteDeselect,
} from "./pgkit/remote_client.js";
import { setPanelDraw } from "./pgkit/panel_core.js";
import { draw }     from "./pg_dispatch.js";
import { drawView } from "./pg_view.js";
import {
  resize, _setDraw, _setDrawView,
} from "./pg_state.js";
import { installPanelBindings, controlnetsBootSync } from "./pg_panel.js";
import { startAutosync } from "./pg_autosync.js";

console.log("[boot] modules loaded");

try {
  _setDraw(draw);
  _setDrawView(drawView);
  setPanelDraw(draw);
  setRemoteDraw(draw);
  setRemoteSelect(() => false);
  setRemoteDeselect(() => {});

  installShellControls();
  installPanelBindings();
  controlnetsBootSync();
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
    "pg_tuning.js":       TUNING_JS,
    "pg_state.js":        STATE_JS,
    "pg_client.js":       CLIENT_JS,
    "pg_view.js":         VIEW_JS,
    "pg_view_image.js":   VIEW_IMAGE_JS,
    "pg_dispatch.js":     DISPATCH_JS,
    "pg_panel.js":        PANEL_JS,
    "pg_autosync.js":     AUTOSYNC_JS,
    "_boot.js":           _BOOT_JS,
}

BOOT_MODULE = "_boot.js"


def module_sources():
    return dict(_MODULE_SOURCES)


def render():
    try:
        return page(
            title          = "Controlnets",
            panel_title    = "Controlnets",
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
