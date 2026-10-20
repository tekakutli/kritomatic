"""
pg_export_entry.py — the export button and the entry point.

Two things, both tiny:

    installExportButton    an IIFE that runs at parse time and wires
                           the "Export cable runs" button into the
                           panel, next to the other OUTPUT-section
                           buttons (the arrow-diag button from
                           pg_arrows_diag is injected into the same
                           slot)

    exportAllCableRuns     the button's click handler: renders every
                           true cable (now one async await per cable,
                           because renderCableRunToCanvas awaits the
                           Rust leader optimiser over the server),
                           collects the failures, opens the preview
                           page, and flashes a status

The status message routes through T() for both the success and the
partial-failure cases.
"""


ENTRY_JS = r"""
/* ---- Button + entry point ---- */

(function installExportButton() {
  const hr = document.querySelector("#ui hr");
  if (!hr) return;
  const row = document.createElement("div");
  row.className = "row";
  const btn = document.createElement("button");
  btn.id = "exportBtn";
  btn.textContent = T("exportButton");
  btn.title       = T("exportButtonTitle");
  row.appendChild(btn);
  hr.parentNode.insertBefore(row, hr);
  btn.addEventListener("click", exportAllCableRuns);
})();

async function exportAllCableRuns() {
  if (!state.trueCables.length) {
    flashStatus(T("noCablesToExport"), "warn");
    return;
  }
  const images = [];
  const failures = [];
  for (const tc of state.trueCables) {
    try {
      /* Async: the leader-geometry optimiser inside this call is a
         round-trip to the Rust subroutine on the Python server (or
         the JS fallback when the binary is absent).  See
         pg_export_rust.py. */
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
