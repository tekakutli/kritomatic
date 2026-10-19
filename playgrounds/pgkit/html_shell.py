"""
pgkit.html_shell — assemble a playground's HTML page.

One function, `page(...)`, takes the project-specific pieces and
returns the complete HTML.  The kit supplies the CSS, the panel
wrapper, the status line, the restore button, the "controlled
remotely" banner, and the canvas; the project supplies its title,
its panel heading, its field markup, and its JavaScript bundle.

TWO INPUT FORMS
===============
    page(script="<concatenated JS>", ...)
        Legacy single-string form.  The bundle is served as one
        `<script>` block labelled `bundle.js`.

    page(segments=[("label1", js1), ("label2", js2), ...], ...)
        Multi-segment form.  Each segment is served as its own
        `<script>` block with `"use strict";` at the top and
        `//# sourceURL=<label>.js` at the bottom.  All blocks share
        the document's global scope, exactly as one flat block would
        — `var`, `function`, `let`, `const`, and `class` declarations
        are visible to subsequent scripts.  The difference is that
        DevTools attributes a runtime error or an uncaught exception
        to the *filename* of the block it occurred in, not to an
        opaque `text_board.html`.

        Prefer this form whenever the project already knows its
        source files.

SYNTAX CHECK
============
`page()` runs `pgkit.js_check.check_js` on the assembled script
before returning HTML.  Two checks, both cheap:

    1.  Each segment is checked on its own
        (`'"use strict";\n' + segment`).  A local syntax error —
        an unclosed brace, a stray token, the string-literal
        concatenation that motivated the check — is reported with
        the segment's own label and line number.

    2.  The concatenation is checked as a whole.  This catches the
        cross-segment errors per-segment checking cannot see:
        duplicate `const` names, unclosed blocks that happen to
        balance across a file boundary, and anything else that only
        becomes a parse error once the pieces are glued together.

A `JsSyntaxError` raised by either check propagates to the caller
uncaught.  The caller decides whether to attribute, print, or exit
— `text_board_html.render()` does all three.

`check_syntax=False` disables both checks.

The shell does NOT emit a help button.  An earlier revision did, and
it was dead on every project that used `page()` without also wiring
`#helpBtn` to something — pair and text_board both shipped a `?`
button that did nothing.  A project that wants a help button adds
its own to `panel_body` (or to `panel_title` in its own HTML, as
cone does via its own HTML_HEAD).
"""

from .styles import SHELL_CSS
from .js_check import JsSyntaxError, check_js


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

# Closes the document.  No `</script>` here — page() closes each
# script block itself so multi-segment pages do not need a trailing
# close tag.
_TAIL = "\n</body>\n</html>\n"


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


def _normalize_js(js: str) -> str:
    """Ensure `js` ends with exactly one newline."""
    return js if js.endswith("\n") else js + "\n"


def _safe_source_name(label: str) -> str:
    """Sanitize a segment label for use in //# sourceURL=.

    SourceURL is a DevTools filename, not a filesystem path.  Keep the
    characters that give it structure (slashes, dots, dashes,
    underscores) and strip anything else so the directive stays on one
    line and the name is recognizable in the Sources panel.
    """
    name = "".join(c for c in label if c.isalnum() or c in "._-/")
    if not name.endswith(".js"):
        name += ".js"
    return name


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
    """Return the closing tags after the last script block."""
    return _TAIL


def boot_js(sync_fn_name=""):
    """Return the shell-controls IIFE, the optional sync call, and
    the final `resize()`."""
    sync_line = f"{sync_fn_name}();\n" if sync_fn_name else ""
    return _BOOT_JS + "\n" + sync_line + "resize();\n"


def _normalize_segments(script, segments):
    if segments is not None:
        return list(segments)
    if script is not None:
        return [("bundle.js", script)]
    raise ValueError("page() requires either 'script' or 'segments'")


def _full_script_for_check(segments, boot_sync_fn) -> str:
    """Assemble the exact string that node --check parses.

    Mirrors what the browser will see concatenated across the
    `<script>` blocks, minus the block boundaries themselves — which
    does not affect parse validity.
    """
    parts = ['"use strict";\n']
    for _label, js in segments:
        parts.append(_normalize_js(js))
    parts.append(boot_js(boot_sync_fn))
    return "".join(parts)


def page(*, title, panel_title, panel_body, script=None, segments=None,
         extra_css="", status_text="", boot_sync_fn="",
         check_syntax=True):
    """Assemble the full HTML page from its parts.

    See the module docstring for the two input forms and the syntax
    check contract.
    """
    segs = _normalize_segments(script, segments)

    if check_syntax:
        # 1. Per-segment check: precise attribution for local errors.
        for label, js in segs:
            check_js('"use strict";\n' + _normalize_js(js), label=label)

        # 2. Concatenation check: cross-segment errors that a
        #    segment-local parse cannot see (duplicate `const`,
        #    unclosed blocks that happen to balance, etc.).
        check_js(_full_script_for_check(segs, boot_sync_fn),
                 label="bundle")

    body_html = (
        head(title, extra_css)
        + panel_open(panel_title)
        + panel_body
        + panel_status(status_text)
        + panel_close()
    )

    out = [body_html]

    for label, js in segs:
        out.append("<script>\n")
        out.append('"use strict";\n')
        out.append(_normalize_js(js))
        out.append(f"//# sourceURL={_safe_source_name(label)}\n")
        out.append("</script>\n")

    out.append("<script>\n")
    out.append('"use strict";\n')
    out.append(boot_js(boot_sync_fn))
    out.append("//# sourceURL=pgkit_boot.js\n")
    out.append("</script>\n")

    out.append(tail())
    return "".join(out)
