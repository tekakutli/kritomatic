"""
cable_html.py — assemble the cable playground's HTML page and module
manifest.

Entry point.  `render(geom)` is the only public function; it asks
pgkit's `page()` to build the shell, passing the project's
`_MODULE_SOURCES` dict and the name of the boot module.
`module_sources(geom)` returns the same dict for `kit_serve()`.

GEOMETRY AS A MODULE
====================
The room geometry from `room_walls.json` is baked into its own ES
module (`pg_geometry.js`) so every other module imports it via
`import { GEOMETRY } from "./pg_geometry.js"`.

PANEL TITLE
===========
`_PANEL_TITLE` is the h3 content emitted by `page()`.  It carries:

    · the panel title text, wrapped in a `<span data-i18n="appTitleShort">`
      so `installVerticalMenu`'s translation pass can set it from T()
    · the snap pill (`#snapPill`, its state text set by `updateSnapPill`)
    · the help button (`#helpBtn`), whose click handler is attached by
      `installInstructionsPopover`

The `#collapseBtn` is added by `page()` after the panel_title string,
in the standard shell position.

BOOT MODULE — DEFENSIVE BY DESIGN
=================================
`_boot.js` uses dynamic `import()` inside an async IIFE for every
dependency, and wraps each import and each wiring step in its own
try/catch.  Same shape as board/pair/text_board/cone/boxes.

The boot logs `[boot] BOOT COMPLETE` at the end; its absence is the
fastest failure signal.  All logs go to `console.log`, not
`console.error`, so DevTools filters cannot hide a failure.

FOUC IS FIXED AT THE SOURCE
===========================
`pg_base.py` bakes the *final* panel CSS and *final* panel markup into
`PG_CSS` and `PG_PANEL_BODY`.  There is no "before styled" state:
`#rulerNumericInput` is `display:none` from `PG_CSS`, the panel is
dark-themed and 200px wide from first paint, and the canvas is
viewport-sized with a dark background from `PG_CSS`.

`installVerticalMenu` and `installDarkScreenStyles` are no-ops (or,
in `installVerticalMenu`'s case, a translation pass over already-baked
DOM).  No element appears in an unstyled intermediate state.

WIRING CALLBACKS
================
Six injected callbacks are wired before any boot action:

    setDraw(draw)                pg_state's _drawRef
    setAfterChange(fn)           pg_state's _afterChange hook
    setPanelDraw(draw)           pgkit/panel_core's _drawFn
    setRemoteDraw(draw)          pgkit/remote_client's _drawFn
    setRemoteSelect(fn)          pgkit/remote_client's select handler
    setRemoteDeselect(fn)        pgkit/remote_client's deselect handler

DELETED
=======
    cable_live.py      (JS aggregator — every JS file is now its own
                        ES module)
    pg_arrows.py       (JS aggregator for the 12 arrow modules)
    pg_export.py       (JS aggregator for the 14 export modules)
    cable_server.py    (replaced by kit_serve)
"""

