"""
pgkit.html_shell — assemble a playground's HTML page.

The page is the shell (head, CSS, panel wrapper, canvas) plus exactly
one `<script type="module" src="/modules/<boot_module>">`.  The boot
module's imports load every other module.

`page()` runs `js_check.check_js` on every module in
`module_sources`, one at a time, before returning HTML.  A syntax
error aborts page assembly with a `JsSyntaxError` naming the file
and line.  The caller catches it and reports; `*_html.py`'s caller
in turn exits with status 1 so no HTML file is written and no
server starts.
"""

from .js_check import JsSyntaxError, check_js
from .styles   import SHELL_CSS


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
    <button id="collapseBtn" title="Collapse (H)"
      style="margin-left:auto; width:auto; padding:0 6px;
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

_BOOT_SCRIPT = """
<script type="module" src="/modules/{boot_module}"></script>
</body>
</html>
"""


def head(title, extra_css=""):
    return _HEAD.format(title=title, css=SHELL_CSS + extra_css)


def panel_open(panel_title):
    return _PANEL_OPEN.format(panel_title=panel_title)


def panel_status(status_text):
    return _PANEL_STATUS.format(status_text=status_text)


def panel_close():
    return _PANEL_CLOSE


def tail(boot_module):
    return _BOOT_SCRIPT.format(boot_module=boot_module)


def page(*, title, panel_title, panel_body, module_sources,
         boot_module, extra_css="", status_text="", check_syntax=True):
    """Assemble the full HTML page from its parts.

    Runs each module through `node --check` before assembling the
    page.  `boot_module` must be one of the keys in `module_sources`;
    the boot module's imports load the rest.

    Raises `JsSyntaxError` on a syntax error in any module.  `*_html.py`
    catches it, reports the offending file and line, and exits.
    """
    if boot_module not in module_sources:
        raise ValueError(
            f"page(): boot_module {boot_module!r} is not present "
            f"in module_sources (keys: {sorted(module_sources)})"
        )

    if check_syntax:
        for name, source in module_sources.items():
            check_js(source, label=name)

    return (
        head(title, extra_css)
        + panel_open(panel_title)
        + panel_body
        + panel_status(status_text)
        + panel_close()
        + tail(boot_module)
    )
