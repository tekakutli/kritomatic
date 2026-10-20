"""
pg_scene.py — save and load the whole scene to JSON.

ES module.  `applySceneJSON` replaces the contents of `quads` and
`floatSquares` (in place — the arrays are const and other modules
hold references) and calls `notifyChanged()`, so the boot's
afterChange hook repaints the panel and canvas in one pass.

`_clearTransientState` clears the drag slots in `state` (from
`pg_state`) and resets the selection.

Dead `drawing` / `drawingPreview` locals removed (stage 1 cleanup).

APEX IS SAVED / LOADED AS INTENT
================================
The cone's `ax` / `ay` in the scene file are the user's intended
apex (`cone.axRaw` / `cone.ayRaw`), not the clamped display.
Saving the display would bake the constraint of the depth the scene
was saved at into the file, so a scene written at a shallow depth
and reloaded at a deep one would silently keep the shrunken apex.
Saving intent means the scene behaves the same way at any depth;
`clampApex` is called after loading to derive the initial display.

Field name in the JSON is unchanged (`cone.ax` / `cone.ay`) for
backwards compatibility with existing scene files; only the
semantics of what is stored changed. Old scenes still load — their
previously-clamped values simply become the intent, which is
correct for the depth they were saved at and now behaves sensibly
at any other depth.
"""

