"""
pgkit.html_shell — assemble a playground's HTML page.

One function, `page(...)`, takes the project-specific pieces and
returns the complete HTML.  The kit supplies the CSS, the panel
wrapper, the status line, the restore button, the "controlled
remotely" banner, and the canvas; the project supplies its title,
its panel heading, its field markup, and its JavaScript bundle.

The assembly order matches the current pair_playground and
cone_playground pages, so a project that migrates to the kit
should produce byte-identical output (modulo the new banner and
the shared CSS block).

The `boot_sync_fn` parameter names a JS function — usually the
project's panel-sync function — that the boot script calls once,
after `installShellControls`.  Passing `""` skips it.  Every
project's boot sequence ends with `resize()`, which the kit
includes automatically.
"""

from .styles import SHELL_CSS


_HEAD = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>{title}</title>
<style>
{css}
</style>
</head>
<body>
"""

_PANEL_OPEN = """<div id="ui">
  <h3>{panel_title}
    <button id="helpBtn" title="Help"
      style="margin-left:auto; width:auto; padding:0 6px;
             height:20px; font-size:11px; line-height:1;">?</button>
    <button id="collapseBtn" title="Collapse (H)"
      style="width:auto; padding:0 6px;
             height:20px; font-size:11px; line-height:1;">−</button>
  </h3>

  <div class="panel-body">
"""

_PANEL_STATUS = """    <div id="status">{status_text}</div>

  </div>
</div>
"""

_PANEL_CLOSE = """
<button id="restoreBtn" title="Show panel (H)">&#9776; Show panel</button>
<div id="remoteBanner" title="A remote client is connected">&#9679; Controlled remotely</div>

<canvas id="c"></canvas>
"""

_TAIL = "</script>\n</body>\n</html>\n"


_BOOT_JS = r"""
(function installShellControls() {
  const ui          = document.getElementById("ui");
  const restoreBtn  = document.getElementById("restoreBtn");
  const collapseBtn = document.getElementById("collapseBtn");

  const collapse = () => {
    ui.classList.add("collapsed");
    restoreBtn.classList.add("visible");
  };
  const restore = () => {
    ui.classList.remove("collapsed");
    restoreBtn.classList.remove("visible");
  };

  collapseBtn.addEventListener("click", collapse);
  restoreBtn.addEventListener("click", restore);

  window.addEventListener("keydown", (e) => {
    const t = e.target;
    if (t && (t.tagName === "INPUT" || t.tagName === "TEXTAREA")) return;
    if (e.key === "h" || e.key === "H") {
      if (ui.classList.contains("collapsed")) restore();
      else                                     collapse();
    }
  });
})();
"""


def head(title, extra_css=""):
    """Return the HTML head, the body opening, and the panel markup
    through the opening of `.panel-body`."""
    return _HEAD.format(title=title, css=SHELL_CSS + extra_css)


def panel_open(panel_title):
    """Return the panel's `<h3>` and the opening of `.panel-body`."""
    return _PANEL_OPEN.format(panel_title=panel_title)


def panel_status(status_text):
    """Return the status line and the closing tags of the panel."""
    return _PANEL_STATUS.format(status_text=status_text)


def panel_close():
    """Return the restore button, the remote banner, and the canvas."""
    return _PANEL_CLOSE


def tail():
    """Return the closing tags after the script."""
    return _TAIL


def boot_js(sync_fn_name=""):
    """Return the shell-controls IIFE, the optional sync call, and
    the final `resize()`."""
    sync_line = f"{sync_fn_name}();\n" if sync_fn_name else ""
    return _BOOT_JS + "\n" + sync_line + "resize();\n"


def page(*, title, panel_title, panel_body, script,
         extra_css="", status_text="", boot_sync_fn=""):
    """Assemble the full HTML page from its parts."""
    return (
        head(title, extra_css)
        + panel_open(panel_title)
        + panel_body
        + panel_status(status_text)
        + panel_close()
        + "<script>\n"
        + '"use strict";\n'
        + script
        + boot_js(boot_sync_fn)
        + tail()
    )
