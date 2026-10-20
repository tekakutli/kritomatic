"""
boxes_html.py — assemble the boxes playground's HTML page and module
manifest.

Entry point.  `render(geom)` is the only function; it asks pgkit's
`page()` to build the shell, passing the project's `_MODULE_SOURCES`
dict (one entry per JS file — kit's own modules plus every boxes
module) and the name of the boot module.  `module_sources(geom)`
returns the same dict for `kit_serve()`.

GEOMETRY AS A MODULE
====================
The room geometry from `room_walls.json` is passed to `render()` as a
dict.  In the concatenation model it was injected into the `<script>`
as `const GEOMETRY = ...`.  In the ES-module model it is generated
as its own module (`bx_geometry.js`) so every other module imports
it via `import { GEOMETRY } from "./bx_geometry.js"`.  This keeps
the whole project uniform: no module relies on a variable injected
into the page, every module declares its dependencies.

BOOT MODULE — DEFENSIVE BY DESIGN
=================================
`_boot.js` uses dynamic `import()` inside an async IIFE for every
dependency, and wraps each import and each wiring step in its own
try/catch.  Same shape as board/pair/text_board/cone.  Consequences:

    · A module failing to load produces exactly one console line
      `[boot] FAIL import <key> → <error>` before the boot aborts.

    · Each wiring step is logged on its own line.

    · Everything logs with `console.log` so DevTools filters cannot
      hide a failure.

    · The boot logs `[boot] BOOT COMPLETE` on success; its absence
      is the fastest failure signal.

WIRING CALLBACKS
================
Five injected callbacks are wired before any boot action:

    _setDraw(draw)                  bx_core's internal _drawRef
    setPanelDraw(draw)              pgkit/panel_core's _drawFn
    setRemoteDraw(draw)             pgkit/remote_client's _drawFn
    setRemoteSelect(pgkitHandleSelect)
    setRemoteDeselect(() => ...)    clears state, refreshes panel

`boxesBootSync()` performs the initial panel push and notifies the
remote client so a freshly-connected remote panel sees the current
state immediately.

DELETED
=======
    boxes_live.py    (JS aggregator — each JS file is now its own
                     ES module)
    boxes_panel.py   (HTML_HEAD and PANEL_JS split into pg_base +
                     bx_panel_* modules)
    boxes_server.py  (replaced by kit_serve)
"""

