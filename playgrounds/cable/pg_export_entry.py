"""
pg_export_entry.py — the export button's event handler.

ES module.  Two exports:

    installExportButton    attach the click handler to the baked
                           `#exportBtn` element (the button is in
                           `pg_base.PG_PANEL_BODY`; the installer no
                           longer creates it)

    exportAllCableRuns     the button's click handler: renders every
                           true cable (one `await` per cable, because
                           `renderCableRunToCanvas` awaits the Rust
                           leader optimiser), collects failures,
                           opens the preview page, and flashes a
                           status.

The status message routes through `T` for both the success and the
partial-failure cases.

ASYNC
=====
`exportAllCableRuns` is async — it awaits `renderCableRunToCanvas`
for each cable.  The button's click handler is `async () =>`, which
threads the await through.
"""

ENTRY_JS = r"""
import {
  state,
} from "./pg_state.js";
import { T } from "./pg_i18n.js";
import {
  renderCableRunToCanvas,
} from "./pg_export_render.js";
import {
  openExportPreview,
} from "./pg_export_preview.js";
import {
  flashStatus,
} from "./pgkit/panel_core.js";

/* ==========================================================================
   BUTTON WIRING
   ==========================================================================
   `#exportBtn` is baked in pg_base.PG_PANEL_BODY.  This function
   attaches the click handler; it does not create the button. */

export function installExportButton() {
  const btn = document.getElementById("exportBtn");
  if (!btn) return;
  btn.addEventListener("click", exportAllCableRuns);
}

/* ==========================================================================
   THE CLICK HANDLER
   ========================================================================== */

export async function exportAllCableRuns() {
  if (!state.trueCables.length) {
    flashStatus(T("noCablesToExport"), "warn");
    return;
  }
  const images   = [];
  const failures = [];
  for (const tc of state.trueCables) {
    try {
      const result = await renderCableRunToCanvas(tc.id);
      if (result && result.canvas) {
        images.push({ id: tc.id, canvas: result.canvas, meta: result.meta });
      } else {
        failures.push(tc.id);
      }
    } catch (err) {
      failures.push(tc.id);
      console.error("renderCableRunToCanvas failed for cable " + tc.id, err);
    }
  }
  if (!images.length) {
    flashStatus(T("nothingToExport")(failures.length), "bad");
    return;
  }
  openExportPreview(images);
  const msg = failures.length
    ? T("exportedWithFailures")(images.length, failures.length)
    : T("exportedOk")(images.length);
  flashStatus(msg, failures.length ? "warn" : "ok");
}
"""
