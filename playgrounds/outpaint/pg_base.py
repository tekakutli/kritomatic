"""Outpaint playground's panel body and CSS.

Sections: Image (area dims + refresh + krita check), Generated
(the stack of outpaint results), Send (Send to Krita), State
(status line).

AREA DIM INPUTS
===============
`#areaW` and `#areaH` are scrub inputs (see
`pg_panel.py`'s AREA DIM SCRUB INPUTS).  They are `type="text"`
with `class="scrubInput"` so `_installScrubInput` from
`pgkit/panel_core.js` can attach drag-to-scrub behaviour.  Click
to type an exact value, drag sideways to scrub.  Shift-drag snaps
to 8-px multiples.

The old `type="number"` inputs (with `min="1" max="8192"`) are
gone.  `setAreaDims()` in `pg_state.py` already enforces the real
bounds — area must be at least the viewer size and at least
`VIEWER_MIN_SIZE_PX` — and the scrub input's `write` callback
clamps to a sane upper bound (16384) so an over-scrub can't
produce an absurd area.

GENERATED SECTION
=================
A wrapping grid of thumbnails, one per outpaint result received
from the daemon's event bus.  Each thumbnail:

  · Click → preview the result at full size in the canvas.  The
    area resizes to the image's exact dims.  While previewing,
    a "Show source" button appears above the strip so the user can
    return to `@thecanvas`.
  · Delete button (×) → remove that entry from the stack.

The strip is a `grid` with `auto-fill` columns, so it wraps to
new rows as items accumulate.  A `max-height` + `overflow-y: auto`
keeps the section bounded — once the grid fills the visible
height, additional rows scroll vertically instead of pushing the
strip off the bottom of the panel.  (Previously it was a single
horizontal `flex` row: items beyond the visible width were
unreachable because the panel clipped horizontal overflow.)

DELETE BUTTON — SPECIFICITY
===========================
The `.del` button lives inside the panel's `#ui` root, which has
a base `#ui button { ... }` rule (specificity `1,0,1`).  A plain
`.gen-thumb .del` selector is only `0,2,0` and therefore loses —
the button inherits `#ui button`'s `background` / `border` /
`color`, which is why a previous iteration rendered as "just a
black outline and a ×" (the chip fill was being overridden to
transparent).  The selector here is `#ui .gen-thumb .del`
(`1,2,0`), which beats `#ui button` on every property without
needing `!important`.  The background is a **solid** opaque
colour, not `rgba`, so it cannot look see-through regardless of
what thumbnail content sits behind it.

The stack lives in `board.generatedStack` (see `pg_state.js`).
Entries are NOT auto-displayed on arrival — the previous behaviour
(where the first result instantly replaced the viewer) is gone.
The user decides which one to look at.

The "Show source" button is hidden when the source is showing, and
shown when a preview is active.  Clicking it calls
`previewSource()`, which restores the saved source view exactly.

SEND SECTION
============
One button: **Send to Krita**.

  · **Send to Krita** takes the currently previewed image and asks
    the server to create a new Krita document from it (via the
    existing `create_new_with_dimensions` + `comfy_paste_image_b64`
    commands).  The user's original document is untouched.
    Disabled when the source is showing — Send to Krita requires a
    generated image to have been selected.

Generation is triggered by the docker's global **Run** button
(`comfy_panel_run`) — the playground does not own a Run action.
The outpaint result reaches the viewer via the daemon event bus
(`outpaint_result` → `/active_state`'s `last_pasted` →
`pg_autosync.js` → `pushGeneratedToStack`).  See
`outpaint_server.py`'s EVENT BUS SUBSCRIPTION.
"""

