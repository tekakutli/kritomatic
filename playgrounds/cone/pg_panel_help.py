"""
pg_panel_help.py — the help popup.

The help card is not part of the main document.  It lives as a
string constant here and is written into a separate popup window
when the "?" button is clicked.

The window is created with a stable name ("cone_help"), so a second
click reuses the existing popup instead of spawning another one.
Its content is (re)written on every open, which means:

    - the popup always shows the current help card (no stale copy
      lingering if the main page was reloaded with an older script),
      and
    - if the user closed the popup, clicking "?" reopens it.

The popup window is a plain HTML document styled to match the
panel: same dark palette, same monospaced face, same cyan accent.
It carries no scripts, so it cannot affect the main view.

If the browser blocks the popup (some do, by default, for
window.open calls not tied to a user gesture — this one is), the
failure is reported through flashStatus rather than silently
swallowed.

EDITING THIS FILE
=================
The help card's content is HTML inside HELP_HTML.  Sections are
labelled with <div class="section">…</div>; each hint line is a
<div class="hint">…</div>.  Inline markup uses <strong> for
key terms, <em> for the highlighted phrase, and <span class="kbd">
for a key.  The style block at the top of HELP_HTML controls the
look; edits to that propagate to every section.
"""

PANEL_HELP_JS = r"""
/* ==========================================================================
   HELP POPUP SUBWINDOW
   ========================================================================== */

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
  .hint strong {
    color:#dce6f2; font-weight:700;
  }
  .hint em {
    color:#ffc966; font-style:normal;
    letter-spacing:0.04em;
  }

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
  cone \u2014 pull it off &pi; when the original and mirror would otherwise
  visually cross at the apex</div>
<div class="hint">hold <span class="kbd">Shift</span> while dragging
  <strong>Mirror &ang;</strong> to move the mirror <em>and</em> the
  original symmetrically about the midpoint they shared at drag
  start</div>
<div class="hint"><strong>Mirror flip</strong> negates the mirror's rotation
  so a tilted or rotated shape is a faithful copy, not a 180&deg;-rotated
  ghost</div>
<div class="hint"><strong>Plane warp</strong> reparametrises the patch's
  own tangential plane without moving it.  0 = the historic axial
  (rectangular) chart aligned with the patch's U and V axes; 1 =
  the same plane addressed with a radial chart, so the space between
  squares grows with distance from the apex.  Only the cone view
  reads it</div>

<div class="section">Squares</div>
<div class="hint">hold <span class="kbd">Alt</span> while clicking a square to grab
  its <strong>patch</strong> instead</div>
<div class="hint">dragging a <strong>corner</strong> anchors the
  <em>opposite corner</em> \u2014 only the two edges that meet at that
  corner move</div>
<div class="hint">hold <span class="kbd">Shift</span> while dragging a
  <strong>corner</strong> to resize about the shape's own centre
  instead</div>
<div class="hint">hold <span class="kbd">Shift</span> while dragging the
  rotate handle to jump in 15&deg; steps</div>
<div class="hint"><span class="kbd">Shift</span>+<span class="kbd">A</span>
  aligns the selected square to 45&deg;</div>
<div class="hint"><span class="kbd">Delete</span> removes the selected square</div>
<div class="hint"><strong>Square &phi;</strong> / <strong>Square s</strong> set
  the selected shape's centre on the unfolded sheet.  &phi; is in radians
  on [0, 2&pi;); the cone wraps, so typing a value on the far side of the
  seam moves the shape the short way around</div>
<div class="hint"><strong>Slope</strong> hinges the shape out of its patch
  about the near edge.  0 = flat on the patch, 1 = rotated a full
  90&deg; and standing perpendicular to it, tipped into the cone's cavity</div>
<div class="hint">the multiplier to the right of Slope controls how tall a
  hinged shape stands, as a multiple of its flat-view height.  Global,
  not per-shape</div>
<div class="hint"><strong>Visual bot.</strong> on a square makes its
  KRA text anchor and its four corner senses follow the <em>visual
  bottom of the scene</em> \u2014 the side of the square that sits
  lowest on screen \u2014 instead of the shape's own longest edge</div>

<div class="section">Panel fields</div>
<div class="hint">drag a value sideways to <strong>scrub</strong> it;
  hold <span class="kbd">Shift</span> to snap</div>
<div class="hint">fields with <em>&phi;</em>, <em>s</em>, <em>W</em>, <em>H</em>,
  <em>slope</em>, and <em>visual bottom</em> follow the current selection</div>
<div class="hint"><strong>Mirror</strong>, <strong>Mirror &ang;</strong>,
  <strong>Mirror flip</strong>, and <strong>Plane warp</strong> follow
  the selected patch: they grey out when nothing is selected, and
  reflect the patch's own flags otherwise</div>

<div class="section">Draw Quad \u2014 two fit modes</div>
<div class="hint">click four corners in <strong>one band</strong> to fit a patch
  and a rectangle to them</div>
<div class="hint">all four points must be in the same band
  (upper cone, or lower flat)</div>
<div class="hint"><span class="kbd">Esc</span> cancels an in-progress draw</div>
<div class="hint"><strong>shape &rarr; patch</strong> (default): the shape is
  fitted first, then a patch of the <em>same size</em> as the one created
  by <em>+ Patch</em> is centred on the shape's footprint</div>
<div class="hint"><strong>patch &rarr; shape</strong>: the four clicks' centroid
  defines the patch's centre; a patch of the <em>same size</em> as <em>+ Patch</em>
  is created there, and the shape is fitted inside it</div>
<div class="hint"><strong>No rotate</strong>: when checked, the fitted shape's
  rotation is locked to zero \u2014 its edges align with the flat view's
  &phi; and s axes, so it reads as <em>north/south aligned</em> on the
  unfolded sheet.  Applies to both fit modes.</div>

<div class="section">Export</div>
<div class="hint"><strong>Export visual state</strong> writes a JSON dump of
  everything on screen</div>
<div class="hint"><strong>Generate .kra</strong> writes a Krita document
  with one vector-text layer per square</div>
<div class="hint"><strong>Save scene</strong> / <strong>Load scene</strong>
  round-trip the whole workspace as JSON</div>

</body>
</html>`;

let _helpWindow = null;

function openHelpWindow() {
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
    if (typeof flashStatus === "function") {
      flashStatus("Popup blocked \u2014 allow popups to see help", "warn");
    }
    return;
  }

  /* Write the card every time, so a stale help window from a prior
     page load is refreshed rather than reused verbatim. */
  try {
    win.document.open();
    win.document.write(HELP_HTML);
    win.document.close();
  } catch (e) {
    /* Some browsers refuse document.write on a cross-origin or
       already-closed window.  Fall back to a same-origin navigation
       of the popup to a data URL carrying the same content. */
    try {
      win.location.href =
        "data:text/html;charset=utf-8," + encodeURIComponent(HELP_HTML);
    } catch (e2) {
      if (typeof flashStatus === "function") {
        flashStatus("Could not open help window", "bad");
      }
      return;
    }
  }

  /* Bring it to the front if it was already open behind the main
     window. */
  try { win.focus(); } catch (e) {}

  _helpWindow = win;
}
"""
