"""
pg_scene.py — save and load the whole scene to JSON.

The scene is the complete editable state of the workspace:

    cone state      ax, ay, depth, halfAngle, ringCount, meridianCount
    shapeDepthCone  the cone view's SHAPE_DEPTH_CONE multiplier
    shapeStandingMult
                    the global hinge standing-height multiplier
    patches         id, name, phi0, phi1, s0, s1, mirror,
                    mirrorAngle, mirrorFlip, warp
    squares         id, name, quadId, u, v, scaleU, scaleV, theta,
                    slope, visualBottom
    id counters     nextQuadId, nextSquareId
    kraOptions      the KRA-export meta-options block

File shape:

    {
      "sceneVersion": 1,
      "type":         "cone_scene",
      "generatedAt":  "2026-...",
      "cone":         { ... },
      "shapeDepthCone": 0.5,
      "shapeStandingMult": 2.0,
      "nextQuadId":   3,
      "nextSquareId": 5,
      "patches":      [ ... ],
      "squares":      [ ... ],
      "kraOptions":   { ... }
    }

The "type" field is a plain string tag so a scene file can be
distinguished from the visual-state export (which uses schemaVersion
and has a completely different shape).

Applying a scene
================
The scene replaces the contents of `quads` and `floatSquares` without
rebinding them — the arrays are cleared by truncation and refilled by
push — because they are `const` and other modules hold references to
the same objects.

STAGE 1 CLEANUP
===============
`_clearTransientState` used to clear two locals, `drawing` and
`drawingPreview`, that no longer exist anywhere in the codebase
(they were from an earlier "draw a patch freeform" experiment).
The `typeof ... !== "undefined"` guards made the lines legal but
dead.  Removed.
"""


SCENE_JS = r"""
/* ==========================================================================
   SCENE SAVE / LOAD
   ========================================================================== */

const SCENE_VERSION = 1;
const SCENE_TYPE    = "cone_scene";
const SCENE_FILE    = "cone_scene.json";

function buildSceneJSON() {
  return {
    sceneVersion: SCENE_VERSION,
    type:         SCENE_TYPE,
    generatedAt:  new Date().toISOString(),

    cone: {
      ax:            cone.ax,
      ay:            cone.ay,
      depth:         cone.depth,
      halfAngle:     cone.halfAngle,
      ringCount:     cone.ringCount,
      meridianCount: cone.meridianCount,
    },

    shapeDepthCone: SHAPE_DEPTH_CONE,
    shapeStandingMult: SHAPE_STANDING_HEIGHT_MULT,

    nextQuadId:   nextQuadId,
    nextSquareId: nextSquareId,

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
    flashStatus("Saved " + quads.length + " patch(es) and "
                + floatSquares.length + " shape(s)", "ok");
  }
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

  selectedQuad   = -1;
  selectedSquare = -1;
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
                    + data.sceneVersion
                    + " (expected " + SCENE_VERSION + ")");
  }

  if (data.cone && typeof data.cone === "object") {
    const c = data.cone;
    if (typeof c.ax            === "number") cone.ax            = c.ax;
    if (typeof c.ay            === "number") cone.ay            = c.ay;
    if (typeof c.depth         === "number") cone.depth         = c.depth;
    if (typeof c.halfAngle     === "number") cone.halfAngle     = c.halfAngle;
    if (typeof c.ringCount     === "number") cone.ringCount     = c.ringCount;
    if (typeof c.meridianCount === "number") cone.meridianCount = c.meridianCount;
  }

  if (typeof data.shapeDepthCone === "number") {
    SHAPE_DEPTH_CONE = data.shapeDepthCone;
  } else if (typeof data.shapeDepth === "number") {
    SHAPE_DEPTH_CONE = data.shapeDepth;
  }

  if (typeof data.shapeStandingMult === "number") {
    SHAPE_STANDING_HEIGHT_MULT =
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
  nextQuadId   = Math.max(maxQuadId   + 1,
                          (typeof data.nextQuadId   === "number")
                            ? data.nextQuadId   : 1);
  nextSquareId = Math.max(maxSquareId + 1,
                          (typeof data.nextSquareId === "number")
                            ? data.nextSquareId : 1);

  _clearTransientState();

  for (let i = 0; i < floatSquares.length; i++) {
    const sq = floatSquares[i];
    const qi = quadIdxById(sq.quadId);
    if (qi >= 0) sq.v = clampCloneV(qi, sq.v);
  }

  syncPanelSliders();
  syncQuadList();
  draw();
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
    flashStatus("Loaded " + quads.length + " patch(es) and "
                + floatSquares.length + " shape(s)", "ok");
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
