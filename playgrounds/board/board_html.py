"""
board_html.py — assemble the board's HTML page and module manifest.

Entry point.  `render()` is the only public function; it asks
pgkit's `page()` to build the shell, passing the project's
`_MODULE_SOURCES` dict (one entry per JS file — kit's own modules
plus every board module) and the name of the boot module.

BOOT MODULE — DEFENSIVE BY DESIGN
=================================
`_boot.js` uses dynamic `import()` inside an async IIFE for every
dependency, and wraps each import and each wiring step in its own
try/catch.  Consequences:

    · A module failing to load produces exactly one console line —
      `[boot] FAIL import <key> → <error>` — followed by
      `[boot] BOOT ABORTED: …`.  Without this, a failed static
      import aborts the whole module graph silently and the page
      loads as a shell with no listeners and a blank canvas.

    · Each wiring step is logged on its own line, so a throw inside
      `installPanelBindings()` (say) doesn't prevent `resize()`
      from running, and the console names the step.

    · Everything logs with `console.log`, not `console.error`, so
      no DevTools filter can hide the failure.

    · The boot logs `[boot] BOOT COMPLETE` at the end; the absence
      of that line after reload is the fastest way to know the
      boot did not run to completion.

BOOT WIRING
===========
Five injected callbacks are wired before any boot action:

    _setDraw(draw)                  pg_core's internal _drawRef
    setPanelDraw(draw)              pgkit/panel_core's _drawFn
    setRemoteDraw(draw)             pgkit/remote_client's _drawFn
    setRemoteSelect(pgkitHandleSelect)
    setRemoteDeselect(() => …)      clears board.selectedIdx,
                                    syncs the file list

Board has no `setAfterChange` hook (unlike pair).  Every mutator
calls `syncFileList()`, `draw()`, `pgkitNotifyAll()` explicitly in
its own body; there is no single choke point.

STATIC CHECKS — WHERE EACH BUG CLASS IS CAUGHT
==============================================
    parse errors               node --check in page()  (pre-serve)
    missing exports in imports pgkit.lint              (manual)
    unbound identifiers        pgkit.lint              (manual)
    module load failure        [boot] FAIL import ...  (runtime)
    wiring-step throw          [boot] FAIL <step> ...  (runtime)
    rare-path bugs             pgkit.lint              (manual)

Two failures from this bug class were present in this playground
before the refactor:

    pg_dispatch.js imported `itemHitTest` from pg_core, which
    exports `fileHitTest`, not `itemHitTest`.

    pg_panel_scrubs.js imported `selectedFile` and
    `_installScrubInput_unused` from pg_core, neither of which
    exists there (`selectedFile` is in pg_panel_fields).

`pgkit.lint` is the manual pass that catches this class before
serving.  It runs an import/export consistency check (pure Python)
plus, if ESLint is on PATH, a `no-undef` pass over every module.

SYNTAX ERRORS
=============
`page()` runs `pgkit.js_check.check_js` on every module before
assembling HTML.  A syntax error is reported with the source file
and line, and exits with status 1; no HTML file is written and no
server starts.
"""

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_PARENT = os.path.dirname(_HERE)
for _p in (_HERE, _PARENT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from pg_base        import PG_CSS, PG_PANEL_BODY
from pg_core        import CORE_JS
from pg_view_board  import VIEW_BOARD_JS
from pg_krita       import KRITA_JS
from pg_panel_fields   import PG_PANEL_FIELDS_JS
from pg_panel_lists    import PG_PANEL_LISTS_JS
from pg_panel_scrubs   import PG_PANEL_SCRUBS_JS
from pg_panel_bindings import PG_PANEL_BINDINGS_JS
from pg_scene       import SCENE_JS
from pg_dispatch    import DISPATCH_JS

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
// and no wiring steps run.  See board_html.py's module docstring for
// the full map of which bug class is caught by which check.
//
// Import order: leaf modules first (pg_core, pg_panel_fields,
// pg_panel_lists, pg_view_board, pg_scene), then pg_krita (which
// cycles with pg_dispatch via pg_dispatch → pg_krita → pg_dispatch;
// ES modules resolve this because both sides are `export function`,
// which is hoisted), then pg_panel_scrubs (side effects), pg_dispatch,
// pg_panel_bindings, and finally the three pgkit modules.

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
      ["core",     "./pg_core.js"],
      ["fields",   "./pg_panel_fields.js"],
      ["lists",    "./pg_panel_lists.js"],
      ["view",     "./pg_view_board.js"],
      ["scene",    "./pg_scene.js"],
      ["krita",    "./pg_krita.js"],
      ["scrubs",   "./pg_panel_scrubs.js"],    // side effects only
      ["disp",     "./pg_dispatch.js"],
      ["bindings", "./pg_panel_bindings.js"],
      ["shell",    "./pgkit/shell.js"],
      ["rc",       "./pgkit/remote_client.js"],
      ["pc",       "./pgkit/panel_core.js"],
    ]);

    // Wire kit's injectable slots.
    step("_setDraw",
         () => m.core._setDraw(m.disp.draw));
    step("setPanelDraw",
         () => m.pc.setPanelDraw(m.disp.draw));
    step("setRemoteDraw",
         () => m.rc.setRemoteDraw(m.disp.draw));
    step("setRemoteSelect",
         () => m.rc.setRemoteSelect(m.bindings.pgkitHandleSelect));
    step("setRemoteDeselect",
         () => m.rc.setRemoteDeselect(() => {
           m.core.board.selectedIdx = -1;
           m.lists.syncFileList();
         }));

    // Boot.
    step("installShellControls",
         () => m.shell.installShellControls());
    step("installPanelBindings",
         () => m.bindings.installPanelBindings());
    step("boardBootSync",
         () => m.bindings.boardBootSync());
    step("resize",
         () => m.core.resize());

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
    "pg_core.js":             CORE_JS,
    "pg_view_board.js":       VIEW_BOARD_JS,
    "pg_krita.js":            KRITA_JS,
    "pg_panel_fields.js":     PG_PANEL_FIELDS_JS,
    "pg_panel_lists.js":      PG_PANEL_LISTS_JS,
    "pg_panel_scrubs.js":     PG_PANEL_SCRUBS_JS,
    "pg_panel_bindings.js":   PG_PANEL_BINDINGS_JS,
    "pg_scene.js":            SCENE_JS,
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
            title          = "Board — files on a canvas",
            panel_title    = "Board",
            panel_body     = PG_PANEL_BODY,
            module_sources = _MODULE_SOURCES,
            boot_module    = BOOT_MODULE,
            extra_css      = PG_CSS,
            status_text    = "ready",
        )
    except JsSyntaxError as err:
        _report_and_exit(err)
