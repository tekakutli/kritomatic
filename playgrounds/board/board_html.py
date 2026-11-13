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

    · A module failing to load produces exactly one line
      `[boot] FAIL import <key> → <error>` — followed by
      `[boot] BOOT ABORTED: …`.  Without this, a failed static
      import aborts the whole module graph silently and the page
      loads as a shell with no listeners and a blank canvas.

    · Each wiring step is logged on its own line, so a throw inside
      `installPanelBindings()` (say) doesn't prevent `resize()`
      from running, and the console names the step.

    · Success messages log via `console.log`; FAILURES log via
      `console.error`.  The distinction matters: `pgkit/panel_core.js`
      overrides `console.error` to mirror every call to the server
      terminal via `POST ./__log`, so a boot failure now reaches BOTH
      DevTools AND the terminal process running the playground.
      Success messages stay in DevTools only — no terminal noise.

    · The boot logs `[boot] BOOT COMPLETE` at the end; the absence
      of that line after reload is the fastest way to know the
      boot did not run to completion.

IMPORT ORDER — panel_core FIRST
===============================
`pgkit/panel_core.js` is imported FIRST, before `core`, `fields`,
`lists`, `view`, `scene`, `krita`, `scrubs`, `crops`, `grid`.
That ordering is what installs the console override before any
other module can throw during import.  Without it, a failure in
`core` or `view` would fire before `panel_core` loaded, and the
`console.error` inside `fail()` would go to DevTools only — the
terminal would stay silent.

`panel_core.js` is a leaf module (imports nothing of its own), so
evaluating it first adds no load-order risk to anything else.

If `panel_core` itself fails to import, the failure IS visible via
the browser's native console (no override installed yet), via the
`[boot] BOOT ABORTED` line that follows, and via the absence of
`[boot] BOOT COMPLETE`.  Only the terminal mirror is lost in that
specific case — an unavoidable limitation.

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

import sys

from .pg_base        import PG_CSS, PG_PANEL_BODY
from .pg_core        import CORE_JS
from .pg_view_board  import VIEW_BOARD_JS
from .pg_krita       import KRITA_JS
from .pg_panel_fields   import PG_PANEL_FIELDS_JS
from .pg_panel_lists    import PG_PANEL_LISTS_JS
from .pg_panel_scrubs   import PG_PANEL_SCRUBS_JS
from .pg_panel_bindings import PG_PANEL_BINDINGS_JS
from .pg_scene       import SCENE_JS
from .pg_dispatch    import DISPATCH_JS
from .pg_crops       import CROPS_JS
from .pg_grid        import GRID_JS

from pgkit.html_shell  import page
from pgkit.js_check    import JsSyntaxError
from pgkit.modules     import pgkit_modules


_BOOT_JS = r"""
// _boot.js — defensive dynamic-import boot.
//
// Every dependency is loaded with `await import(path)` inside an
// async IIFE.  Each import and each wiring step logs on its own
// console line.  Success messages use console.log; failures use
// console.error, which pgkit/panel_core.js mirrors to the server
// terminal through its console override.  A module failure produces
// exactly one error line:
//
//     [boot] FAIL import <key> -> <error>
//     [boot] BOOT ABORTED: <error>
//
// and no wiring steps run.  See board_html.py's module docstring for
// the full map of which bug class is caught by which check.
//
// Import order: panel_core FIRST so its console override installs
// before any subsequent import can fail.  If a module below pc
// throws during import, console.error inside fail() reaches the
// server terminal via the override pc installed.

(async () => {
  const log  = (...a) => console.log("[boot]", ...a);
  const fail = (label, e) => {
    console.error("[boot]", "FAIL", label, "\u2192",
                  (e && e.message) || e);
  };

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
      ["pc",       "./pgkit/panel_core.js"],   // FIRST: installs mirror
      ["core",     "./pg_core.js"],
      ["fields",   "./pg_panel_fields.js"],
      ["lists",    "./pg_panel_lists.js"],
      ["view",     "./pg_view_board.js"],
      ["scene",    "./pg_scene.js"],
      ["krita",    "./pg_krita.js"],
      ["scrubs",   "./pg_panel_scrubs.js"],    // side effects only
      ["crops",    "./pg_crops.js"],
      ["grid",     "./pg_grid.js"],
      ["disp",     "./pg_dispatch.js"],
      ["bindings", "./pg_panel_bindings.js"],
      ["shell",    "./pgkit/shell.js"],
      ["rc",       "./pgkit/remote_client.js"],
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
    console.error("[boot]", "BOOT ABORTED:", (e && e.message) || e);
    if (e && e.stack) {
      console.error("[boot]", "STACK:",
                    e.stack.split("\n").slice(0, 4).join(" | "));
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
    "pg_crops.js":            CROPS_JS,
    "pg_grid.js":             GRID_JS,
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