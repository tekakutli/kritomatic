"""
pg_scene.py — save / load the whole workspace to JSON.

A pair and its segments are a single abstract object; the scene file
stores them together, so reloading a pair reproduces both its
geometry and its 1D layout exactly.  The pair's id counters are
saved so newly-added pairs after a load do not collide with the
existing ones.

REMOTE NOTIFICATION
===================
`applySceneJSON` replaces the entire model.  Every value that any
registered field reads changes as a result.  It ends with
`pgkitNotifyAll()`, so remote clients receive a single patch with
the new state on the next animation frame.  `buildSceneJSON` and
`saveSceneJSON` are read-only from the model's perspective and do
not notify.

SCENE CONTENTS
==============
    view         — scale, pan, zoom
    segScroll    — segment view's vertical scroll offset
    nextPairId, nextSegId
    pairs        — every pair with its geometry and its segments
    kraOptions   — .kra export meta-options

LOOPBACK GAP, PER-PAIR SCALE, etc. all round-trip through this file
with the same defaulting and clamping rules as before.
"""

SCENE_JS = r"""
const SCENE_VERSION = 1;
const SCENE_TYPE    = "pair_scene";
const SCENE_FILE    = "pair_scene.json";

function buildSceneJSON() {
  return {
    sceneVersion: SCENE_VERSION,
    type:         SCENE_TYPE,
    generatedAt:  new Date().toISOString(),

    view: {
      scale: view.scale,
      tx:    view.tx,
      ty:    view.ty,
      zoom:  view.zoom,
      panX:  view.panX,
      panY:  view.panY,
    },

    segScroll: state.segScroll,

    nextPairId: nextPairId,
    nextSegId:  nextSegId,

    pairs: pairs.map(p => ({
      id:      p.id,
      name:    p.name,
      cx:      p.cx,
      cy:      p.cy,
      angle:   p.angle,
      metricA: p.metricA,
      metricB: p.metricB,
      offset:  p.offset,
      gap:     p.gap,
      loopbackGap: (typeof p.loopbackGap === "number")
                     ? p.loopbackGap : 0,
      scale: (typeof p.scale === "number" && isFinite(p.scale)
                && p.scale > 0)
               ? p.scale : 1,
      segments: p.segments.map(s => ({
        id:     s.id,
        name:   s.name,
        a:      s.a,
        b:      s.b,
        filled: s.filled !== false,
      })),
    })),

    kraOptions: (typeof readKraOptionsFromDOM === "function")
                  ? readKraOptionsFromDOM()
                  : undefined,
  };
}

function saveSceneJSON() {
  const text = JSON.stringify(buildSceneJSON(), null, 2);
  const url  = "data:application/json;charset=utf-8,"
             + encodeURIComponent(text);
  const a = document.createElement("a");
  a.href = url;
  a.download = SCENE_FILE;
  a.style.display = "none";
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  if (typeof flashStatus === "function") {
    flashStatus("Saved " + pairs.length + " pair(s)", "ok");
  }
}

function applySceneJSON(data) {
  if (!data || typeof data !== "object") {
    throw new Error("scene: not an object");
  }
  if (data.type && data.type !== SCENE_TYPE) {
    throw new Error("scene: wrong type '" + data.type
                    + "' (expected '" + SCENE_TYPE + "')");
  }
  if (data.sceneVersion !== SCENE_VERSION) {
    throw new Error("scene: unsupported sceneVersion "
                    + data.sceneVersion);
  }

  if (data.view && typeof data.view === "object") {
    const v = data.view;
    if (typeof v.zoom === "number") view.zoom = v.zoom;
    if (typeof v.panX === "number") view.panX = v.panX;
    if (typeof v.panY === "number") view.panY = v.panY;
  }

  if (typeof data.segScroll === "number") {
    state.segScroll = Math.max(0, data.segScroll);
  }

  if (data.kraOptions && typeof applyKraOptionsToDOM === "function") {
    applyKraOptionsToDOM(data.kraOptions);
  }

  const src = Array.isArray(data.pairs) ? data.pairs : [];

  pairs.length = 0;
  for (const p of src) {
    if (typeof p.id !== "number") continue;
    const segs = [];
    if (Array.isArray(p.segments)) {
      for (const s of p.segments) {
        if (typeof s.a !== "number" || typeof s.b !== "number") continue;
        segs.push({
          id:   (typeof s.id === "number") ? s.id : nextSegId++,
          name: (typeof s.name === "string" && s.name.length)
                  ? s.name : ("S" + nextSegId),
          a:    s.a,
          b:    s.b,
          filled: s.filled !== false,
        });
      }
    }
    pairs.push({
      id:   p.id,
      name: (typeof p.name === "string" && p.name.length)
              ? p.name : ("P" + p.id),
      cx:   (typeof p.cx === "number") ? p.cx : 0,
      cy:   (typeof p.cy === "number") ? p.cy : 0,
      angle:   (typeof p.angle === "number") ? p.angle : 0,
      metricA: (typeof p.metricA === "number") ? p.metricA : 1,
      metricB: (typeof p.metricB === "number") ? p.metricB : 1,
      offset:  (typeof p.offset === "number") ? p.offset : 0,
      gap:     (typeof p.gap === "number") ? p.gap : 1,
      loopbackGap: (typeof p.loopbackGap === "number")
                     ? Math.max(0, Math.min(1000, p.loopbackGap)) : 0,
      scale: (typeof p.scale === "number" && isFinite(p.scale)
                && p.scale > 0)
               ? Math.max(0.05, Math.min(100, p.scale)) : 1,
      segments: segs,
    });
  }

  let maxPairId = 0, maxSegId = 0;
  for (const p of pairs) {
    maxPairId = Math.max(maxPairId, p.id);
    for (const s of p.segments) maxSegId = Math.max(maxSegId, s.id);
  }
  nextPairId = Math.max(maxPairId + 1,
                        (typeof data.nextPairId === "number")
                          ? data.nextPairId : 1);
  nextSegId  = Math.max(maxSegId + 1,
                        (typeof data.nextSegId === "number")
                          ? data.nextSegId : 1);

  state.dragPairAnchor  = null;
  state.dragSegmentEdge = null;
  state.dragSegmentBody = null;
  state.shiftDragSet    = null;
  selectedPair    = -1;
  selectedSegment = null;

  fitView();
  syncPairList();
  draw();
  pgkitNotifyAll();
}

function loadSceneFromText(text) {
  let data;
  try {
    data = JSON.parse(text);
  } catch (e) {
    if (typeof flashStatus === "function") {
      flashStatus("Load failed: not valid JSON", "bad");
    }
    return false;
  }
  try {
    applySceneJSON(data);
  } catch (e) {
    if (typeof flashStatus === "function") {
      flashStatus("Load failed: " + e.message, "bad");
    }
    return false;
  }
  if (typeof flashStatus === "function") {
    flashStatus("Loaded " + pairs.length + " pair(s)", "ok");
  }
  return true;
}

let _sceneFileInput = null;

function _installSceneFileInput() {
  if (_sceneFileInput) return _sceneFileInput;
  const inp = document.createElement("input");
  inp.type = "file";
  inp.accept = "application/json,.json";
  inp.style.display = "none";
  inp.addEventListener("change", (e) => {
    const file = e.target.files && e.target.files[0];
    if (!file) return;
    const reader = new FileReader();
    reader.onload = () => {
      loadSceneFromText(String(reader.result || ""));
      inp.value = "";
    };
    reader.onerror = () => {
      if (typeof flashStatus === "function") {
        flashStatus("Load failed: could not read file", "bad");
      }
      inp.value = "";
    };
    reader.readAsText(file);
  });
  document.body.appendChild(inp);
  _sceneFileInput = inp;
  return inp;
}

function promptLoadScene() {
  _installSceneFileInput().click();
}
"""