SCENE_JS = r"""
import {
  quads, floatSquares, coneState, SHAPE_STATE, state,
  view, cone, quadIdxById, clampApex, clampCloneV, syncPanelSliders,
  notifyChanged,
} from "./pg_state.js";
import { SHAPE_DEFAULT_SCALE } from "./pg_view_squares_core.js";
import { readKraOptionsFromDOM, applyKraOptionsToDOM } from "./pg_kra.js";
import { flashStatus } from "./pgkit/panel_core.js";

const SCENE_VERSION = 1;
const SCENE_TYPE    = "cone_scene";
const SCENE_FILE    = "cone_scene.json";

export function buildSceneJSON() {
  return {
    sceneVersion: SCENE_VERSION,
    type:         SCENE_TYPE,
    generatedAt:  new Date().toISOString(),

    cone: {
      // Save the INTENT (axRaw/ayRaw), not the clamped display. The
      // displayed apex depends on depth and may already be clamped;
      // saving it would bake the constraint into the scene file, so
      // reloading at a different depth would lose the user's original
      // offset. Saving raw means the same scene file behaves the same
      // way at any depth it is loaded at.
      ax:            cone.axRaw,
      ay:            cone.ayRaw,
      depth:         cone.depth,
      halfAngle:     cone.halfAngle,
      ringCount:     cone.ringCount,
      meridianCount: cone.meridianCount,
    },

    shapeDepthCone:    SHAPE_STATE.depthCone,
    shapeStandingMult: SHAPE_STATE.standingMult,

    nextQuadId:   coneState.nextQuadId,
    nextSquareId: coneState.nextSquareId,

    patches: quads.map(q => ({
      id:   q.id,
      name: q.name,
      phi0: q.phi0,
      phi1: q.phi1,
      s0:   q.s0,
      s1:   q.s1,
      mirror: !!q.mirror,
      mirrorAngle: (typeof q.mirrorAngle === "number")
                     ? q.mirrorAngle : Math.PI,
      mirrorFlip: q.mirrorFlip !== false,
      warp: (typeof q.warp === "number") ? q.warp : 0,
    })),

    squares: floatSquares.map(sq => ({
      id:     sq.id,
      name:   sq.name,
      quadId: sq.quadId,
      u:      sq.u,
      v:      sq.v,
      scaleU: sq.scaleU,
      scaleV: sq.scaleV,
      theta:  sq.theta || 0,
      slope:  sq.slope || 0,
      visualBottom: !!sq.visualBottom,
    })),

    kraOptions: (typeof readKraOptionsFromDOM === "function")
                  ? readKraOptionsFromDOM() : undefined,
  };
}

export function saveSceneJSON() {
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
  flashStatus("Saved " + quads.length + " patch(es) and "
              + floatSquares.length + " shape(s)", "ok");
}

function _clearTransientState() {
  state.dragApex       = null;
  state.dragQuadVertex = null;
  state.dragSquare     = null;
  state.flatDrag       = null;
  state.flatSquareDrag = null;
  state.dragPatchBody  = null;

  if (state.mouse) {
    state.mouse.sx     = 0;
    state.mouse.sy     = 0;
    state.mouse.inside = false;
  }

  coneState.selectedQuad   = -1;
  coneState.selectedSquare = -1;
}

export function applySceneJSON(data) {
  if (!data || typeof data !== "object") {
    throw new Error("scene: not an object");
  }
  if (data.type && data.type !== SCENE_TYPE) {
    throw new Error("scene: wrong type '" + data.type
                    + "' (expected '" + SCENE_TYPE + "')");
  }
  if (data.sceneVersion !== SCENE_VERSION) {
    throw new Error("scene: unsupported sceneVersion "
                    + data.sceneVersion
                    + " (expected " + SCENE_VERSION + ")");
  }

  if (data.cone && typeof data.cone === "object") {
    const c = data.cone;
    // Depth and half-angle first: the clamped display depends on them,
    // so clampApex must run *after* they are set.
    if (typeof c.depth         === "number") cone.depth         = c.depth;
    if (typeof c.halfAngle     === "number") cone.halfAngle     = c.halfAngle;
    if (typeof c.ringCount     === "number") cone.ringCount     = c.ringCount;
    if (typeof c.meridianCount === "number") cone.meridianCount = c.meridianCount;
    // Save files carry the intent in ax/ay (see buildSceneJSON).
    if (typeof c.ax === "number") cone.axRaw = c.ax;
    if (typeof c.ay === "number") cone.ayRaw = c.ay;
    clampApex();
  }

  if (typeof data.shapeDepthCone === "number") {
    SHAPE_STATE.depthCone = data.shapeDepthCone;
  } else if (typeof data.shapeDepth === "number") {
    SHAPE_STATE.depthCone = data.shapeDepth;
  }

  if (typeof data.shapeStandingMult === "number") {
    SHAPE_STATE.standingMult =
      Math.max(0.05, Math.min(10, data.shapeStandingMult));
  }

  if (data.kraOptions && typeof applyKraOptionsToDOM === "function") {
    applyKraOptionsToDOM(data.kraOptions);
  }

  const patches = Array.isArray(data.patches) ? data.patches : [];
  const squares = Array.isArray(data.squares) ? data.squares : [];

  quads.length = 0;
  for (const p of patches) {
    if (typeof p.id !== "number") continue;
    quads.push({
      id:   p.id,
      name: (typeof p.name === "string" && p.name.length)
              ? p.name : ("Q" + p.id),
      phi0: (typeof p.phi0 === "number") ? p.phi0 : 0,
      phi1: (typeof p.phi1 === "number") ? p.phi1 : Math.PI / 2,
      s0:   (typeof p.s0   === "number") ? p.s0   : 0.10,
      s1:   (typeof p.s1   === "number") ? p.s1   : 0.50,
      mirror: !!p.mirror,
      mirrorAngle: (typeof p.mirrorAngle === "number")
                     ? p.mirrorAngle : Math.PI,
      mirrorFlip: p.mirrorFlip !== false,
      warp: (typeof p.warp === "number")
              ? Math.max(0, Math.min(1, p.warp)) : 0,
    });
  }

  floatSquares.length = 0;
  for (const s of squares) {
    if (typeof s.id     !== "number") continue;
    if (typeof s.quadId !== "number") continue;

    let quadExists = false;
    for (const q of quads) {
      if (q.id === s.quadId) { quadExists = true; break; }
    }
    if (!quadExists) continue;

    let slopeVal = (typeof s.slope === "number") ? s.slope : 0;
    if (slopeVal < 0 || slopeVal > 1) slopeVal = 0;

    floatSquares.push({
      id:     s.id,
      name:   (typeof s.name === "string" && s.name.length)
                ? s.name : ("S" + s.id),
      quadId: s.quadId,
      u:      (typeof s.u === "number") ? s.u : 0,
      v:      (typeof s.v === "number") ? s.v : 0,
      scaleU: (typeof s.scaleU === "number")
                ? s.scaleU : SHAPE_DEFAULT_SCALE,
      scaleV: (typeof s.scaleV === "number")
                ? s.scaleV : SHAPE_DEFAULT_SCALE,
      theta:  (typeof s.theta === "number") ? s.theta : 0,
      slope:  slopeVal,
      visualBottom: !!s.visualBottom,
    });
  }

  let maxQuadId   = 0;
  let maxSquareId = 0;
  for (const q of quads)        maxQuadId   = Math.max(maxQuadId,   q.id);
  for (const s of floatSquares) maxSquareId = Math.max(maxSquareId, s.id);
  coneState.nextQuadId   = Math.max(maxQuadId   + 1,
    (typeof data.nextQuadId   === "number") ? data.nextQuadId   : 1);
  coneState.nextSquareId = Math.max(maxSquareId + 1,
    (typeof data.nextSquareId === "number") ? data.nextSquareId : 1);

  _clearTransientState();

  for (let i = 0; i < floatSquares.length; i++) {
    const sq = floatSquares[i];
    const qi = quadIdxById(sq.quadId);
    if (qi >= 0) sq.v = clampCloneV(qi, sq.v);
  }

  syncPanelSliders();
  notifyChanged();
}

export function loadSceneFromText(text) {
  let data;
  try {
    data = JSON.parse(text);
  } catch (e) {
    flashStatus("Load failed: not valid JSON", "bad");
    return false;
  }
  try {
    applySceneJSON(data);
  } catch (e) {
    flashStatus("Load failed: " + e.message, "bad");
    return false;
  }
  flashStatus("Loaded " + quads.length + " patch(es) and "
              + floatSquares.length + " shape(s)", "ok");
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
      flashStatus("Load failed: could not read file", "bad");
      inp.value = "";
    };
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
