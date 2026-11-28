"""
pgkit.html_shell — assemble a playground's HTML page.

The page is the shell (head, CSS, panel wrapper, canvas) plus exactly
one `<script type="module" src="modules/<boot_module>">`.  The boot
module's imports load every other module.

`page()` runs `check_js_many` on every module in `module_sources`
before returning HTML.  Modules are checked in parallel; a syntax
error aborts page assembly with a `JsSyntaxError` naming the file
and line.  The caller catches it and reports; `*_html.py`'s caller
in turn exits with status 1 so no HTML file is written and no
server starts.

BOOT SCRIPT URL IS RELATIVE
===========================
`src="modules/<boot_module>"` is a *relative* URL.  It resolves
against the page's own URL:

    standalone   http://host:87NN/board_playground.html
                 →  http://host:87NN/modules/_boot.js

    under hub    http://host:8780/board/board_playground.html
                 →  http://host:8780/board/modules/_boot.js

The boot module's *own* `import "./pgkit/shell.js"` resolves
against the boot module's URL, so it lands on the right prefix
without any `<base>` tag: the browser already has the URL.  This
is why the kit does not emit `<base href="...">` — it would only
re-duplicate information the page URL already carries, and would
break the "same HTML file, two deployment shapes" property.

The only place in the shell that used an absolute `/modules/` path
was this script tag.  No other template needed a prefix.
"""

from .js_check import JsSyntaxError, check_js, check_js_many
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
<script type="module" src="modules/{boot_module}"></script>
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

    Modules are checked in parallel via `check_js_many` — see
    `pgkit/js_check.py`'s PARALLELISM section.  On a cold cache this
    is 2–4x faster than the sequential loop it replaces; on a warm
    cache the difference is negligible but harmless.

    Raises `JsSyntaxError` on a syntax error in any module.  `*_html.py`
    catches it, reports the offending file and line, and exits.
    """
    if boot_module not in module_sources:
        raise ValueError(
            f"page(): boot_module {boot_module!r} is not present "
            f"in module_sources (keys: {sorted(module_sources)})"
        )

    if check_syntax:
        check_js_many(module_sources)

    return (
        head(title, extra_css)
        + panel_open(panel_title)
        + panel_body
        + panel_status(status_text)
        + panel_close()
        + tail(boot_module)
    )