PG_CSS = r"""
  #ui button#refreshCanvasBtn {
    border-color: rgba(120, 220, 255, 0.72);
    color: #b8ecff;
  }
  #ui button#refreshCanvasBtn:hover:not(:disabled) {
    border-color: rgba(140, 225, 255, 1.00);
    background: rgba(120, 220, 255, 0.10);
    color: #d8f4ff;
  }
  #ui button#sendToKritaBtn {
    border-color: rgba(255, 220, 120, 0.72);
    color: #ffe4b8;
  }
  #ui button#sendToKritaBtn:hover:not(:disabled) {
    border-color: rgba(255, 230, 140, 1.00);
    background: rgba(255, 220, 120, 0.10);
    color: #fff2d8;
  }
  #ui button#sendToKritaBtn:disabled {
    border-color: rgba(60, 70, 85, 0.6);
    color: #5a6774;
  }
  #ui button#showSourceBtn {
    border-color: rgba(180, 200, 255, 0.72);
    color: #c8d4ff;
    margin-bottom: 4px;
  }
  #ui button#showSourceBtn:hover:not(:disabled) {
    border-color: rgba(200, 215, 255, 1.00);
    background: rgba(180, 200, 255, 0.10);
    color: #dfe6ff;
  }
  #imageInfo {
    font-size:10px; color:#5a6774;
    margin:6px 0;
    overflow:hidden; text-overflow:ellipsis; white-space:nowrap;
  }

  /* Generated stack — wrapping grid, vertically scrollable.
     auto-fill keeps empty trailing cells so a single item doesn't
     stretch to fill the whole row; minmax(56px, 1fr) targets ~4
     columns in a typical sidebar.  max-height + overflow-y keeps
     the section bounded once the grid fills up.  (Was: single
     horizontal flex row that overflowed off-panel.) */
  #genStrip {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(56px, 1fr));
    gap: 6px;
    padding: 4px 0;
    border-top: 1px solid #223040;
    border-bottom: 1px solid #223040;
    margin: 4px 0;
    max-height: 280px;
    overflow-y: auto;
    overflow-x: hidden;
    scrollbar-width: thin;
    scrollbar-color: #34445a #0a0e14;
  }
  #genStrip::-webkit-scrollbar { width: 6px; }
  #genStrip::-webkit-scrollbar-track { background: #0a0e14; }
  #genStrip::-webkit-scrollbar-thumb { background: #34445a; border-radius: 3px; }

  .gen-thumb {
    position: relative;
    width: 100%;
    aspect-ratio: 1 / 1;
    border: 1px solid #34445a;
    border-radius: 2px;
    overflow: hidden;
    cursor: pointer;
    background: #0a0e14;
    transition: border-color 0.12s;
  }
  .gen-thumb:hover { border-color: #5a8099; }
  .gen-thumb.active {
    border-color: #00e5ff;
    box-shadow: 0 0 0 1px #00e5ff;
  }
  .gen-thumb img {
    width: 100%;
    height: 100%;
    object-fit: contain;
    display: block;
    pointer-events: none;
  }

  /* Delete button — opaque chip, always visible.
     Selector is `#ui .gen-thumb .del` (specificity 1,2,0) so it
     beats the panel's base `#ui button { ... }` (1,0,1).  A plain
     `.gen-thumb .del` (0,2,0) would lose, and the button would
     inherit `#ui button`'s transparent background — which is
     exactly why earlier "fix" attempts had no visible effect.
     Background is a solid opaque colour (not rgba) so the chip
     stays readable over dark outpaint outputs.  box-shadow gives
     an additional silhouette edge independent of the border.
     See the module docstring, DELETE BUTTON — SPECIFICITY. */
  #ui .gen-thumb .del {
    position: absolute;
    top: 2px;
    right: 2px;
    width: 18px;
    height: 18px;
    margin: 0;
    padding: 0;
    border: 1px solid rgba(255, 255, 255, 0.55);
    border-radius: 3px;
    background: #14181f;
    color: #ffb0b0;
    font-family: system-ui, -apple-system, "Segoe UI", sans-serif;
    font-size: 14px;
    font-weight: 700;
    line-height: 1;
    cursor: pointer;
    display: flex;
    align-items: center;
    justify-content: center;
    box-shadow: 0 1px 3px rgba(0, 0, 0, 0.75);
    transition: background 0.12s, color 0.12s, border-color 0.12s;
  }
  #ui .gen-thumb .del:hover {
    color: #ff7070;
    background: #000;
    border-color: rgba(255, 140, 140, 0.9);
  }

  #genEmpty {
    font-size: 10px;
    color: #5a6774;
    line-height: 1.5;
    margin: 4px 0;
  }
  #previewInfo {
    font-size: 10px;
    color: #5a6774;
    margin-top: 4px;
    font-variant-numeric: tabular-nums;
  }
  #genHint {
    font-size:10px; color:#5a6774; line-height:1.5;
    margin-top:6px;
    overflow-wrap:anywhere;
  }
  #genHint code { color:#00e5ff; }
  #kritaStatus {
    font-size: 10px;
    color: #5a6774;
    margin-left: 8px;
  }
  #kritaStatus.ok   { color: #00e5ff; }
  #kritaStatus.bad  { color: #d48590; }
  #areaW, #areaH {
    width: 100%;
    background: transparent;
    border: 0;
    border-bottom: 1px solid #223040;
    color: #00e5ff;
    font-family: inherit;
    font-size: 11px;
    font-weight: 700;
    font-variant-numeric: tabular-nums;
    padding: 2px 4px;
    outline: none;
    box-sizing: border-box;
  }
  #areaW:hover, #areaH:hover { border-bottom-color: #34445a; }
  #areaW:focus, #areaH:focus { border-bottom-color: #00e5ff; }
  #statusLine {
    font-size:10px; color:#7b8794;
    margin-top:6px;
    font-variant-numeric: tabular-nums;
    overflow-wrap:anywhere; line-height:1.5;
  }
  #statusLine.ok   { color:#00e5ff; }
  #statusLine.bad  { color:#d48590; }
  #statusLine.warn { color:#ffe600; }
  #stateInfo {
    font-size:10px; color:#5a6774; line-height:1.5;
    margin-bottom:6px;
  }
  #stateInfo code { color:#00e5ff; }
"""

