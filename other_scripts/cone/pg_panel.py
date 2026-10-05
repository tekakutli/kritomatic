"""pg_panel.py — panel bindings, including the patch list."""

PANEL_JS = r"""
/* ==========================================================================
   SLIDER SYNC
   ========================================================================== */

function syncPanelSliders() {
  const d = document.getElementById("depthSlider");
  const a = document.getElementById("angleSlider");
  const r = document.getElementById("ringsSlider");
  const m = document.getElementById("meridiansSlider");
  if (d) d.value = cone.depth;
  if (a) a.value = Math.round(cone.halfAngle * 180 / Math.PI);
  if (r) r.value = cone.ringCount;
  if (m) m.value = cone.meridianCount;
  updatePanelLabels();
}

function updatePanelLabels() {
  const dv = document.getElementById("depthVal");
  const av = document.getElementById("angleVal");
  const rv = document.getElementById("ringsVal");
  const mv = document.getElementById("meridiansVal");
  if (dv) dv.textContent = cone.depth.toFixed(1);
  if (av) av.textContent =
    Math.round(cone.halfAngle * 180 / Math.PI) + "\u00B0";
  if (rv) rv.textContent = String(cone.ringCount);
  if (mv) mv.textContent = String(cone.meridianCount);
}

/* ==========================================================================
   PATCH LIST
   ==========================================================================
   Rebuild the panel list from the quads array.  Called on add,
   delete, select, and after a vertex drag that changes which patch
   is active.  Rebuilding rather than mutating keeps the row / array
   index correspondence trivially correct — the list is short enough
   that the cost is invisible. */

function syncQuadList() {
  const list = document.getElementById("quadList");
  if (!list) return;
  list.innerHTML = "";

  if (quads.length === 0) {
    const e = document.createElement("div");
    e.className = "empty";
    e.textContent = "no patches yet";
    list.appendChild(e);
    return;
  }

  for (let i = 0; i < quads.length; i++) {
    const q   = quads[i];
    const row = document.createElement("div");
    row.className = "quadRow" + (i === selectedQuad ? " selected" : "");
    row.dataset.idx = String(i);

    const sw = document.createElement("span");
    sw.className = "quadSwatch";
    row.appendChild(sw);

    const lb = document.createElement("span");
    lb.className = "quadLabel";
    lb.textContent = q.name;
    row.appendChild(lb);

    const del = document.createElement("button");
    del.className = "quadDel";
    del.textContent = "\u00D7";
    del.title = "Delete patch";
    del.addEventListener("click", (e) => {
      e.stopPropagation();
      deleteQuad(i);
    });
    row.appendChild(del);

    row.addEventListener("click", () => {
      selectedQuad = i;
      syncQuadList();
      draw();
    });

    list.appendChild(row);
  }
}

/* ==========================================================================
   BINDINGS
   ========================================================================== */

(function installPanel() {
  const d = document.getElementById("depthSlider");
  const a = document.getElementById("angleSlider");
  const r = document.getElementById("ringsSlider");
  const m = document.getElementById("meridiansSlider");
  const resetBtn  = document.getElementById("resetBtn");
  const centerBtn = document.getElementById("centerBtn");
  const addBtn    = document.getElementById("addQuadBtn");

  /* Depth and half-angle both drive R_world, so both re-clamp the
     apex: shrinking the cone with the apex near the base ring would
     otherwise push the apex outside the new disk and invert the
     shear on the affected rings.  The patches, being bound to
     (phi, s), follow the surface automatically — their screen
     positions change but their binding does not. */
  if (d) d.addEventListener("input", () => {
    cone.depth = parseFloat(d.value);
    clampApex();
    updatePanelLabels();
    draw();
  });

  if (a) a.addEventListener("input", () => {
    cone.halfAngle = parseFloat(a.value) * Math.PI / 180;
    clampApex();
    updatePanelLabels();
    draw();
  });

  if (r) r.addEventListener("input", () => {
    cone.ringCount = parseInt(r.value, 10);
    updatePanelLabels();
    draw();
  });

  if (m) m.addEventListener("input", () => {
    cone.meridianCount = parseInt(m.value, 10);
    updatePanelLabels();
    draw();
  });

  if (resetBtn)  resetBtn.addEventListener("click", resetView);
  if (centerBtn) centerBtn.addEventListener("click", () => {
    cone.ax = 0;
    cone.ay = 0;
    draw();
  });

  if (addBtn) addBtn.addEventListener("click", addQuad);
})();
"""
