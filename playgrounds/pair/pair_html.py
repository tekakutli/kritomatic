"""
pair_html.py — assemble the pair playground's HTML page and module
manifest.

Entry point.  `render()` is the only public function; it asks
pgkit's `page()` to build the shell, passing the project's
`_MODULE_SOURCES` dict (one entry per JS file — kit's own modules
plus every pair module) and the name of the boot module.

MODULE MANIFEST
===============
`_MODULE_SOURCES` maps URL paths *under* `/modules/` to JS source
strings.  The boot module is `_boot.js`; it does all the `import`
wiring and calls the shell, panel, and boot-sync functions in the
right order.

`_boot.js` imports, in order:

    · "./pg_panel_scrubs.js"        (side effect: field registrations)
    · installShellControls          from pgkit/shell
    · setRemoteDraw/Select/Deselect from pgkit/remote_client
    · setPanelDraw                  from pgkit/panel_core
    · installPanelBindings,
      pgkitHandleSelect,
      pairBootSync                  from pg_panel_bindings
    · draw                          from pg_dispatch
    · resize, setAfterChange,
      pairState, _setDraw           from pg_core
    · syncPairList                  from pg_panel_lists

Then it wires five injected callbacks before boot:

    _setDraw(draw)                  → pg_core's internal _drawRef
    setPanelDraw(draw)              → pgkit/panel_core's _drawFn
    setRemoteDraw(draw)             → pgkit/remote_client's _drawFn
    setRemoteSelect(pgkitHandleSelect)
    setRemoteDeselect(...)          → clears pairState, syncs list

and one after-change hook:

    setAfterChange(() => {
      syncPairList();
      draw();
      pgkitNotifyAll();
    })

The after-change hook is what `notifyChanged()` in pg_core calls;
all lifecycle mutators route through it, so any model change
refreshes the panel, canvas, and remote client in one pass.

WHY A MANIFEST
==============
The browser loads each module over HTTP.  `page()` needs the
source strings to run `node --check` on each one before serving
HTML, and `serve()` needs them to respond to `GET /modules/<name>`.
One dict for both sides prevents drift between "which file exists"
and "which file is served".

SYNTAX ERRORS
=============
`page()` runs `pgkit.js_check.check_js` on every module before
assembling HTML.  A syntax error — including the "missing export"
class of error that blanked the canvas before this fix — is
reported with the source file and line, and exits with status 1.
No HTML is written; no server starts.  That way the failure mode
is a clear diagnostic, not a page-shaped shell that does nothing.
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
// Side-effect imports (registers fields).
import "./pg_panel_scrubs.js";

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

console.log("[boot] modules loaded");

try {
  _setDraw(draw);                   console.log("[boot] _setDraw ok");
  setPanelDraw(draw);               console.log("[boot] setPanelDraw ok");
  setRemoteDraw(draw);              console.log("[boot] setRemoteDraw ok");
  setRemoteSelect(pgkitHandleSelect); console.log("[boot] setRemoteSelect ok");
  setRemoteDeselect(() => {
    pairState.selectedPair = -1;
    pairState.selectedSegment = null;
    syncPairList();
  });
  setAfterChange(() => {
    syncPairList();
    draw();
    pgkitNotifyAll();
  });                                console.log("[boot] setAfterChange ok");

  installShellControls();           console.log("[boot] installShellControls ok");
  installPanelBindings();           console.log("[boot] installPanelBindings ok");
  pairBootSync();                   console.log("[boot] pairBootSync ok");
  resize();                         console.log("[boot] resize ok, BOOT COMPLETE");
} catch (e) {
  console.error("[boot] FAILED:", e);
  console.error(e && e.stack);
}
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
