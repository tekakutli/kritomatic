SCENE_JS = r"""
import {
  pairs, pairState, state, view, notifyChanged, fitView,
} from "./pg_core.js";
import { syncPairList } from "./pg_panel_lists.js";
import { applyKraOptionsToDOM, readKraOptionsFromDOM } from "./pg_kra.js";
import { pgkitNotifyAll } from "./pgkit/remote_client.js";
import { flashStatus } from "./pgkit/panel_core.js";

const SCENE_VERSION = 1;
const SCENE_TYPE    = "pair_scene";
const SCENE_FILE    = "pair_scene.json";

export function buildSceneJSON() {
  return {
    sceneVersion: SCENE_VERSION,
    type: SCENE_TYPE,
    generatedAt: new Date().toISOString(),
    view: {
      scale: view.scale, tx: view.tx, ty: view.ty,
      zoom: view.zoom, panX: view.panX, panY: view.panY,
    },
    segScroll: state.segScroll,
    nextPairId: pairState.nextPairId,
    nextSegId:  pairState.nextSegId,
    pairs: pairs.map(p => ({
      id: p.id, name: p.name,
      cx: p.cx, cy: p.cy, angle: p.angle,
      metricA: p.metricA, metricB: p.metricB,
      offset: p.offset, gap: p.gap,
      loopbackGap: (typeof p.loopbackGap === "number") ? p.loopbackGap : 0,
      scale: (typeof p.scale === "number" && isFinite(p.scale) && p.scale > 0) ? p.scale : 1,
      segments: p.segments.map(s => ({
        id: s.id, name: s.name, a: s.a, b: s.b,
        filled: s.filled !== false,
      })),
    })),
    kraOptions: (typeof readKraOptionsFromDOM === "function")
                  ? readKraOptionsFromDOM() : undefined,
  };
}

export function saveSceneJSON() {
  const text = JSON.stringify(buildSceneJSON(), null, 2);
  const url  = "data:application/json;charset=utf-8," + encodeURIComponent(text);
  const a = document.createElement("a");
  a.href = url;
  a.download = SCENE_FILE;
  a.style.display = "none";
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  flashStatus("Saved " + pairs.length + " pair(s)", "ok");
}

export function applySceneJSON(data) {
  if (!data || typeof data !== "object") throw new Error("scene: not an object");
  if (data.type && data.type !== SCENE_TYPE)
    throw new Error("scene: wrong type '" + data.type + "'");
  if (data.sceneVersion !== SCENE_VERSION)
    throw new Error("scene: unsupported sceneVersion " + data.sceneVersion);

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
          id: (typeof s.id === "number") ? s.id : pairState.nextSegId++,
          name: (typeof s.name === "string" && s.name.length) ? s.name : ("S" + pairState.nextSegId),
          a: s.a, b: s.b,
          filled: s.filled !== false,
        });
      }
    }
    pairs.push({
      id: p.id,
      name: (typeof p.name === "string" && p.name.length) ? p.name : ("P" + p.id),
      cx: (typeof p.cx === "number") ? p.cx : 0,
      cy: (typeof p.cy === "number") ? p.cy : 0,
      angle: (typeof p.angle === "number") ? p.angle : 0,
      metricA: (typeof p.metricA === "number") ? p.metricA : 1,
      metricB: (typeof p.metricB === "number") ? p.metricB : 1,
      offset: (typeof p.offset === "number") ? p.offset : 0,
      gap: (typeof p.gap === "number") ? p.gap : 1,
      loopbackGap: (typeof p.loopbackGap === "number")
                    ? Math.max(0, Math.min(1000, p.loopbackGap)) : 0,
      scale: (typeof p.scale === "number" && isFinite(p.scale) && p.scale > 0)
              ? Math.max(0.05, Math.min(100, p.scale)) : 1,
      segments: segs,
    });
  }

  let maxPairId = 0, maxSegId = 0;
  for (const p of pairs) {
    maxPairId = Math.max(maxPairId, p.id);
    for (const s of p.segments) maxSegId = Math.max(maxSegId, s.id);
  }
  pairState.nextPairId = Math.max(maxPairId + 1,
    (typeof data.nextPairId === "number") ? data.nextPairId : 1);
  pairState.nextSegId = Math.max(maxSegId + 1,
    (typeof data.nextSegId === "number") ? data.nextSegId : 1);

  state.dragPairAnchor = null;
  state.dragSegmentEdge = null;
  state.dragSegmentBody = null;
  state.shiftDragSet = null;
  pairState.selectedPair = -1;
  pairState.selectedSegment = null;

  fitView();
  syncPairList();
  notifyChanged();
  pgkitNotifyAll();
}

export function loadSceneFromText(text) {
  let data;
  try { data = JSON.parse(text); }
  catch (e) { flashStatus("Load failed: not valid JSON", "bad"); return false; }
  try { applySceneJSON(data); }
  catch (e) { flashStatus("Load failed: " + e.message, "bad"); return false; }
  flashStatus("Loaded " + pairs.length + " pair(s)", "ok");
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
    reader.onload = () => { loadSceneFromText(String(reader.result || "")); inp.value = ""; };
    reader.onerror = () => { flashStatus("Load failed: could not read file", "bad"); inp.value = ""; };
    reader.readAsText(file);
  });
  document.body.appendChild(inp);
  _sceneFileInput = inp;
  return inp;
}

export function promptLoadScene() {
  _installSceneFileInput().click();
}
"""
