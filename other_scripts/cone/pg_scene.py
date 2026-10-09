"""
pg_scene.py — save and load the whole scene to JSON.

The scene is the complete editable state of the workspace:

    cone state      ax, ay, depth, halfAngle, ringCount, meridianCount
    shapeDepthCone  the cone view's SHAPE_DEPTH_CONE multiplier
    patches         id, name, phi0, phi1, s0, s1, mirror,
                    mirrorAngle, mirrorFlip
    squares         id, name, quadId, u, v, scaleU, scaleV, theta,
                    slope, visualBottom
    id counters     nextQuadId, nextSquareId
    kraOptions      the KRA-export meta-options block

The save and the load both speak the same JSON shape.  Saving writes
a data URL and triggers a download; loading opens a file picker, reads
the file, and applies it in place.

KRA options
===========
The KRA-export meta-options (text position, text padding, draw
rectangles, text color) are saved alongside the geometry, so a scene
reloaded later produces the same .kra without the user re-setting
every dropdown.

The functions readKraOptionsFromDOM and applyKraOptionsToDOM live in
pg_kra.py and are the single point of truth for what the options
are.  This module simply calls them: adding a new option in pg_kra
automatically makes it part of the scene, with no change here.

The `kraOptions` field is optional on load: a scene file from before
the field existed simply does not restore the panel's option
controls, leaving them at whatever the current session already had.

File shape
==========
    {
      "sceneVersion": 1,
      "type":         "cone_scene",
      "generatedAt":  "2026-...",
      "cone":         { ... },
      "shapeDepthCone": 0.5,
      "nextQuadId":   3,
      "nextSquareId": 5,
      "patches":      [ ... ],
      "squares":      [ ... ],
      "kraOptions":   { ... }
    }

The "type" field is a plain string tag so a scene file can be
distinguished from the visual-state export (which uses schemaVersion
and has a completely different shape).  On load, an unknown type is
rejected with a clear message rather than silently misapplied.

Applying a scene
================
The scene replaces the contents of `quads` and `floatSquares` without
rebinding them — the arrays are cleared by truncation and refilled by
push — because they are `const` and other modules hold references to
the same objects.  Cone fields are assigned one by one for the same
reason.  Every drag slot on `state` is cleared so a load mid-drag
cannot leave a stale index pointing into a cleared array.

After applying, `syncPanelSliders`, `syncQuadList`, and `draw` are
called to bring every view and the panel into agreement with the new
state.

SHAPE DEPTH
===========
Only the cone view's depth multiplier is persisted.  `SHAPE_DEPTH_FLAT`
is a fixed const in pg_view_squares.py and is not written; a scene
loaded on a build with a different flat-view constant will not shift
the flat view's footprint.

SLOPE
=====
The per-square `slope` field is persisted alongside `theta`.  Files
written before slope existed simply omit it; the loader defaults a
missing value to 0 (shape flat on its patch), so old scenes load
unchanged.

MIRROR
======
The per-patch `mirror`, `mirrorAngle`, and `mirrorFlip` fields are
persisted alongside the patch's φ and s bounds.  Files written
before any of these existed simply omit them; the loader defaults
`mirror` to false, `mirrorAngle` to π, and `mirrorFlip` to true, so
old scenes load unchanged.

VISUAL BOTTOM
=============
The per-square `visualBottom` boolean is persisted alongside the
square's geometry.  Files written before this field existed simply
omit it; the loader defaults a missing value to false, so old
scenes load with the historical longest-edge reading of the
shape's "bottom".  See the VISUAL BOTTOM section in pg_panel.py
for the semantics.
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

    /* KRA-export meta-options.  The mapping between DOM controls
       and this object is defined entirely inside pg_kra.py, via
       readKraOptionsFromDOM / applyKraOptionsToDOM.  Adding a new
       option there makes it automatically part of the scene. */
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

/* Clear every drag slot on `state` so a load cannot leave a stale
   index pointing into a cleared array.  Also clears the selection
   and any in-progress drawing. */
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

  if (typeof drawing        !== "undefined") drawing        = null;
  if (typeof drawingPreview !== "undefined") drawingPreview = null;

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

  /* ---- cone ---------------------------------------------------- */
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
    /* Legacy scenes stored a single factor; treat it as the cone
       view's, since that is the one the panel now drives. */
    SHAPE_DEPTH_CONE = data.shapeDepth;
  }
  /* SHAPE_DEPTH_FLAT is a fixed const and is not restored. */

  /* ---- KRA meta-options --------------------------------------- */
  /* The DOM ⇄ object mapping lives in pg_kra.py.  Older scenes
     without this field simply leave the panel's option controls
     at whatever the current session already has. */
  if (data.kraOptions && typeof applyKraOptionsToDOM === "function") {
    applyKraOptionsToDOM(data.kraOptions);
  }

  /* ---- patches ------------------------------------------------- */
  const patches = Array.isArray(data.patches) ? data.patches : [];
  const squares = Array.isArray(data.squares) ? data.squares : [];

  /* quads and floatSquares are `const` bindings; the arrays cannot
     be reassigned, but their contents can be cleared and refilled
     in place.  Every module holds the same reference throughout. */
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
    });
  }

  floatSquares.length = 0;
  for (const s of squares) {
    if (typeof s.id     !== "number") continue;
    if (typeof s.quadId !== "number") continue;

    /* Skip a square whose patch is not in the file — it would be
       an orphan with no plane to live on. */
    let quadExists = false;
    for (const q of quads) {
      if (q.id === s.quadId) { quadExists = true; break; }
    }
    if (!quadExists) continue;

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
      slope:  (typeof s.slope === "number") ? s.slope : 0,
      visualBottom: !!s.visualBottom,
    });
  }

  /* ---- id counters -------------------------------------------- */
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

  /* ---- clean up ----------------------------------------------- */
  _clearTransientState();

  /* Re-clamp each shape against the new cone: a sq.v that was legal
     on the old cone may need pulling back on the new one. */
  for (let i = 0; i < floatSquares.length; i++) {
    const sq = floatSquares[i];
    const qi = quadIdxById(sq.quadId);
    if (qi >= 0) sq.v = clampCloneV(qi, sq.v);
  }

  /* ---- refresh views and panel -------------------------------- */
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

/* A hidden <input type=file> is created on first use and reused
   thereafter, so clicking Load does not create a new element per
   click.  Its value is cleared after each read so the same file can
   be loaded twice in a row. */
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
