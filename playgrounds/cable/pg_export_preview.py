"""
pg_export_preview.py — the printable export preview page.

ES module.  One export: `openExportPreview(images)` — opens a new
browser tab with one card per cable, an inline download button per
card, and a handful of toolbar controls:

    · download all PNGs (serial, ~180 ms apart so the browser's
      per-tab download limit doesn't swallow them)
    · download all metadata as one JSON bundle
    · a "filter collinear vertices" toggle that flips
      `window.filterCollinearVerticesInStrip` and re-renders every
      card in place
    · a "show angles" toggle that flips `window.showCableAngles` and
      does the same

Everything the page draws comes from the `images` array passed in by
`pg_export_entry.exportAllCableRuns`: each entry is
`{ id, canvas, meta }` — already rendered, already scaled, already
cropped.  The preview page never touches the export render path a
second time except for the re-render triggered by the two toggles.

The page is written via `document.write` on a fresh `window.open("")`.
That means it inherits no CSP and no same-origin asset base; every
font and stylesheet inlined, every canvas is read out as a
`toDataURL("image/png")` data URL.  Nothing else would survive a
`file://` fallback on a browser that refuses `document.write`.
"""

PREVIEW_JS = r"""
import {
  state,
} from "./pg_state.js";
import { T } from "./pg_i18n.js";
import {
  FONT_FACE_CSS,
  FONT_SANS,
  FONT_MONO,
} from "./pg_state.js";
import {
  renderCableRunToCanvas,
} from "./pg_export_render.js";
import { flashStatus } from "./pgkit/panel_core.js";

/* ==========================================================================
   OPEN
   ========================================================================== */

export function openExportPreview(images) {
  const w = window.open("", "_blank");
  if (!w) {
    flashStatus(T("popupBlocked"), "bad");
    return;
  }
  let html = `<!DOCTYPE html>
<html><head><meta charset="utf-8">
<title>${T("previewTitle")}</title>
<style>
  ${FONT_FACE_CSS}
  body { font-family: ${FONT_SANS};
         margin: 0; padding: 24px; background: #f1f5f9; color: #000000; }
  h1 { font-size: 22px; margin: 0 0 4px; }
  p.sub { margin: 0 0 16px; color: #333333; font-size: 13px; }
  .toolbar { margin: 0 0 24px; display: flex; gap: 10px;
             align-items: center; flex-wrap: wrap; }
  .toolbar button {
    padding: 8px 16px; cursor: pointer; border: 0;
    background: #000000; color: #ffffff; border-radius: 5px;
    font-size: 13px; font-weight: 600;
  }
  .toolbar button:hover { background: #333333; }
  .toolbar button.secondary { background: #555555; }
  .toolbar button.secondary:hover { background: #333333; }
  .toolbar button:disabled { background: #999999; cursor: default; }
  .toolbar label.toggle {
    display: inline-flex; align-items: center; gap: 6px;
    font-size: 13px; font-weight: 600; color: #000000;
    cursor: pointer; user-select: none;
    padding: 6px 10px; border: 1px solid #000000;
    border-radius: 5px; background: #ffffff;
  }
  .toolbar label.toggle:hover { background: #f0f0f0; }
  .toolbar .hint { color: #333333; font-size: 12px; }
  .printHint { background: #f0f0f0; border-left: 3px solid #000000;
               padding: 10px 14px; margin: 0 0 24px;
               font-size: 13px; color: #000000; border-radius: 0 4px 4px 0; }
  .card { background: #ffffff; border-radius: 8px; padding: 18px;
          margin-bottom: 24px; box-shadow: 0 1px 3px rgba(0,0,0,0.08); }
  .card h2 { font-size: 15px; margin: 0 0 4px;
             font-family: ${FONT_MONO}; color: #000000; }
  .card .meta { font-size: 12px; color: #333333; margin: 0 0 12px;
                font-family: ${FONT_MONO}; }
  .card img { max-width: 100%; height: auto; display: block;
              border: 1px solid #cccccc; border-radius: 4px; }
  .actions { margin-top: 12px; display: flex; gap: 8px; flex-wrap: wrap; }
  .actions a { display: inline-block; padding: 6px 14px;
               background: #000000; color: #ffffff; text-decoration: none;
               border-radius: 4px; font-size: 12px; font-weight: 600;
               cursor: pointer; }
  .actions a:hover { background: #333333; }
  .actions a.json { background: #555555; }
  .actions a.json:hover { background: #333333; }
</style></head><body>
<h1>${T("previewTitle")}</h1>
<p class="sub">${T("previewSubtitle")(images.length)}</p>
<div class="printHint">
  ${T("previewHint")}
</div>
<div class="toolbar">
  <button id="downloadAllBtn">${T("downloadAllPngs")(images.length)}</button>
  <button id="downloadAllJsonBtn" class="secondary">${T("downloadAllJson")(images.length)}</button>
  <label class="toggle" title="${T("filterCollinearTitle")}">
    <input type="checkbox" id="filterCollinear">
    ${T("filterCollinear")}
  </label>
  <label class="toggle" title="${T("showAnglesTitle")}">
    <input type="checkbox" id="showAngles">
    ${T("showAngles")}
  </label>
  <span class="hint" id="dlHint"></span>
</div>`;

  for (const img of images) {
    let url;
    try { url = img.canvas.toDataURL("image/png"); }
    catch (e) { continue; }
    const wpx = img.canvas.width;
    const hpx = img.canvas.height;
    const mm  = Math.round(wpx * 25.4 / 300);

    const jsonText = JSON.stringify(img.meta, null, 2);
    const jsonUrl  = "data:application/json;charset=utf-8,"
                   + encodeURIComponent(jsonText);

    html += `
<div class="card" data-cable-id="${img.id}">
  <h2>${T("cableTitle")} ${img.id}</h2>
  <p class="meta">${T("cardSizeMeta")(wpx, hpx, mm)}</p>
  <img src="${url}" alt="${T("cardAlt")(img.id)}">
  <div class="actions">
    <a href="${url}"     download="cable_${img.id}.png">${T("downloadPng")}</a>
    <a href="${jsonUrl}" class="json" download="cable_${img.id}.json">${T("downloadJson")}</a>
  </div>
</div>`;
  }
  html += `</body></html>`;

  w.document.open();
  w.document.write(html);
  w.document.close();

  const doc = w.document;
  const allBtn     = doc.getElementById("downloadAllBtn");
  const allJsonBtn = doc.getElementById("downloadAllJsonBtn");
  const hint       = doc.getElementById("dlHint");
  const filterCb   = doc.getElementById("filterCollinear");
  const anglesCb   = doc.getElementById("showAngles");

  if (allBtn) {
    allBtn.addEventListener("click", () => {
      allBtn.disabled = true;
      const delay = 180;
      for (let i = 0; i < images.length; i++) {
        const img = images[i];
        let url;
        try { url = img.canvas.toDataURL("image/png"); }
        catch (e) { continue; }
        w.setTimeout(() => {
          const a = doc.createElement("a");
          a.href = url;
          a.download = "cable_" + img.id + ".png";
          a.style.display = "none";
          doc.body.appendChild(a);
          a.click();
          doc.body.removeChild(a);
          if (i === images.length - 1) {
            hint.textContent = T("doneHint");
            allBtn.disabled = false;
          }
        }, i * delay);
      }
    });
  }

  if (allJsonBtn) {
    allJsonBtn.addEventListener("click", () => {
      const bundle = {
        schemaVersion: 6,
        generatedAt:   new Date().toISOString(),
        cables:        images.map(img => img.meta),
      };
      const text = JSON.stringify(bundle, null, 2);
      const url  = "data:application/json;charset=utf-8,"
                 + encodeURIComponent(text);
      const a = doc.createElement("a");
      a.href = url;
      a.download = "cable_runs.json";
      a.style.display = "none";
      doc.body.appendChild(a);
      a.click();
      doc.body.removeChild(a);
      hint.textContent = T("downloadedBundle");
    });
  }

  /* Shared re-render.  Both toolbar checkboxes flip a global that
     renderCableRunToCanvas reads, then ask every card to redraw in
     place. */
  const rerenderAll = async () => {
    for (const tc of state.trueCables) {
      let result = null;
      try {
        result = await renderCableRunToCanvas(tc.id);
      } catch (err) {
        console.error("re-render failed for cable " + tc.id, err);
        continue;
      }
      if (!result || !result.canvas) continue;

      const card = doc.querySelector(`[data-cable-id="${tc.id}"]`);
      if (!card) continue;

      const url = result.canvas.toDataURL("image/png");
      const imgEl = card.querySelector("img");
      if (imgEl) imgEl.src = url;

      const metaEl = card.querySelector(".meta");
      if (metaEl) {
        const wpx = result.canvas.width;
        const hpx = result.canvas.height;
        const mm  = Math.round(wpx * 25.4 / 300);
        metaEl.textContent = T("cardSizeMeta")(wpx, hpx, mm);
      }

      const links = card.querySelectorAll("a");
      if (links[0]) links[0].href = url;
      if (links[1]) {
        const jsonUrl = "data:application/json;charset=utf-8,"
                      + encodeURIComponent(
                          JSON.stringify(result.meta, null, 2));
        links[1].href = jsonUrl;
      }

      const stored = images.find(im => im.id === tc.id);
      if (stored) {
        stored.canvas = result.canvas;
        stored.meta   = result.meta;
      }
    }
  };

  if (filterCb) {
    filterCb.checked = !!window.filterCollinearVerticesInStrip;
    filterCb.addEventListener("change", async () => {
      window.filterCollinearVerticesInStrip = filterCb.checked;
      await rerenderAll();
    });
  }

  if (anglesCb) {
    anglesCb.checked = !!window.showCableAngles;
    anglesCb.addEventListener("change", async () => {
      window.showCableAngles = anglesCb.checked;
      await rerenderAll();
    });
  }
}
"""