import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_PARENT = os.path.dirname(_HERE)
for _p in (_HERE, _PARENT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from pg_base              import PG_CSS, PG_PANEL_BODY
from bx_i18n              import I18N_JS
from bx_base              import BASE_JS
from bx_core              import CORE_JS
from bx_view              import VIEW_JS
from bx_edit              import EDIT_JS
from bx_png               import PNG_JS
from bx_scene             import SCENE_JS
from bx_dispatch          import DISPATCH_JS
from bx_panel_fields      import PANEL_FIELDS_JS
from bx_panel_scrubs      import PANEL_SCRUBS_JS
from bx_panel_bindings    import PANEL_BINDINGS_JS

from pgkit.html_shell  import page
from pgkit.js_check    import JsSyntaxError
from pgkit.modules     import pgkit_modules


_BOOT_JS = r"""
// _boot.js — defensive dynamic-import boot.
//
// Every dependency is loaded with `await import(path)` inside an
// async IIFE.  Each import and each wiring step logs on its own
// console line.  All logs use console.log so no DevTools filter can
// hide them.  A module failure produces exactly one line:
//
//     [boot] FAIL import <key> -> <error>
//     [boot] BOOT ABORTED: <error>
//
// and no wiring steps run.  See boxes_html.py's module docstring for
// the full map of which bug class is caught by which check.

(async () => {
  const log  = (...a) => console.log("[boot]", ...a);
  const fail = (label, e) => log("FAIL", label, "\u2192",
                                 (e && e.message) || e);

  log("starting");

  const importAll = async (entries) => {
    const out = {};
    for (const [key, path] of entries) {
      try {
        out[key] = await import(path);
        log("import", key, "ok");
      } catch (e) {
        fail("import " + key, e);
        throw e;
      }
    }
    return out;
  };

  const step = (name, fn) => {
    try {
      fn();
      log(name, "ok");
    } catch (e) {
      fail(name, e);
    }
  };

  try {
    const m = await importAll([
      ["geom",     "./bx_geometry.js"],
      ["i18n",     "./bx_i18n.js"],
      ["base",     "./bx_base.js"],
      ["core",     "./bx_core.js"],
      ["view",     "./bx_view.js"],
      ["edit",     "./bx_edit.js"],
      ["png",      "./bx_png.js"],
      ["scene",    "./bx_scene.js"],
      ["fields",   "./bx_panel_fields.js"],
      ["scrubs",   "./bx_panel_scrubs.js"],    // side effects only
      ["bindings", "./bx_panel_bindings.js"],
      ["disp",     "./bx_dispatch.js"],
      ["shell",    "./pgkit/shell.js"],
      ["rc",       "./pgkit/remote_client.js"],
      ["pc",       "./pgkit/panel_core.js"],
    ]);

    // Wire bx_core's draw hook.
    step("_setDraw",
         () => m.core._setDraw(m.disp.draw));

    // Wire kit's injectable slots.
    step("setPanelDraw",
         () => m.pc.setPanelDraw(m.disp.draw));
    step("setRemoteDraw",
         () => m.rc.setRemoteDraw(m.disp.draw));
    step("setRemoteSelect",
         () => m.rc.setRemoteSelect(m.bindings.pgkitHandleSelect));
    step("setRemoteDeselect",
         () => m.rc.setRemoteDeselect(() => {
           m.core.board.selectedIdx = -1;
           m.fields._syncSelectionInputs();
           m.fields._boxSyncInspector();
         }));

    // Boot.
    step("installShellControls",  () => m.shell.installShellControls());
    step("installVerticalMenu",   () => m.bindings.installVerticalMenu());
    step("installDarkScreenStyles", () => m.bindings.installDarkScreenStyles());
    step("installInstructionsPopover",
         () => m.bindings.installInstructionsPopover());
    step("installPanelFade",
         () => m.bindings.installPanelFade());
    step("installPanelBindings",  () => m.bindings.installPanelBindings());
    step("boxesBootSync",         () => m.bindings.boxesBootSync());

    // Autoload (async; not awaited as a wiring step so a slow fetch
    // does not block the rest of the boot).
    m.core.autoload().catch(e => fail("autoload", e));

    step("resize",                () => m.core.resize());

    log("BOOT COMPLETE");
  } catch (e) {
    log("BOOT ABORTED:", (e && e.message) || e);
    if (e && e.stack) {
      log("STACK:", e.stack.split("\n").slice(0, 4).join(" | "));
    }
  }
})();
"""


def _build_module_sources(geom):
    """Build the module_sources dict from the geometry dict.

    This is called from both `render()` (for `page()`'s syntax
    pre-check) and `module_sources()` (for `kit_serve()`'s dynamic
    module serving).  Building it fresh each call ensures the two
    sides agree about which files exist and what they contain.
    """
    geometry_js = (
        "/* Generated from room_walls.json at page-build time. */\n"
        "export const GEOMETRY = "
        + json.dumps(geom, separators=(",", ":"))
        + ";\n"
    )
    return {
        **pgkit_modules(),
        "bx_geometry.js":         geometry_js,
        "bx_i18n.js":             I18N_JS,
        "bx_base.js":             BASE_JS,
        "bx_core.js":             CORE_JS,
        "bx_view.js":             VIEW_JS,
        "bx_edit.js":             EDIT_JS,
        "bx_png.js":              PNG_JS,
        "bx_scene.js":            SCENE_JS,
        "bx_dispatch.js":         DISPATCH_JS,
        "bx_panel_fields.js":     PANEL_FIELDS_JS,
        "bx_panel_scrubs.js":     PANEL_SCRUBS_JS,
        "bx_panel_bindings.js":   PANEL_BINDINGS_JS,
        "_boot.js":               _BOOT_JS,
    }


BOOT_MODULE = "_boot.js"


def module_sources(geom):
    return _build_module_sources(geom)


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


def render(geom):
    try:
        return page(
            title          = "Box placement playground",
            panel_title    = "Box planner",
            panel_body     = PG_PANEL_BODY,
            module_sources = _build_module_sources(geom),
            boot_module    = BOOT_MODULE,
            extra_css      = PG_CSS,
            status_text    = "",
        )
    except JsSyntaxError as err:
        _report_and_exit(err)
