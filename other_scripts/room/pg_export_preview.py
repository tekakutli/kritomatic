"""
pg_export_preview.py — the preview page.

openExportPreview writes one HTML document into a new tab: a title,
a subtitle, a hint paragraph, a toolbar with four controls, and one
card per exported cable.

The toolbar carries:

    Download all PNGs      one file per cable, staggered by 180 ms
                           so the browser does not treat them as a
                           single multi-file download
    Download all JSON      one cable_runs.json bundle
    Filter collinear       the checkbox that flips
    vertices in strip      window.filterCollinearVerticesInStrip and
                           re-renders every card in place
    Show cable angles      the checkbox that flips
                           window.showCableAngles and re-renders
                           every card in place

The two checkboxes share a `rerenderAll` helper: each handler flips
its global, then asks every card to redraw in place.  Card DOM stays
put; only the PNG src, the meta line, and the JSON link change.  The
two flags compose — the collinear filter runs first inside
computeVertexLabelPlacement and may remove some pills entirely, then
the angle flag decorates the survivors.

Nothing here is in the main playground's scope.  The page is
self-contained — its own CSS, its own script — and the only
interaction between it and the parent is the state.trueCables list
the caller closes over when it calls openExportPreview.
"""


PREVIEW_JS = r"""
/* ---- Preview page ---- */

function openExportPreview(images) {
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
     place.  Card DOM stays put; only the PNG src, the meta line,
     and the JSON link change.  The two flags compose: the collinear
     filter runs first inside computeVertexLabelPlacement and may
     remove some pills entirely, then the angle flag decorates the
     survivors. */
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
