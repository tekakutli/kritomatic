"""Interceptor playground HTML assembly and module manifest.

Small manifest: state, view, dispatch, client, panel_fields,
panel_bindings.  No scrubs module — the interceptor's only editable
input is the prompt textarea, which lives in panel_bindings.
"""

import sys

from .pg_base           import PG_CSS, PG_PANEL_BODY
from .pg_state          import STATE_JS
from .pg_view           import VIEW_JS
from .pg_dispatch       import DISPATCH_JS
from .pg_client         import CLIENT_JS
from .pg_panel_fields   import PANEL_FIELDS_JS
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
  installPanelBindings, interceptorBootSync,
} from "./pg_panel_bindings.js";
import { draw } from "./pg_dispatch.js";
import { resize, _setDraw } from "./pg_state.js";

console.log("[boot] modules loaded");

try {
  _setDraw(draw);
  setPanelDraw(draw);
  setRemoteDraw(draw);
  setRemoteSelect(() => false);
  setRemoteDeselect(() => {});

  installShellControls();
  installPanelBindings();
  interceptorBootSync();
  resize();
  console.log("[boot] BOOT COMPLETE");
} catch (e) {
  console.error("[boot] FAILED:", e);
  console.error(e && e.stack);
}
"""


_MODULE_SOURCES = {
    **pgkit_modules(),
    "pg_state.js":           STATE_JS,
    "pg_view.js":            VIEW_JS,
    "pg_dispatch.js":        DISPATCH_JS,
    "pg_client.js":          CLIENT_JS,
    "pg_panel_fields.js":    PANEL_FIELDS_JS,
    "pg_panel_bindings.js":  PANEL_BINDINGS_JS,
    "_boot.js":              _BOOT_JS,
}

BOOT_MODULE = "_boot.js"


def module_sources():
    return dict(_MODULE_SOURCES)


def render():
    try:
        return page(
            title          = "Interceptor",
            panel_title    = "Interceptor",
            panel_body     = PG_PANEL_BODY,
            module_sources = _MODULE_SOURCES,
            boot_module    = BOOT_MODULE,
            extra_css      = PG_CSS,
            status_text    = "waiting for a task",
        )
    except JsSyntaxError as err:
        print(file=sys.stderr)
        print("=" * 60, file=sys.stderr)
        print(f"JS syntax error in {err.label}", file=sys.stderr)
        print(f"  {err.message}", file=sys.stderr)
        print("=" * 60, file=sys.stderr)
        sys.exit(1)