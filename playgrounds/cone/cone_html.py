"""
cone_html.py — assemble the cone's HTML page and module manifest.

Entry point.  `render()` is the only public function; it asks pgkit's
`page()` to build the shell, passing the project's `_MODULE_SOURCES`
dict (one entry per JS file) and the name of the boot module.

BOOT MODULE — DEFENSIVE BY DESIGN
=================================
`_boot.js` uses dynamic `import()` inside an async IIFE for every
dependency, and wraps each import and each wiring step in its own
try/catch.  Same shape as board/pair/text_board.  Consequences:

    · A module failing to load produces exactly one console line
      `[boot] FAIL import <key> → <error>` before the boot aborts.

    · Each wiring step is logged on its own line.

    · Everything logs with `console.log` so DevTools filters cannot
      hide a failure.

    · The boot logs `[boot] BOOT COMPLETE` on success; its absence
      is the fastest failure signal.

MODULE INVENTORY (19 files)
===========================
    pgkit/*                     kit-supplied (3)
    pg_state.js                 root state / hooks          (new)
    pg_flat_math.js             flat-view parameter math    (new)
    pg_view_squares_core.js     shape model / geometry
    pg_view_squares_warp.js     warp chart
    pg_view_squares_proj.js     cone-band projection
    pg_view_squares_edit.js     drag solvers
    pg_view_squares_flat.js     flat projection / labels
    pg_view_cone.js             cone-band draw (bg cache)
    pg_view_flat.js             flat-band draw (chrome cache)
    pg_dispatch.js              draw + events
    pg_core.js                  draw tool fit + resize
    pg_panel_fields.js          field sync
    pg_panel_lists.js           the two lists
    pg_panel_help.js            help popup
    pg_panel_scrubs.js          scrub inputs + field reg.
    pg_panel_bindings.js        buttons + actions
    pg_scene.js                 save / load
    pg_export.js                visual-state export
    pg_kra.js                   .kra export glue
    _boot.js                    defensive boot

DELETED
=======
    pg_view_squares.py    (assembler — was the concatenation of the 5
                           squares files)
    pg_panel_core.py      (nothing left — flashStatus and
                           _installScrubInput came from the kit,
                           _phiToDisplay moved to pg_flat_math, rates
                           to pg_panel_scrubs, syncPanelSliders to
                           pg_state)
    pg_panel.py           (assembler — was concatenation of the 6
                           panel files)
    cone_server.py        (replaced by kit_serve)
"""

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_PARENT = os.path.dirname(_HERE)
for _p in (_HERE, _PARENT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from pg_base import CONE_CSS, CONE_PANEL_BODY

from pg_state              import STATE_JS
from pg_flat_math          import FLAT_MATH_JS
from pg_view_squares_core  import SQUARES_CORE_JS
from pg_view_squares_warp  import SQUARES_WARP_JS
from pg_view_squares_proj  import SQUARES_PROJ_JS
from pg_view_squares_edit  import SQUARES_EDIT_JS
from pg_view_squares_flat  import SQUARES_FLAT_JS
from pg_view_cone          import CONE_VIEW_JS
from pg_view_flat          import FLAT_VIEW_JS
from pg_dispatch           import DISPATCH_JS
from pg_core               import CORE_JS
from pg_panel_fields       import PANEL_FIELDS_JS
from pg_panel_lists        import PANEL_LISTS_JS
from pg_panel_help         import PANEL_HELP_JS
from pg_panel_scrubs       import PANEL_SCRUBS_JS
from pg_panel_bindings     import PANEL_BINDINGS_JS
from pg_scene              import SCENE_JS
from pg_export             import EXPORT_JS
from pg_kra                import KRA_JS

from pgkit.html_shell  import page
from pgkit.js_check    import JsSyntaxError
from pgkit.modules     import pgkit_modules


_PANEL_TITLE = (
    'Cone interior '
    '<button id="helpBtn" title="Help (opens a separate window)" '
    'style="margin-left:auto; width:auto; padding:0 6px; '
    'height:20px; font-size:11px; line-height:1;">?</button>'
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
// and no wiring steps run.

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
      ["state",    "./pg_state.js"],
      ["flat",     "./pg_flat_math.js"],
      ["sqCore",   "./pg_view_squares_core.js"],
      ["sqWarp",   "./pg_view_squares_warp.js"],
      ["sqProj",   "./pg_view_squares_proj.js"],
      ["sqEdit",   "./pg_view_squares_edit.js"],
      ["sqFlat",   "./pg_view_squares_flat.js"],
      ["vc",       "./pg_view_cone.js"],
      ["vf",       "./pg_view_flat.js"],
      ["fields",   "./pg_panel_fields.js"],
      ["lists",    "./pg_panel_lists.js"],
      ["help",     "./pg_panel_help.js"],
      ["scrubs",   "./pg_panel_scrubs.js"],   // side effects only
      ["core",     "./pg_core.js"],
      ["scene",    "./pg_scene.js"],
      ["exp",      "./pg_export.js"],
      ["kra",      "./pg_kra.js"],
      ["bindings", "./pg_panel_bindings.js"],
      ["disp",     "./pg_dispatch.js"],
      ["shell",    "./pgkit/shell.js"],
      ["rc",       "./pgkit/remote_client.js"],
      ["pc",       "./pgkit/panel_core.js"],
    ]);

    // Wire state's draw hook.
    step("setDraw",           () => m.state.setDraw(m.disp.draw));

    // Wire state's afterChange hook: sync the panel + draw + notifyAll.
    step("setAfterChange",    () => m.state.setAfterChange(() => {
      m.lists.syncQuadList();
      m.disp.draw();
      m.rc.pgkitNotifyAll();
    }));

    // Wire kit's injectable slots.
    step("setPanelDraw",      () => m.pc.setPanelDraw(m.disp.draw));
    step("setRemoteDraw",     () => m.rc.setRemoteDraw(m.disp.draw));
    step("setRemoteSelect",   () => m.rc.setRemoteSelect(m.bindings.pgkitHandleSelect));
    step("setRemoteDeselect", () => m.rc.setRemoteDeselect(() => {
      m.state.coneState.selectedQuad = -1;
      m.state.coneState.selectedSquare = -1;
      m.lists.syncQuadList();
    }));

    // Boot.
    step("installShellControls",  () => m.shell.installShellControls());
    step("installPanelBindings",  () => m.bindings.installPanelBindings());
    step("coneBootSync",          () => m.bindings.coneBootSync());
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