import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_PARENT = os.path.dirname(_HERE)
for _p in (_HERE, _PARENT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from pg_base     import PG_CSS, PG_PANEL_BODY
from pg_state    import STATE_JS
from pg_core     import CORE_JS
from pg_i18n     import I18N_JS
from pg_snap     import SNAP_JS
from pg_route    import ROUTE_JS
from pg_view_cables import CABLES_VIEW_JS
from pg_view_floor  import FLOOR_VIEW_JS
from pg_view_wall   import WALL_VIEW_JS
from pg_view_sheet  import SHEET_VIEW_JS
from pg_edit        import CABLE_EDIT_JS
from pg_navigation  import NAVIGATION_JS
from pg_panel       import PANEL_JS
from pg_ruler       import RULER_JS
from pg_dispatch    import DISPATCH_JS

from pg_arrows_tuning   import TUNING_JS
from pg_arrows_geometry import GEO_JS as ARROWS_GEO_JS
from pg_arrows_state    import STATE_JS as ARROWS_STATE_JS
from pg_arrows_passes   import PASSES_JS as ARROWS_PASSES_JS
from pg_arrows_scoring  import SCORING_JS as ARROWS_SCORING_JS
from pg_arrows_layout   import LAYOUT_JS as ARROWS_LAYOUT_JS
from pg_arrows_shapes   import SHAPES_JS as ARROWS_SHAPES_JS
from pg_arrows_reroute  import REROUTE_JS as ARROWS_REROUTE_JS
from pg_arrows_solver   import SOLVER_JS as ARROWS_SOLVER_JS
from pg_arrows_render   import RENDER_JS as ARROWS_RENDER_JS
from pg_arrows_entry    import ENTRY_JS  as ARROWS_ENTRY_JS
from pg_arrows_diag     import DIAG_JS   as ARROWS_DIAG_JS

from pg_export_textures  import TEXTURES_JS  as EXPORT_TEXTURES_JS
from pg_export_linearize import LINEARIZE_JS as EXPORT_LINEARIZE_JS
from pg_export_primitives import PRIMITIVES_JS as EXPORT_PRIMITIVES_JS
from pg_export_geometry  import GEO_JS       as EXPORT_GEO_JS
from pg_export_snapshot  import SNAPSHOT_JS  as EXPORT_SNAPSHOT_JS
from pg_export_penalties import PENALTIES_JS as EXPORT_PENALTIES_JS
from pg_export_style     import STYLE_JS     as EXPORT_STYLE_JS
from pg_export_optimizer import OPTIMIZER_JS as EXPORT_OPTIMIZER_JS
from pg_export_pillpush  import PILLPUSH_JS  as EXPORT_PILLPUSH_JS
from pg_export_labels    import LABELS_JS    as EXPORT_LABELS_JS
from pg_export_render    import RENDER_JS    as EXPORT_RENDER_JS
from pg_export_preview   import PREVIEW_JS   as EXPORT_PREVIEW_JS
from pg_export_rust      import RUST_JS      as EXPORT_RUST_JS
from pg_export_entry     import ENTRY_JS     as EXPORT_ENTRY_JS

from pgkit.html_shell  import page
from pgkit.js_check    import JsSyntaxError
from pgkit.modules     import pgkit_modules


_PANEL_TITLE = (
    '<span data-i18n="appTitleShort">Cable plan</span> '
    '<span id="snapPill">snap: off</span> '
    '<button id="helpBtn" data-i18n-title="tipHelp">?</button>'
)


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
// and no wiring steps run.  The panel's CSS and markup are baked in
// pg_base; nothing is "reshaped" at boot, so there is no flash-of-
// unstyled-content — the served page is the final page.

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
      ["i18n",           "./pg_i18n.js"],
      ["geom",           "./pg_geometry.js"],
      ["state",          "./pg_state.js"],
      ["core",           "./pg_core.js"],
      ["snap",           "./pg_snap.js"],
      ["route",          "./pg_route.js"],
      ["viewCables",     "./pg_view_cables.js"],
      ["viewFloor",      "./pg_view_floor.js"],
      ["viewWall",       "./pg_view_wall.js"],
      ["viewSheet",      "./pg_view_sheet.js"],
      ["navigation",     "./pg_navigation.js"],
      ["edit",           "./pg_edit.js"],
      ["ruler",          "./pg_ruler.js"],
      ["arrowTuning",    "./pg_arrows_tuning.js"],
      ["arrowGeo",       "./pg_arrows_geometry.js"],
      ["arrowState",     "./pg_arrows_state.js"],
      ["arrowPasses",    "./pg_arrows_passes.js"],
      ["arrowScoring",   "./pg_arrows_scoring.js"],
      ["arrowLayout",    "./pg_arrows_layout.js"],
      ["arrowShapes",    "./pg_arrows_shapes.js"],
      ["arrowReroute",   "./pg_arrows_reroute.js"],
      ["arrowSolver",    "./pg_arrows_solver.js"],
      ["arrowRender",    "./pg_arrows_render.js"],
      ["arrowEntry",     "./pg_arrows_entry.js"],
      ["arrowDiag",      "./pg_arrows_diag.js"],
      ["expTex",         "./pg_export_textures.js"],
      ["expLin",         "./pg_export_linearize.js"],
      ["expPrim",        "./pg_export_primitives.js"],
      ["expGeo",         "./pg_export_geometry.js"],
      ["expSnap",        "./pg_export_snapshot.js"],
      ["expPen",         "./pg_export_penalties.js"],
      ["expStyle",       "./pg_export_style.js"],
      ["expOpt",         "./pg_export_optimizer.js"],
      ["expPush",        "./pg_export_pillpush.js"],
      ["expLabels",      "./pg_export_labels.js"],
      ["expRender",      "./pg_export_render.js"],
      ["expPreview",     "./pg_export_preview.js"],
      ["expRust",        "./pg_export_rust.js"],
      ["expEntry",       "./pg_export_entry.js"],
      ["panel",          "./pg_panel.js"],
      ["dispatch",       "./pg_dispatch.js"],
      ["shell",          "./pgkit/shell.js"],
      ["rc",             "./pgkit/remote_client.js"],
      ["pc",             "./pgkit/panel_core.js"],
    ]);

    /* ---- translate the baked panel markup into the active language.
           (installVerticalMenu is now a relabel pass over
           data-i18n attributes; it no longer creates DOM or CSS.) */
    step("installVerticalMenu",        () => m.panel.installVerticalMenu());

    /* ---- wire state's draw hook. */
    step("setDraw",           () => m.state.setDraw(m.dispatch.draw));

    /* ---- wire state's after-change hook. */
    step("setAfterChange",    () => m.state.setAfterChange(() => {
      m.rc.pgkitNotifyAll();
    }));

    /* ---- wire kit's injectable slots. */
    step("setPanelDraw",      () => m.pc.setPanelDraw(m.dispatch.draw));
    step("setRemoteDraw",     () => m.rc.setRemoteDraw(m.dispatch.draw));
    step("setRemoteSelect",   () => m.rc.setRemoteSelect(m.panel.pgkitHandleSelect));
    step("setRemoteDeselect", () => m.rc.setRemoteDeselect(() => {
      m.state.state.selectedCable  = null;
      m.state.state.selectedVertex = null;
    }));

    /* ---- boot — panel installers.  installDarkScreenStyles is a
           no-op (CSS is baked in pg_base); kept in the boot for
           symmetry with the other playgrounds. */
    step("installShellControls",       () => m.shell.installShellControls());
    step("installDarkScreenStyles",    () => m.panel.installDarkScreenStyles());
    step("installInstructionsPopover", () => m.panel.installInstructionsPopover());
    step("installExportButton",        () => m.expEntry.installExportButton());
    step("installArrowDiagButton",     () => m.arrowDiag.installArrowDiagButton());
    step("installArrowToggle",         () => m.arrowDiag.installArrowToggle());

    /* ---- boot — keyboard listeners. */
    step("installKeyboard",            () => m.core.installKeyboard());

    /* ---- boot — panel button bindings. */
    step("installPanelBindings",       () => m.panel.installPanelBindings());

    /* ---- boot — build the junction graph before anything reads it. */
    step("buildJunctionGraph",         () => m.core.buildJunctionGraph());

    /* ---- boot — load from server, then finish. */
    m.core.loadFromServer()
      .then(data => { if (data) m.core.applyLoaded(data); })
      .catch(e => fail("loadFromServer", e))
      .finally(() => {
        step("resize", () => m.navigation.resize());
        step("updateSnapPill", () => m.core.updateSnapPill());
        step("updateDrawButton", () => m.core.updateDrawButton());
        step("panelBootSync", () => m.panel.pgkitBootSync());
        log("BOOT COMPLETE");
      });
  } catch (e) {
    log("BOOT ABORTED:", (e && e.message) || e);
    if (e && e.stack) {
      log("STACK:", e.stack.split("\n").slice(0, 4).join(" | "));
    }
  }
})();
"""


def _build_module_sources(geom):
    geometry_js = (
        "/* Generated from room_walls.json at page-build time. */\n"
        "export const GEOMETRY = "
        + json.dumps(geom, separators=(",", ":"))
        + ";\n"
    )
    return {
        **pgkit_modules(),
        "pg_geometry.js":           geometry_js,
        "pg_i18n.js":               I18N_JS,
        "pg_state.js":              STATE_JS,
        "pg_core.js":               CORE_JS,
        "pg_snap.js":               SNAP_JS,
        "pg_route.js":              ROUTE_JS,
        "pg_view_cables.js":        CABLES_VIEW_JS,
        "pg_view_floor.js":         FLOOR_VIEW_JS,
        "pg_view_wall.js":          WALL_VIEW_JS,
        "pg_view_sheet.js":         SHEET_VIEW_JS,
        "pg_edit.js":               CABLE_EDIT_JS,
        "pg_navigation.js":         NAVIGATION_JS,
        "pg_panel.js":              PANEL_JS,
        "pg_ruler.js":              RULER_JS,
        "pg_dispatch.js":           DISPATCH_JS,
        "pg_arrows_tuning.js":      TUNING_JS,
        "pg_arrows_geometry.js":    ARROWS_GEO_JS,
        "pg_arrows_state.js":       ARROWS_STATE_JS,
        "pg_arrows_passes.js":      ARROWS_PASSES_JS,
        "pg_arrows_scoring.js":     ARROWS_SCORING_JS,
        "pg_arrows_layout.js":      ARROWS_LAYOUT_JS,
        "pg_arrows_shapes.js":      ARROWS_SHAPES_JS,
        "pg_arrows_reroute.js":     ARROWS_REROUTE_JS,
        "pg_arrows_solver.js":      ARROWS_SOLVER_JS,
        "pg_arrows_render.js":      ARROWS_RENDER_JS,
        "pg_arrows_entry.js":       ARROWS_ENTRY_JS,
        "pg_arrows_diag.js":        ARROWS_DIAG_JS,
        "pg_export_textures.js":    EXPORT_TEXTURES_JS,
        "pg_export_linearize.js":   EXPORT_LINEARIZE_JS,
        "pg_export_primitives.js":  EXPORT_PRIMITIVES_JS,
        "pg_export_geometry.js":    EXPORT_GEO_JS,
        "pg_export_snapshot.js":    EXPORT_SNAPSHOT_JS,
        "pg_export_penalties.js":   EXPORT_PENALTIES_JS,
        "pg_export_style.js":       EXPORT_STYLE_JS,
        "pg_export_optimizer.js":   EXPORT_OPTIMIZER_JS,
        "pg_export_pillpush.js":    EXPORT_PILLPUSH_JS,
        "pg_export_labels.js":      EXPORT_LABELS_JS,
        "pg_export_render.js":      EXPORT_RENDER_JS,
        "pg_export_preview.js":     EXPORT_PREVIEW_JS,
        "pg_export_rust.js":        EXPORT_RUST_JS,
        "pg_export_entry.js":       EXPORT_ENTRY_JS,
        "_boot.js":                 _BOOT_JS,
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
            title          = "Cable / pipe layout planner",
            panel_title    = _PANEL_TITLE,
            panel_body     = PG_PANEL_BODY,
            module_sources = _build_module_sources(geom),
            boot_module    = BOOT_MODULE,
            extra_css      = PG_CSS,
            status_text    = "",
        )
    except JsSyntaxError as err:
        _report_and_exit(err)
