"""
text_boxes_html.py — assemble text_boxes's HTML page and module
manifest.

Entry point.  `render()` is the only public function; it asks
pgkit's `page()` to build the shell, passing the project's
`_MODULE_SOURCES` dict (one entry per JS file — kit's own modules
plus every text_boxes module) and the name of the boot module.

MODULE MANIFEST
===============
`_MODULE_SOURCES` maps URL paths *under* `/modules/` to JS source
strings:

    "pgkit/shell.js"               served at /modules/pgkit/shell.js
    "tbox_core.js"                   served at /modules/tbox_core.js
    "_boot.js"                     served at /modules/_boot.js

`_boot.js` is one entry among the others; it does all the `import`
wiring and calls the shell, panel, and boot-sync functions in the
right order.  Its imports:

    · "./tbox_panel_scrubs.js"          (side effect: field registrations)
    · installShellControls            from pgkit/shell
    · setRemoteDraw/Select/Deselect   from pgkit/remote_client
    · setPanelDraw                    from pgkit/panel_core
    · installEditorBindings,
      _syncEditor, syncPanelSliders   from tbox_panel_fields
    · installPanelBindings,
      pgkitHandleSelect, pgkitBootSync from tbox_panel_bindings
    · draw                            from tbox_dispatch
    · board, resize, _setDraw         from tbox_core
    · syncItemList                    from tbox_panel_lists

Then it wires the injected callbacks (`_setDraw`, `setPanelDraw`,
`setRemoteDraw`, `setRemoteSelect`, `setRemoteDeselect`) and calls
the boot sequence (`installShellControls`, `installEditorBindings`,
`installPanelBindings`, `pgkitBootSync`, `resize`).

WHY A MANIFEST
==============
The browser loads each module over HTTP; `page()` needs the source
strings to run `node --check` on each one before serving HTML, and
`serve()` needs them to respond to `GET /modules/<name>`.  Putting
both the file names and their contents in one dict means the two
sides of the pipe can never disagree about which file exists.

SYNTAX ERRORS
=============
`page()` runs `pgkit.js_check.check_js` on every module before
assembling HTML.  A syntax error is reported with the source file
and line and exits with status 1; no HTML file is written and no
server starts.  The `JsSyntaxError` catch here mirrors that; without
it, `page()` would raise uncaught from `render()` and the failure
mode would be a Python traceback rather than a clear "JS syntax
error in tbox_core.py, line N".
"""

import sys

from  .tbox_base       import TEXT_BOXES_CSS, TEXT_BOXES_PANEL_BODY
from  .tbox_core       import CORE_JS
from  .tbox_view       import VIEW_JS
from  .tbox_krita      import KRITA_JS
from  .tbox_panel_fields   import TEXT_BOXES_PANEL_FIELDS_JS
from  .tbox_panel_lists    import TEXT_BOXES_PANEL_LISTS_JS
from  .tbox_panel_scrubs   import TEXT_BOXES_PANEL_SCRUBS_JS
from  .tbox_panel_bindings import TEXT_BOXES_PANEL_BINDINGS_JS
from  .tbox_scene      import SCENE_JS
from  .tbox_dispatch   import DISPATCH_JS

from pgkit.html_shell  import page
from pgkit.js_check    import JsSyntaxError
from pgkit.modules     import pgkit_modules


_BOOT_JS = r"""
// Side-effect imports.
import "./tbox_panel_scrubs.js";

// Named imports.
import { installShellControls }             from "./pgkit/shell.js";
import {
  setRemoteDraw, setRemoteSelect, setRemoteDeselect,
} from "./pgkit/remote_client.js";
import { setPanelDraw }                     from "./pgkit/panel_core.js";
import {
  installEditorBindings, _syncEditor, syncPanelSliders,
} from "./tbox_panel_fields.js";
import {
  installPanelBindings, pgkitHandleSelect, pgkitBootSync,
} from "./tbox_panel_bindings.js";
import { draw }                             from "./tbox_dispatch.js";
import { board, resize, _setDraw }          from "./tbox_core.js";
import { syncItemList }                     from "./tbox_panel_lists.js";

// Wire kit's injectable slots.
_setDraw(draw);
setPanelDraw(draw);
setRemoteDraw(draw);
setRemoteSelect(pgkitHandleSelect);
setRemoteDeselect(() => {
  board.selectedIdx = -1;
  syncItemList();
  _syncEditor();
});

// Boot.
installShellControls();
installEditorBindings();
installPanelBindings();
pgkitBootSync();
resize();
"""


_MODULE_SOURCES = {
    **pgkit_modules(),
    "tbox_core.js":             CORE_JS,
    "tbox_view.js":             VIEW_JS,
    "tbox_krita.js":            KRITA_JS,
    "tbox_panel_fields.js":     TEXT_BOXES_PANEL_FIELDS_JS,
    "tbox_panel_lists.js":      TEXT_BOXES_PANEL_LISTS_JS,
    "tbox_panel_scrubs.js":     TEXT_BOXES_PANEL_SCRUBS_JS,
    "tbox_panel_bindings.js":   TEXT_BOXES_PANEL_BINDINGS_JS,
    "tbox_scene.js":            SCENE_JS,
    "tbox_dispatch.js":         DISPATCH_JS,
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
            title          = "Text boxes — every text shape on a canvas",
            panel_title    = "Text boxes",
            panel_body     = TEXT_BOXES_PANEL_BODY,
            module_sources = _MODULE_SOURCES,
            boot_module    = BOOT_MODULE,
            extra_css      = TEXT_BOXES_CSS,
            status_text    = "ready",
        )
    except JsSyntaxError as err:
        _report_and_exit(err)