PG_PANEL_BODY = r"""
    <details class="section" open>
      <summary>Image</summary>
      <div class="section-body">
        <div class="field">
          <label>Area width</label>
          <input type="text" id="areaW" class="scrubInput"
                 value="1024" autocomplete="off" spellcheck="false"
                 title="Area width in pixels. Drag sideways to scrub; hold Shift to snap to 8 px. Click to type an exact value.">
        </div>
        <div class="field">
          <label>Area height</label>
          <input type="text" id="areaH" class="scrubInput"
                 value="1024" autocomplete="off" spellcheck="false"
                 title="Area height in pixels. Drag sideways to scrub; hold Shift to snap to 8 px. Click to type an exact value.">
        </div>
        <div class="row" style="margin-top:8px;">
          <button id="refreshCanvasBtn"
                  title="Re-fetch the layer selected at @thecanvas. Use when the source changed in Krita but the autosync poller didn't pick it up — the poller only fires when the @thecanvas spec changes (active ↔ layer X ↔ layer Y), not when the pixels behind a constant spec change.">
            ↻ Refresh @thecanvas
          </button>
        </div>
        <div id="imageInfo">No image loaded</div>
        <div class="row" style="margin-top:4px;">
          <button id="kritaCheckBtn">Check Krita</button>
          <span id="kritaStatus">not checked</span>
        </div>
      </div>
    </details>
    <details class="section" open>
      <summary>Generated (<span id="genCount">0</span>)</summary>
      <div class="section-body">
        <div id="genEmpty">
          No generations yet.  Click the docker's <strong>Run</strong>
          button to produce one.
        </div>
        <div id="genStrip" style="display:none;"></div>
        <div class="row" style="margin-top:2px;">
          <button id="showSourceBtn" style="display:none;"
                  title="Return canvas to showing @thecanvas (source).">
            ↺ Show source
          </button>
        </div>
        <div id="previewInfo"></div>
      </div>
    </details>
    <details class="section" open>
      <summary>Send</summary>
      <div class="section-body">
        <div class="row">
          <button id="sendToKritaBtn" disabled
                  title="Create a brand-new Krita document at the currently-previewed image's exact size and paste it into the new document at (0,0). Your original document is untouched. Disabled when the source is showing — click a thumbnail to preview a generated result first.">
            → Send to Krita
          </button>
        </div>
        <div id="genHint">
          Trigger generation with the docker's <strong>Run</strong>
          button; results appear as thumbnails above.  Click a
          thumbnail to preview it, then <strong>Send to Krita</strong>
          to materialize it as a new document.
        </div>
      </div>
    </details>
    <details class="section" open>
      <summary>State</summary>
      <div class="section-body">
        <div id="stateInfo">
          Served at <code>/state</code>. Version:
          <code id="stateVersion">—</code>
        </div>
        <div id="statusLine">idle</div>
      </div>
    </details>
"""