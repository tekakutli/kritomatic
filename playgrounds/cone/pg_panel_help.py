"""
pg_panel_help.py — the help popup.

ES module.  `openHelpWindow` opens a named popup (reused across
clicks) and writes the help card into it.  Uses `flashStatus` from
the kit for popup-blocked errors.
"""

PANEL_HELP_JS = r"""
import { flashStatus } from "./pgkit/panel_core.js";

const HELP_HTML = `<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Cone interior \u2014 help</title>
<style>
  html, body {
    margin:0; padding:0;
    background:#0b1018; color:#a8b5c4;
    font-family: 'JetBrains Mono', 'Fira Code', 'Consolas',
                 'SF Mono', monospace;
    font-size:12px;
  }
  body { padding:20px 24px 28px; }

  h1 {
    display:flex; align-items:center;
    margin:0 0 18px; padding:0 0 12px;
    border-bottom:1px solid #1e2836;
    font-size:11px; font-weight:700;
    letter-spacing:0.16em; text-transform:uppercase;
    color:#dce6f2;
  }

  .section {
    margin:18px 0 6px;
    font-size:10px; font-weight:700;
    letter-spacing:0.14em; text-transform:uppercase;
    color:#00e5ff;
  }
  .section:first-of-type { margin-top:0; }

  .hint {
    padding:5px 0 5px 12px;
    color:#8a97a6;
    letter-spacing:0.03em;
    line-height:1.6;
    border-left:2px solid #1a2433;
    margin:2px 0;
  }
  .hint strong { color:#dce6f2; font-weight:700; }
  .hint em { color:#ffc966; font-style:normal; letter-spacing:0.04em; }

  .kbd {
    display:inline-block;
    padding:1px 6px;
    border:1px solid #223040;
    border-radius:2px;
    background:#0e1622;
    color:#00e5ff;
    font-family:inherit;
    font-weight:700;
    letter-spacing:0.02em;
    font-size:11px;
    margin:0 1px;
  }
</style>
</head>
<body>
<h1>Cone interior \u2014 help</h1>

<div class="section">View</div>
<div class="hint">drag the <strong>apex</strong> in the upper band to tilt the cone</div>
<div class="hint">scroll in the upper band to change <strong>depth</strong></div>
<div class="hint"><span class="kbd">H</span> toggles the side panel</div>

<div class="section">Patches</div>
<div class="hint"><strong>shift</strong>+click a patch to drop a square on it</div>
<div class="hint">drag a patch in either band to move it</div>
<div class="hint">hold <span class="kbd">Shift</span> while dragging a patch or a
  square to slide along the <em>centre&rarr;apex</em> axis only</div>
<div class="hint">tick <strong>Mirror</strong> on a patch to draw a dimmed
  clone of every square on it on the <em>opposite side of the cone</em></div>
<div class="hint"><strong>Mirror &ang;</strong> slides the mirror around the
  cone</div>
<div class="hint">hold <span class="kbd">Shift</span> while dragging
  <strong>Mirror &ang;</strong> to move the mirror <em>and</em> the
  original symmetrically</div>
<div class="hint"><strong>Mirror flip</strong> negates the mirror's rotation
  so a tilted shape is a faithful copy</div>
<div class="hint"><strong>Plane warp</strong> reparametrises the patch's
  own tangential plane without moving it.  0 = axial chart,
  1 = radially-stretched chart.  Only the cone view reads it</div>

<div class="section">Squares</div>
<div class="hint">hold <span class="kbd">Alt</span> while clicking a square to grab
  its <strong>patch</strong> instead</div>
<div class="hint">dragging a <strong>corner</strong> anchors the
  <em>opposite corner</em></div>
<div class="hint">hold <span class="kbd">Shift</span> while dragging a
  <strong>corner</strong> to resize about the shape's centre</div>
<div class="hint">hold <span class="kbd">Shift</span> while dragging the
  rotate handle to jump in 15&deg; steps</div>
<div class="hint"><span class="kbd">Shift</span>+<span class="kbd">A</span>
  aligns the selected square to 45&deg;</div>
<div class="hint"><span class="kbd">Delete</span> removes the selected square</div>
<div class="hint"><strong>Square &phi;</strong> / <strong>Square s</strong> set
  the selected shape's centre on the unfolded sheet</div>
<div class="hint"><strong>Slope</strong> hinges the shape out of its patch</div>
<div class="hint"><strong>Visual bot.</strong> makes the KRA text anchor
  follow the visual bottom of the scene</div>

<div class="section">Draw Quad</div>
<div class="hint">click four corners in <strong>one band</strong> to fit a patch
  and a rectangle to them</div>
<div class="hint"><span class="kbd">Esc</span> cancels an in-progress draw</div>

<div class="section">Export</div>
<div class="hint"><strong>Export visual state</strong> writes a JSON dump of
  everything on screen</div>
<div class="hint"><strong>Generate .kra</strong> writes a Krita document</div>
<div class="hint"><strong>Save scene</strong> / <strong>Load scene</strong>
  round-trip the whole workspace as JSON</div>

</body>
</html>`;

let _helpWindow = null;

export function openHelpWindow() {
  let win = null;
  try {
    win = window.open(
      "",
      "cone_help",
      "width=560,height=680," +
      "menubar=no,toolbar=no,location=no,status=no," +
      "resizable=yes,scrollbars=yes"
    );
  } catch (e) {
    win = null;
  }

  if (!win) {
    flashStatus("Popup blocked \u2014 allow popups to see help", "warn");
    return;
  }

  try {
    win.document.open();
    win.document.write(HELP_HTML);
    win.document.close();
  } catch (e) {
    try {
      win.location.href =
        "data:text/html;charset=utf-8," + encodeURIComponent(HELP_HTML);
    } catch (e2) {
      flashStatus("Could not open help window", "bad");
      return;
    }
  }

  try { win.focus(); } catch (e) {}

  _helpWindow = win;
}
"""
