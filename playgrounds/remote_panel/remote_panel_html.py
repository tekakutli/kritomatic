"""
remote_panel_html.py — assemble the remote panel's HTML page.

The remote panel is not a playground.  It has no canvas, no sidebar,
and no pgkit shell.  This module writes its own page: RP_CSS from
rp_base.py, RP_BODY from rp_base.py, and the concatenation of the
two JS modules.

LOG MIRRORING — INLINE, NOT VIA pgkit/panel_core.js
===================================================
The panel's own server (port 8772) is a pgkit server — `make_handler`
installs `POST /__log` on every playground automatically.  But the
panel's HTML is not module-based: `rp_client.js` and `rp_ui.js` are
concatenated into one `<script>` block, and both are designed to
run as classic scripts (globals, not exports).  Making the page
import `pgkit/panel_core.js` as an ES module would require turning
both files into modules, changing their `const _rp = ...` into
`export const _rp = ...` and every call into an import — a
substantial refactor for a management tool that already surfaces
its errors in its own UI.

Instead, the inline script below installs the same console.error /
console.warn overrides and the same window.onerror /
window.onunhandledrejection hooks that `pgkit/panel_core.js`
installs.  The only differences are the prefix derivation (this
page has no `document.title`-based playground name, so the prefix
is fixed to "remote_panel") and the shape of the opt-out handling
(see next paragraph).  The mirror target — `POST ./__log` — is
identical, so the same `_handle_log` on the panel's own server
prints the message to the terminal.

Opt-out parity with pgkit/panel_core.js:

    ?pgkit_no_mirror=1         master kill — silences every mirror
                               path (console.error, console.warn,
                               window.onerror, unhandledrejection).
                               Honoured here.

    ?pgkit_no_mirror_warn=1    narrow kill — silences only the
                               console.warn mirror.  Honoured here
                               so `rpLog("warn", ...)` and any direct
                               `console.warn` from rp_client / rp_ui
                               stay out of the terminal without
                               taking `console.error` down with
                               them.

    ?pgkit_no_mirror_boot=1    boot-log kill.  NOT honoured here:
                               the panel has no boot modules and
                               emits nothing tagged `[boot]`, so
                               there is nothing to silence.

Because the override installs BEFORE rp_client.js and rp_ui.js (it
is a plain `<script>` in the `<head>`, before the concatenated
script at the bottom of `<body>`), every `console.error` and every
`rpLog("error", ...)` that routes through `console.error` from
either file is captured from the very first line.

`rpLog` itself is unchanged.  It records to the panel's own log
array and renders to the panel's own log DOM.  Its "error" kind
additionally calls `console.error`, which the override mirrors.  A
"warn" kind calls `console.warn`, which the override mirrors unless
`?pgkit_no_mirror_warn=1` is set.  Info and other kinds stay
panel-only.

The `<head>` script is guarded against re-installation
(`if (window.__pgkitMirrorInstalled) return;`) so a reload or an
SPA-like re-render cannot double-wrap `console.error`.
"""

import os
import sys

_HERE   = os.path.dirname(os.path.abspath(__file__))
_PARENT = os.path.dirname(_HERE)
for _p in (_HERE, _PARENT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from rp_base   import RP_CSS, RP_BODY
from rp_client import RP_CLIENT_JS
from rp_ui     import RP_UI_JS


# The inline mirror installer.  A classic `<script>` in `<head>` so it
# executes before the concatenated rp_client+rp_ui block at the bottom
# of `<body>`.  Same mechanism as pgkit/panel_core.js's override; the
# only differences are the fixed prefix, the narrower opt-out set
# (see the module docstring, "Opt-out parity"), and the lack of a
# `reportPanelError` export (nothing on this page calls it).
_MIRROR_JS = r"""
(function installPgkitMirror() {
  if (window.__pgkitMirrorInstalled) return;
  window.__pgkitMirrorInstalled = true;

  // Two opt-outs, matching pgkit/panel_core.js as far as applies to
  // this page:
  //
  //   pgkit_no_mirror       → master kill, disables everything.
  //   pgkit_no_mirror_warn  → silences console.warn mirroring only.
  //
  // pgkit_no_mirror_boot is not honoured here: the panel has no boot
  // modules and emits nothing tagged [boot], so there is nothing to
  // silence.
  var disabled = false;
  var warnDisabled = false;
  try {
    var u = new URL(window.location.href);
    disabled = u.searchParams.get("pgkit_no_mirror") === "1";
    warnDisabled = disabled || u.searchParams.get("pgkit_no_mirror_warn") === "1";
  } catch (_) { /* file:// or exotic envs */ }
  if (disabled) return;

  function postLog(message, cls) {
    var text = String(message == null ? "" : message);
    if (!text) return;
    try {
      fetch("./__log", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          message: text,
          class: String(cls || ""),
          prefix: "remote_panel",
        }),
      }).catch(function () {});
    } catch (_) {}
  }

  function argsToString(args) {
    var out = [];
    for (var i = 0; i < args.length; i++) {
      var a = args[i];
      if (a instanceof Error) {
        out.push(a.stack || String(a));
      } else if (typeof a === "object" && a !== null) {
        try { out.push(JSON.stringify(a)); }
        catch (_) { out.push(String(a)); }
      } else {
        out.push(String(a));
      }
    }
    return out.join(" ");
  }

  var origErr = console.error.bind(console);
  var origWarn = console.warn.bind(console);

  console.error = function () {
    origErr.apply(null, arguments);
    try {
      postLog(argsToString(arguments), "console-error");
    } catch (_) {}
  };
  console.warn = function () {
    origWarn.apply(null, arguments);
    if (warnDisabled) return;
    try {
      postLog(argsToString(arguments), "console-warn");
    } catch (_) {}
  };

  window.addEventListener("error", function (ev) {
    var err = ev && ev.error;
    var msg = (err && err.stack) ? err.stack : (ev && ev.message);
    if (msg) postLog(msg, "js-error");
  });
  window.addEventListener("unhandledrejection", function (ev) {
    var r = ev && ev.reason;
    var msg = (r && r.stack) ? r.stack : String(r);
    postLog(msg, "js-rejection");
  });
})();
"""


def render():
    return (
        "<!DOCTYPE html>\n"
        "<html lang=\"en\">\n"
        "<head>\n"
        "<meta charset=\"utf-8\">\n"
        "<title>Remote panel</title>\n"
        "<script>\n"
        + _MIRROR_JS
        + "\n</script>\n"
        "<style>\n"
        + RP_CSS + "\n"
        "</style>\n"
        "</head>\n"
        "<body>\n"
        + RP_BODY + "\n"
        "<script>\n"
        '"use strict";\n'
        + RP_CLIENT_JS + "\n"
        + RP_UI_JS
        + "\n</script>\n"
        "</body>\n"
        "</html>\n"
    )