_MODULE_SOURCES = {
    **pgkit_modules(),
    "pg_state.js":              STATE_JS,
    "pg_flat_math.js":          FLAT_MATH_JS,
    "pg_view_squares_core.js":  SQUARES_CORE_JS,
    "pg_view_squares_warp.js":  SQUARES_WARP_JS,
    "pg_view_squares_proj.js":  SQUARES_PROJ_JS,
    "pg_view_squares_edit.js":  SQUARES_EDIT_JS,
    "pg_view_squares_flat.js":  SQUARES_FLAT_JS,
    "pg_view_cone.js":          CONE_VIEW_JS,
    "pg_view_flat.js":          FLAT_VIEW_JS,
    "pg_dispatch.js":           DISPATCH_JS,
    "pg_core.js":               CORE_JS,
    "pg_panel_fields.js":       PANEL_FIELDS_JS,
    "pg_panel_lists.js":        PANEL_LISTS_JS,
    "pg_panel_help.js":         PANEL_HELP_JS,
    "pg_panel_scrubs.js":       PANEL_SCRUBS_JS,
    "pg_panel_bindings.js":     PANEL_BINDINGS_JS,
    "pg_scene.js":              SCENE_JS,
    "pg_export.js":             EXPORT_JS,
    "pg_kra.js":                KRA_JS,
    "_boot.js":                 _BOOT_JS,
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
            title          = "Cone interior — viewer",
            panel_title    = _PANEL_TITLE,
            panel_body     = CONE_PANEL_BODY,
            module_sources = _MODULE_SOURCES,
            boot_module    = BOOT_MODULE,
            extra_css      = CONE_CSS,
            status_text    = "drag apex to tilt · scroll to change depth",
        )
    except JsSyntaxError as err:
        _report_and_exit(err)
