"""
pg_ruler.py — the ruler tool, self-contained.

One self-contained measuring tool: state, snap, step-skip state
machine, raycast, dimension-line geometry, rendering, keyboard, and
the panel-inspector mutators the panel wires its MEASURE fields to.

State model
-----------
state.ruler is a field of the playground's top-level state object.  It
owns three kinds of things:

    • the pending measurement — a first-placed point, an optional
      second-endpoint lock (pendingEnd) set by the numeric input,
      an optional axis override (axisPref), and the step-skip flags
    • the completed measurements array, plus the hovered and selected
      indices
    • the numeric-input bookkeeping — which field is open, and which
      measurement it is editing

The pendingEnd lock is what makes the numeric input work: after a
Shift+D or Shift+A commit, the second endpoint is pinned at the typed
value, marked with a cyan ring, and the cursor no longer moves it.
A subsequent click commits at the pinned position.  Escape releases
the lock along with the pending point.

Step skip
---------
Shift held while crossing a step sets pendingJumpedStep to true and
the flag persists after release.  The only way back to the near side
is a deliberate return: within snap range of a step edge, meaningfully
closer to that step edge than to any wall, and moving toward it.  See
the RULER MODE docstring for the full model.

Panel inspector
---------------
The panel's MEASURE fields are the persistent counterpart to the
canvas's transient numeric input.  Both edit the same measurements
through the same rules — span preserves the axis, angle forces
aligned — but the panel's fields live as long as the measurement
stays selected.  The panel DOM is constructed in pg_panel; the two
mutators _rulerApplyPanelSpan / _rulerApplyPanelAngle and the per-
frame sync function _rulerSyncMeasureInspector all live here.

Translation
-----------
The transient canvas input's chrome (label text, unit token) and the
Measure / Exit ruler button labels are T()-sourced.  The three
orientation tokens the input shows — "span", "angle", "°", "cm" — are
covered by rlInputSpan / rlInputAngle / rlUnitCm / rlUnitDeg.
"""


RULER_JS = r"""
/* ==========================================================================
   RULER MODE
   ==========================================================================
   A measuring tool for the floor plan.  Click a point, click another,
   get a dimension line in the drafting idiom: extension lines from each
   anchor, a dimension line between them, 45° ticks at each end, and a
   value pill at the midpoint.

   Points snap, in priority order, to wall corners, wall edges, column
   edges, step boundaries, and floor grid lines.  A ruler measures
   straight-line plan distance — it does not detour around steps,
   columns, or walls.  Anchors are stored in world mm, so a zoom or pan
   does not move the measurement.

   Selection and the panel inspector
   ---------------------------------
   Clicking a completed measurement's value pill selects it.  The
   selected measurement is drawn in cyan (its pill, extension lines,
   ticks, dots), and the MEASURE section of the control panel becomes
   visible with:

       • a small index badge ("#3 / 7"),
       • a Span field (centimetres),
       • an Angle field (degrees),
       • a Delete measurement button.

   Editing either field updates the measurement live.  The span edit
   preserves the current axis (an EW measurement stays horizontal, an
   NS measurement stays vertical, an aligned measurement keeps its
   direction); the angle edit forces the measurement to `aligned`,
   because entering an exact angle only makes sense on a diagonal.

   Clicking empty canvas deselects and places the first point of a
   new measurement.  Right-click and Escape cancel as before, and
   also clear the selection.  Backspace removes the selected
   measurement if there is one; otherwise it pops the last one.

   Axis — EW/NS preference, Alt for free mode
   ------------------------------------------
   By default a measurement is axis-constrained: the axis is chosen from
   the dominant direction of the two points (|dx| >= |dy| → EW, else NS),
   and the second point is projected onto that axis.  Holding Alt while
   clicking the second point forces `aligned` — the true point-to-point
   distance.

   Shift+X cycles an explicit axis override on the pending point or on
   a hovered completed measurement: EW → NS → aligned → back to auto.
   Precedence, strongest first:

       Alt (momentary)  >  Shift+X override (sticky)  >  auto EW/NS

   Numeric entry — Shift+D for span, Shift+A for angle
   ---------------------------------------------------
   When there is a pending first point, or a hovered completed
   measurement, a small floating field can be opened:

       Shift+D     — the total span, in centimetres.  The first-placed
                     dot is the origin.  The current axis is preserved.

       Shift+A     — the angle, in degrees, measured from the +x axis
                     (east) counter-clockwise.  Angle entry forces the
                     measurement to `aligned`.

   The field is prefilled with the current value, commits on Enter and
   cancels on Escape.  After a span or angle is committed while a
   measurement is pending, the second endpoint locks at the typed
   position (marked with a cyan ring) and does not follow the cursor;
   the next click commits at that lock.

   The panel inspector carries the same two quantities, but persistent
   — so a selected measurement can be refined at leisure rather than
   in a transient popup.

   Step skip — Shift held, sticky once jumped
   ------------------------------------------
   Step footprint edges are snap targets by default.  Holding Shift
   while the cursor crosses a step edge suspends them: the cursor will
   not be grabbed by a step edge, and the raycast that constrains an
   EW or NS measurement does not stop at one either.  A measurement
   that has crossed a step therefore reaches the wall behind it.

   The skip is not "while Shift is held" — it is "once a step has been
   crossed, this measurement stays on the far side."  A momentary
   signal decides the jump; the jump itself is persistent.  Releasing
   Shift does not undo it.

   The only way the measurement returns to the near side is if the
   cursor deliberately comes back to a step edge: it must be within
   snap range of a step edge, must be *meaningfully* closer to that
   step edge than to any wall, AND must be moving toward it.

   Interaction
   -----------
   Shift+R      — toggle ruler mode
   click        — place a point, or commit the measurement
   click pill   — select the measurement for editing in the panel
   Alt+click    — finish in free mode (true point-to-point distance)
   Shift+click  — skip step edges; the jump is sticky once made
   Shift+D      — numeric span entry (transient, canvas)
   Shift+A      — numeric angle entry (transient, canvas)
   right-click  — cancel the pending point; with none, exit ruler mode
   Esc          — cancel pending; with none, exit ruler mode
   Shift+X      — cycle the axis of the pending / hovered measurement
   Backspace    — delete the selected measurement, else pop the last
   panel Delete — delete the selected measurement

   The dimension line is offset toward the plan's center of mass, so it
   never sits on top of the geometry it is measuring. */

state.ruler = {
  active:            false,
  pending:           null,   // { x, y, snap } — the first-placed point
  measures:          [],     // completed measurements
  hoverPt:           null,   // { x, y, snap } — current snapped cursor
  hoverIdx:          -1,     // index of hovered measurement, or -1
  selected:          -1,     // index of selected measurement, or -1
  axisPref:          null,   // explicit axis override for the pending point
  ignoreSteps:       false,  // derived — skip step edges right now
  pendingJumpedStep: false,  // sticky — this measurement has crossed a step
  _hasLeftStepEdge:  false,  // internal — cursor has been ≥ snap radius
                             // away from any step edge since the flag was set
  _lastDStep:        Infinity, // internal — previous move's dStep

  /* Numeric-entry state for the CANVAS transient field.  pendingEnd /
     pendingAxis lock the second endpoint of a pending measurement to
     a value typed via Shift+D or Shift+A; input records which field
     is open and, for the hovered-measure case, which measurement it
     is editing.  The panel inspector keeps its own state in the DOM
     — the two are complementary, not shared. */
  pendingEnd:        null,   // { x, y } | null — locked second endpoint
  pendingAxis:       null,   // "ew" | "ns" | "aligned" | null
  input: {
    mode:          null,     // "dist" | "angle" | null
    targetMeasure: null,     // measurement object, or null for pending
  },
};

const RULER_SNAP_PX = 14;   // snap radius in screen pixels
const RULER_OFF_PX  = 26;   // dimension-line offset from the anchors


/* ---- Numeric input element (canvas, transient) ---------------------
   A small floating field that opens next to the relevant endpoint.
   The element is created once at script load; _rulerOpenNumericInput
   shows it, _rulerCommitNumericInput reads it, _rulerCloseNumericInput
   hides it.  Enter commits, Escape cancels; blur also cancels so a
   click elsewhere on the canvas does not leave a dangling field.

   This is the CANVAS-side transient input.  The panel's persistent
   inspector is a separate pair of fields, wired in pg_panel.  Both
   edit the same underlying measurements via _rulerApplyPanelSpan /
   _rulerApplyPanelAngle — those two functions are the shared mutators,
   and the canvas input's commit path uses the same logic inline
   because it also has to handle the pending-measurement lock. */

const _RULER_NUM_INPUT_EL = (() => {
  const el = document.createElement("div");
  el.id = "rulerNumericInput";
  el.innerHTML =
    '<span class="lbl" id="rulerNumericLabel">' + T("rlInputSpan") +
      '</span>' +
    '<input type="text" inputmode="decimal" id="rulerNumericField" ' +
      'autocomplete="off" spellcheck="false">' +
    '<span class="unit" id="rulerNumericUnit">' + T("rlUnitCm") + '</span>';
  document.body.appendChild(el);
  const field = el.querySelector("#rulerNumericField");
  field.addEventListener("keydown", (e) => {
    if (e.key === "Enter") {
      e.preventDefault();
      _rulerCommitNumericInput();
    } else if (e.key === "Escape") {
      e.preventDefault();
      _rulerCloseNumericInput();
    }
  });
  field.addEventListener("blur", () => {
    /* Blur may fire before the canvas mousedown handler runs; hide
       the field so the click is not intercepted by a stale input.
       The commit path itself calls _rulerCloseNumericInput, which
       hides and clears — calling it again here is a no-op. */
    _rulerCloseNumericInput();
  });
  return el;
})();

function _rulerCloseNumericInput() {
  state.ruler.input.mode = null;
  state.ruler.input.targetMeasure = null;
  if (_RULER_NUM_INPUT_EL) {
    _RULER_NUM_INPUT_EL.classList.remove("show");
    const f = _RULER_NUM_INPUT_EL.querySelector("#rulerNumericField");
    if (f) f.value = "";
  }
}

/* Decide the input's target and prefill, then show the field.  The
   target is: the pending measurement if a first point exists, else
   the currently hovered completed measurement, else the selected
   one.  If none, the field is not opened. */
function _rulerOpenNumericInput(mode) {
  if (!state.ruler.active) return;
  const targetMeasure = state.ruler.pending
    ? null
    : (state.ruler.hoverIdx >= 0
        ? state.ruler.measures[state.ruler.hoverIdx]
        : (state.ruler.selected >= 0
            ? state.ruler.measures[state.ruler.selected]
            : null));
  if (!state.ruler.pending && !targetMeasure) return;

  state.ruler.input.mode          = mode;
  state.ruler.input.targetMeasure = targetMeasure;

  const labelEl = document.getElementById("rulerNumericLabel");
  const unitEl  = document.getElementById("rulerNumericUnit");
  const field   = document.getElementById("rulerNumericField");
  if (!labelEl || !unitEl || !field) return;

  labelEl.textContent = (mode === "angle") ? T("rlInputAngle")
                                           : T("rlInputSpan");
  unitEl.textContent  = (mode === "angle") ? T("rlUnitDeg")
                                           : T("rlUnitCm");

  let prefill = "";
  let anchorX = 0, anchorY = 0;

  if (state.ruler.pending) {
    const p = state.ruler.pending;
    const end = state.ruler.pendingEnd
      ? state.ruler.pendingEnd
      : (state.ruler.hoverPt || p);
    if (mode === "dist") {
      const span = Math.hypot(end.x - p.x, end.y - p.y);
      prefill = (span / 10).toFixed(1);
    } else {
      let deg = Math.atan2(end.y - p.y, end.x - p.x) * 180 / Math.PI;
      if (!isFinite(deg)) deg = 0;
      prefill = deg.toFixed(1);
    }
    const [sx, sy] = w2sFloor(end.x, end.y);
    anchorX = sx; anchorY = sy;
  } else {
    const m = targetMeasure;
    if (mode === "dist") {
      const span = Math.hypot(m.x1 - m.x0, m.y1 - m.y0);
      prefill = (span / 10).toFixed(1);
    } else {
      let deg = Math.atan2(m.y1 - m.y0, m.x1 - m.x0) * 180 / Math.PI;
      if (!isFinite(deg)) deg = 0;
      prefill = deg.toFixed(1);
    }
    const pillPos = _rulerMeasurePillPos(m);
    anchorX = pillPos.x; anchorY = pillPos.y;
  }

  field.value = prefill;

  const w = 240, h = 32;
  let px = anchorX + 24;
  let py = anchorY - h - 12;
  if (px + w > window.innerWidth  - 12) px = anchorX - w - 24;
  if (py < 12)                          py = anchorY + 24;
  if (px < 12)                          px = 12;
  if (py > window.innerHeight - h - 12) py = window.innerHeight - h - 12;
  _RULER_NUM_INPUT_EL.style.left = px + "px";
  _RULER_NUM_INPUT_EL.style.top  = py + "px";

  _RULER_NUM_INPUT_EL.classList.add("show");
  field.focus();
  field.select();
}

function _rulerCommitNumericInput() {
  const field = document.getElementById("rulerNumericField");
  if (!field) return;
  const mode = state.ruler.input.mode;
  const targetMeasure = state.ruler.input.targetMeasure;
  if (!mode) { _rulerCloseNumericInput(); return; }

  const raw = field.value.trim();
  const v = parseFloat(raw);
  if (!isFinite(v)) { _rulerCloseNumericInput(); return; }

  const applyingPending = !!state.ruler.pending;
  const applyingMeasure = !applyingPending && !!targetMeasure;
  if (!applyingPending && !applyingMeasure) {
    _rulerCloseNumericInput();
    return;
  }

  const origin = applyingPending
    ? state.ruler.pending
    : { x: targetMeasure.x0, y: targetMeasure.y0 };

  if (mode === "dist") {
    const distMm = v * 10;   // cm → mm
    let dirx = 1, diry = 0;
    if (applyingPending) {
      const end = state.ruler.pendingEnd
        ? state.ruler.pendingEnd
        : (state.ruler.hoverPt || origin);
      const dx = end.x - origin.x, dy = end.y - origin.y;
      const L = Math.hypot(dx, dy);
      if (L > 1e-3) { dirx = dx / L; diry = dy / L; }
    } else {
      const axis = targetMeasure.axis;
      if (axis === "ew") {
        dirx = Math.sign(targetMeasure.x1 - targetMeasure.x0) || 1;
        diry = 0;
      } else if (axis === "ns") {
        dirx = 0;
        diry = Math.sign(targetMeasure.y1 - targetMeasure.y0) || 1;
      } else {
        const dx = targetMeasure.x1 - targetMeasure.x0;
        const dy = targetMeasure.y1 - targetMeasure.y0;
        const L = Math.hypot(dx, dy);
        if (L > 1e-3) { dirx = dx / L; diry = dy / L; }
      }
    }

    let nx, ny, newAxis;
    if (applyingPending) {
      let axis = state.ruler.axisPref;
      if (!axis) {
        const hx = state.ruler.hoverPt ? state.ruler.hoverPt.x : origin.x + dirx;
        const hy = state.ruler.hoverPt ? state.ruler.hoverPt.y : origin.y + diry;
        axis = _rulerPickAxis(origin.x, origin.y, hx, hy, null, false);
      }
      newAxis = axis;
      if (axis === "ew") {
        const sgn = (dirx >= 0) ? 1 : -1;
        nx = origin.x + sgn * distMm;
        ny = origin.y;
      } else if (axis === "ns") {
        const sgn = (diry >= 0) ? 1 : -1;
        nx = origin.x;
        ny = origin.y + sgn * distMm;
      } else {
        nx = origin.x + dirx * distMm;
        ny = origin.y + diry * distMm;
      }
      state.ruler.pendingEnd  = { x: nx, y: ny };
      state.ruler.pendingAxis = newAxis;
      state.ruler.axisPref    = newAxis;
    } else {
      newAxis = targetMeasure.axis;
      if (newAxis === "ew") {
        const sgn = (dirx >= 0) ? 1 : -1;
        targetMeasure.x1 = origin.x + sgn * distMm;
        targetMeasure.y1 = origin.y;
      } else if (newAxis === "ns") {
        const sgn = (diry >= 0) ? 1 : -1;
        targetMeasure.x1 = origin.x;
        targetMeasure.y1 = origin.y + sgn * distMm;
      } else {
        targetMeasure.x1 = origin.x + dirx * distMm;
        targetMeasure.y1 = origin.y + diry * distMm;
      }
      targetMeasure.ox1 = targetMeasure.x1;
      targetMeasure.oy1 = targetMeasure.y1;
    }
  } else if (mode === "angle") {
    const theta = v * Math.PI / 180;
    const dirx = Math.cos(theta);
    const diry = Math.sin(theta);

    let distMm = 1000;
    if (applyingPending) {
      const end = state.ruler.pendingEnd
        ? state.ruler.pendingEnd
        : (state.ruler.hoverPt || origin);
      const L = Math.hypot(end.x - origin.x, end.y - origin.y);
      if (L > 1.0) distMm = L;
    } else {
      const L = Math.hypot(targetMeasure.x1 - origin.x,
                           targetMeasure.y1 - origin.y);
      if (L > 1.0) distMm = L;
    }

    const nx = origin.x + dirx * distMm;
    const ny = origin.y + diry * distMm;

    if (applyingPending) {
      state.ruler.pendingEnd  = { x: nx, y: ny };
      state.ruler.pendingAxis = "aligned";
      state.ruler.axisPref    = "aligned";
    } else {
      targetMeasure.axis = "aligned";
      targetMeasure.x1 = nx;
      targetMeasure.y1 = ny;
      targetMeasure.ox1 = nx;
      targetMeasure.oy1 = ny;
    }
  }

  _rulerCloseNumericInput();
  draw();
}


/* ---- Panel inspector mutators --------------------------------------
   The two functions the panel's MEASURE fields call.  They edit the
   currently selected measurement in place, using the same axis-
   preservation rules as the canvas's Shift+D / Shift+A numeric
   entry.  A separate path from _rulerCommitNumericInput because the
   panel never has a pending measurement to lock — it always edits a
   completed one. */

function _rulerApplyPanelSpan(newSpanMm) {
  const sel = state.ruler.selected;
  if (sel < 0 || sel >= state.ruler.measures.length) return;
  const m = state.ruler.measures[sel];
  if (!m) return;

  const ox = m.x0, oy = m.y0;
  let dirx = 1, diry = 0;

  if (m.axis === "ew") {
    dirx = Math.sign(m.x1 - m.x0) || 1;
    diry = 0;
  } else if (m.axis === "ns") {
    dirx = 0;
    diry = Math.sign(m.y1 - m.y0) || 1;
  } else {
    const dx = m.x1 - m.x0, dy = m.y1 - m.y0;
    const L = Math.hypot(dx, dy);
    if (L > 1e-3) { dirx = dx / L; diry = dy / L; }
  }

  let nx, ny;
  if (m.axis === "ew") {
    nx = ox + (dirx >= 0 ? newSpanMm : -newSpanMm);
    ny = oy;
  } else if (m.axis === "ns") {
    nx = ox;
    ny = oy + (diry >= 0 ? newSpanMm : -newSpanMm);
  } else {
    nx = ox + dirx * newSpanMm;
    ny = oy + diry * newSpanMm;
  }

  m.x1  = nx;  m.y1  = ny;
  m.ox1 = nx;  m.oy1 = ny;
}

function _rulerApplyPanelAngle(deg) {
  const sel = state.ruler.selected;
  if (sel < 0 || sel >= state.ruler.measures.length) return;
  const m = state.ruler.measures[sel];
  if (!m) return;

  const ox = m.x0, oy = m.y0;
  const theta = deg * Math.PI / 180;
  const dirx = Math.cos(theta);
  const diry = Math.sin(theta);

  const span = Math.hypot(m.x1 - m.x0, m.y1 - m.y0) || 1000;

  m.axis = "aligned";
  m.x1 = ox + dirx * span;
  m.y1 = oy + diry * span;
  m.ox1 = m.x1;
  m.oy1 = m.y1;
}

/* Push the current selected-measurement state into the panel's
   MEASURE fields, and show / hide the section as appropriate.
   Called from draw() so every state change reaches the panel within
   one frame.

   The activeElement guard is what stops the two-way loop: when the
   user is typing in the span field, its own `input` handler mutates
   the measurement and calls draw(); draw() calls this function; this
   function must not overwrite the field the user is typing in.  It
   checks each field against document.activeElement before writing.
   The other field still updates, which is what we want — a live
   span edit changes the angle only if the axis changed, and the user
   should see that reflected.

   The visible-display rule is an explicit "block" rather than the
   default: the section starts with an inline style="display: none"
   and the dark stylesheet carries "#ui .measureSection { display:
   none }".  Clearing the inline style would let the stylesheet's
   rule win and the section would stay hidden; setting display:
   block inline is what actually shows it. */
function _rulerSyncMeasureInspector() {
  const section = document.getElementById("measureSection");
  const idxEl   = document.getElementById("measureIndex");
  const spanEl  = document.getElementById("measureSpan");
  const angleEl = document.getElementById("measureAngle");
  if (!section || !idxEl || !spanEl || !angleEl) return;

  const sel   = state.ruler.selected;
  const total = state.ruler.measures.length;

  if (!state.ruler.active || sel < 0 || sel >= total) {
    section.style.display = "none";
    return;
  }
  section.style.display = "block";

  const m = state.ruler.measures[sel];
  idxEl.textContent = "#" + (sel + 1) + " / " + total;

  const spanMm   = Math.hypot(m.x1 - m.x0, m.y1 - m.y0);
  const angleDeg = Math.atan2(m.y1 - m.y0, m.x1 - m.x0) * 180 / Math.PI;

  if (document.activeElement !== spanEl) {
    spanEl.value = (spanMm / 10).toFixed(2);
  }
  if (document.activeElement !== angleEl) {
    angleEl.value = angleDeg.toFixed(2);
  }
}


/* ---- Snap candidate collection -------------------------------------- */

function _rulerSnapCandidates(wx, wy) {
  const cands = [];

  /* Wall endpoints and edges.  isSegHidden is deliberately NOT
     consulted: the ruler measures the room, and focus mode is a
     strip-only concept.

     Step-kind WALL.segments are the free risers (the parts of the
     step footprint that are not covered by any wall).  When skip
     mode is armed, they are excluded from the candidate set so
     the cursor cannot be pulled back to them. */
  for (let i = 0; i < WALL.segments.length; i++) {
    const s = WALL.segments[i];
    if (state.ruler.ignoreSteps && s.kind === "step") continue;
    const ax = s.a[0], ay = s.a[1], bx = s.b[0], by = s.b[1];
    cands.push({ kind: "corner", x: ax, y: ay });
    cands.push({ kind: "corner", x: bx, y: by });
    const dx = bx - ax, dy = by - ay;
    const L2 = dx * dx + dy * dy;
    if (L2 < 1e-9) continue;
    let t = ((wx - ax) * dx + (wy - ay) * dy) / L2;
    t = Math.max(0, Math.min(1, t));
    cands.push({ kind: "wall", x: ax + t * dx, y: ay + t * dy });
  }

  /* Column edges and corners. */
  for (const col of (GEOMETRY.columns || [])) {
    const [x0, y0, x1, y1] = col.box;
    const ring = [[x0, y0], [x1, y0], [x1, y1], [x0, y1]];
    for (let i = 0; i < 4; i++) {
      const A = ring[i], B = ring[(i + 1) % 4];
      cands.push({ kind: "corner", x: A[0], y: A[1] });
      const dx = B[0] - A[0], dy = B[1] - A[1];
      const L2 = dx * dx + dy * dy;
      if (L2 < 1e-9) continue;
      let t = ((wx - A[0]) * dx + (wy - A[1]) * dy) / L2;
      t = Math.max(0, Math.min(1, t));
      cands.push({ kind: "column", x: A[0] + t * dx, y: A[1] + t * dy });
    }
  }

  /* Step-polygon edges (outer rings only).  These are what make
     "wall → step" measurable: the step's footprint edge is a real
     plan feature the ruler can anchor to.  Skipped when skip mode
     is armed. */
  if (!state.ruler.ignoreSteps) {
    for (const face of GEOMETRY.stepFaces) {
      const ring = face.outer;
      for (let i = 0; i < ring.length; i++) {
        const A = ring[i], B = ring[(i + 1) % ring.length];
        const dx = B[0] - A[0], dy = B[1] - A[1];
        const L2 = dx * dx + dy * dy;
        if (L2 < 1e-9) continue;
        let t = ((wx - A[0]) * dx + (wy - A[1]) * dy) / L2;
        t = Math.max(0, Math.min(1, t));
        cands.push({ kind: "step", x: A[0] + t * dx, y: A[1] + t * dy });
      }
    }
  }

  /* Floor grid lines — clipped to the plan bounds. */
  const b = GEOMETRY.bounds;
  for (const g of state.floorGrids) {
    if (g.type === "ns") {
      if (wy >= b.minY && wy <= b.maxY) {
        cands.push({ kind: "grid", x: g.pos, y: wy });
      }
    } else {
      if (wx >= b.minX && wx <= b.maxX) {
        cands.push({ kind: "grid", x: wx, y: g.pos });
      }
    }
  }

  return cands;
}

/* Snap the cursor to the nearest candidate within RULER_SNAP_PX.
   Ties are broken by priority: corner > wall/column > step > grid. */
function rulerSnapPoint(wx, wy) {
  const cands = _rulerSnapCandidates(wx, wy);
  const radiusW = RULER_SNAP_PX / Math.max(viewFloor.scale, 1e-9);
  const pri = { corner: 0, wall: 1, column: 1, step: 2, grid: 3 };
  let best = null, bestD = radiusW;
  for (const c of cands) {
    const d = Math.hypot(c.x - wx, c.y - wy);
    if (d > radiusW) continue;
    const better = !best
      || d < bestD - 1e-6
      || (Math.abs(d - bestD) < 1e-6 && pri[c.kind] < pri[best.kind]);
    if (better) { best = c; bestD = d; }
  }
  if (best) return { x: best.x, y: best.y, snap: best.kind };
  return { x: wx, y: wy, snap: "free" };
}


/* ---- Axis choice ---------------------------------------------------- */

/* Pick the axis of a measurement.

     freeMode — Alt held: force aligned (true point-to-point distance)
     override — an explicit Shift+X-cycled preference: EW, NS, or aligned
     default  — EW if |dx| >= |dy|, else NS

   Precedence: Alt beats an explicit override beats the auto pick.
   Alt wins over the override because it is momentary and the user is
   holding it at the exact moment of the click; a sticky override set
   with Shift+X is the second priority; the auto EW/NS pick is the
   fallback that requires no keypress at all. */
function _rulerPickAxis(x0, y0, x1, y1, override, freeMode) {
  if (freeMode) return "aligned";
  if (override) return override;
  const dx = Math.abs(x1 - x0);
  const dy = Math.abs(y1 - y0);
  return (dx >= dy) ? "ew" : "ns";
}


/* ---- Step-skip state ------------------------------------------------
   Helpers for the step-skip state machine.  See the RULER MODE
   docstring for the full model; the terse version is:

     • Shift held                       → skip steps (momentary)
     • pendingJumpedStep true, but no   → skip stays on (sticky)
       deliberate return to the step
     • cursor within snap range of a    → reset the sticky flag
       step edge, meaningfully closer
       to it than to any wall, and
       moving toward it

   The "meaningfully closer to the step edge than to any wall" check
   is the wall-adjacency guard: step footprint polygons are bounded
   by walls on the sides where the step meets a wall, so the step
   edge and the wall's face are geometrically coincident there.  At
   a coincident line both distances are ~0, and the check correctly
   refuses to treat the wall face as a return to the step. */

function _rulerSegsIntersect(ax, ay, bx, by, cx, cy, dx, dy) {
  const d1x = bx - ax, d1y = by - ay;
  const d2x = dx - cx, d2y = dy - cy;
  const denom = d1x * d2y - d1y * d2x;
  if (Math.abs(denom) < 1e-9) return false;
  const t = ((cx - ax) * d2y - (cy - ay) * d2x) / denom;
  const u = ((cx - ax) * d1y - (cy - ay) * d1x) / denom;
  return t > 1e-6 && t < 1 - 1e-6 && u > 1e-6 && u < 1 - 1e-6;
}

function _rulerSegmentCrossesStep(px, py, qx, qy) {
  for (let i = 0; i < WALL.segments.length; i++) {
    const s = WALL.segments[i];
    if (s.kind !== "step") continue;
    if (_rulerSegsIntersect(px, py, qx, qy,
                            s.a[0], s.a[1], s.b[0], s.b[1]))
      return true;
  }
  for (const face of GEOMETRY.stepFaces) {
    const ring = face.outer;
    for (let i = 0; i < ring.length; i++) {
      const A = ring[i], B = ring[(i + 1) % ring.length];
      if (_rulerSegsIntersect(px, py, qx, qy, A[0], A[1], B[0], B[1]))
        return true;
    }
  }
  return false;
}

/* Nearest distance from the cursor to (a) any step edge and (b) any
   wall/column edge, in world mm.  Two separate minima; the reset
   condition compares them. */
function _rulerNearestDistances(wx, wy) {
  let dStep = Infinity, dWall = Infinity;
  for (let i = 0; i < WALL.segments.length; i++) {
    const s = WALL.segments[i];
    const d = pointToSegmentDist(wx, wy, s.a[0], s.a[1], s.b[0], s.b[1]);
    if (s.kind === "step") {
      if (d < dStep) dStep = d;
    } else {
      if (d < dWall) dWall = d;
    }
  }
  for (const face of GEOMETRY.stepFaces) {
    const ring = face.outer;
    for (let i = 0; i < ring.length; i++) {
      const A = ring[i], B = ring[(i + 1) % ring.length];
      const d = pointToSegmentDist(wx, wy, A[0], A[1], B[0], B[1]);
      if (d < dStep) dStep = d;
    }
  }
  return { dStep, dWall };
}

/* Has the cursor deliberately returned to the step?  True when the
   cursor is within snap range of a step edge AND the step edge is
   meaningfully closer than any wall.  The 5 mm margin is the
   discriminating slack: at a coincident wall/step line both
   distances are 0 and the margin correctly rejects. */
function _rulerReturnedToStep(wx, wy) {
  const R = RULER_SNAP_PX / Math.max(viewFloor.scale, 1e-9);
  const { dStep, dWall } = _rulerNearestDistances(wx, wy);
  if (dStep >= R) return false;
  return dStep + 5.0 < dWall;
}

/* Reconcile the skip state from the modifier and the cursor.  Called
   from mousemove, mousedown, and the Shift keyup handler.  The
   direction gate (_hasLeftStepEdge + _lastDStep) is what prevents a
   Shift release just past the riser from immediately resetting the
   jump: on release the cursor is still near the step, has not yet
   left the snap radius, and is not moving toward it. */
function _rulerUpdateSkipState(wx, wy, shiftHeld) {
  const { dStep, dWall } = _rulerNearestDistances(wx, wy);
  const R = RULER_SNAP_PX / Math.max(viewFloor.scale, 1e-9);
  const prevD = state.ruler._lastDStep;
  state.ruler._lastDStep = dStep;

  if (shiftHeld) {
    state.ruler.ignoreSteps = true;
    if (state.ruler.pending &&
        _rulerSegmentCrossesStep(state.ruler.pending.x,
                                 state.ruler.pending.y, wx, wy)) {
      if (!state.ruler.pendingJumpedStep) {
        state.ruler.pendingJumpedStep = true;
        state.ruler._hasLeftStepEdge  = false;
      }
    }
    if (dStep >= R) state.ruler._hasLeftStepEdge = true;
    return;
  }

  if (!state.ruler.pendingJumpedStep) {
    state.ruler.ignoreSteps = false;
    if (dStep >= R) state.ruler._hasLeftStepEdge = true;
    return;
  }

  if (dStep >= R) state.ruler._hasLeftStepEdge = true;

  const nearStep   = dStep < R;
  const notOnWall  = dStep + 5.0 < dWall;
  const approaching = isFinite(prevD) && dStep < prevD - 0.5;
  const hasLeft    = state.ruler._hasLeftStepEdge;

  if (hasLeft && nearStep && notOnWall && approaching) {
    state.ruler.pendingJumpedStep = false;
    state.ruler.ignoreSteps       = false;
    state.ruler._hasLeftStepEdge  = false;
    return;
  }

  state.ruler.ignoreSteps = true;
}


/* ---- Wall raycast --------------------------------------------------- */

/* Cast a unit-direction ray from (px, py) and return the first
   intersection with any WALL.segment, or null.

   The ray is (px, py) + t·(dx, dy) with t > 0.  Each wall segment is a
   2D line segment; the classical 2×2 determinant solve gives t and the
   segment parameter u.  A t just past zero is discarded, so a ray that
   starts exactly on a wall does not "hit" that wall.

   WALL.segments carry no thickness (they are the wall centerlines), so
   the intersection is with the centerline.  That is what the dimension
   line should touch: the wall's own measured line.

   Step-kind segments (risers) are skipped when ignoreSteps is set:
   without this, disabling step snap freed the cursor but the raycast
   still halted the measurement at the riser's plan line — the step
   was still blocking the measurement even though the cursor could
   pass across it. */
function _rulerRaycastWall(px, py, dx, dy) {
  let bestT = Infinity;
  for (let i = 0; i < WALL.segments.length; i++) {
    const s = WALL.segments[i];
    if (state.ruler.ignoreSteps && s.kind === "step") continue;
    const ax = s.a[0], ay = s.a[1];
    const ex = s.b[0] - ax, ey = s.b[1] - ay;
    const rx = ax - px, ry = ay - py;
    const det = -dx * ey + dy * ex;
    if (Math.abs(det) < 1e-9) continue;
    const t = (-rx * ey + ex * ry) / det;
    const u = (dx * ry - dy * rx) / det;
    if (t > 1e-3 && u >= -0.001 && u <= 1.001 && t < bestT) {
      bestT = t;
    }
  }
  if (bestT === Infinity) return null;
  return { x: px + dx * bestT, y: py + dy * bestT, t: bestT };
}


/* ---- Second-point resolution --------------------------------------- */

function _rulerResolveSecondPoint(p, pt, axis) {
  if (axis === "aligned") return { x: pt.x, y: pt.y };

  if (axis === "ew") {
    const dir = Math.sign(pt.x - p.x) || 1;
    let x1 = pt.x;
    const hit = _rulerRaycastWall(p.x, p.y, dir, 0);
    if (hit) {
      const hitDist = Math.abs(hit.x - p.x);
      const curDist = Math.abs(pt.x - p.x);
      if (hitDist <= curDist + 0.5) x1 = hit.x;
    }
    return { x: x1, y: p.y };
  }

  if (axis === "ns") {
    const dir = Math.sign(pt.y - p.y) || 1;
    let y1 = pt.y;
    const hit = _rulerRaycastWall(p.x, p.y, 0, dir);
    if (hit) {
      const hitDist = Math.abs(hit.y - p.y);
      const curDist = Math.abs(pt.y - p.y);
      if (hitDist <= curDist + 0.5) y1 = hit.y;
    }
    return { x: p.x, y: y1 };
  }

  return { x: pt.x, y: pt.y };
}


/* ---- Dimension-line geometry ---------------------------------------- */

function _rulerDimLineEndpoints(sx0, sy0, sx1, sy1, axis) {
  const off = RULER_OFF_PX;
  const b = GEOMETRY.bounds;
  const [cx, cy] = w2sFloor((b.minX + b.maxX) / 2,
                            (b.minY + b.maxY) / 2);

  if (axis === "ew") {
    const midY = (sy0 + sy1) / 2;
    const up = midY > cy;
    const oy = up ? -1 : 1;
    const yy = up ? Math.min(sy0, sy1) - off
                  : Math.max(sy0, sy1) + off;
    return { qx0: sx0, qy0: yy, qx1: sx1, qy1: yy, ox: 0, oy };
  }

  if (axis === "ns") {
    const midX = (sx0 + sx1) / 2;
    const right = midX < cx;
    const ox = right ? 1 : -1;
    const xx = right ? Math.max(sx0, sx1) + off
                     : Math.min(sx0, sx1) - off;
    return { qx0: xx, qy0: sy0, qx1: xx, qy1: sy1, ox, oy: 0 };
  }

  const dx = sx1 - sx0, dy = sy1 - sy0;
  const len = Math.hypot(dx, dy) || 1;
  let nx = -dy / len, ny = dx / len;
  const mx = (sx0 + sx1) / 2, my = (sy0 + sy1) / 2;
  if (nx * (cx - mx) + ny * (cy - my) < 0) { nx = -nx; ny = -ny; }
  return {
    qx0: sx0 + nx * off, qy0: sy0 + ny * off,
    qx1: sx1 + nx * off, qy1: sy1 + ny * off,
    ox: nx, oy: ny,
  };
}

function _rulerMeasurePillPos(m) {
  const [sx0, sy0] = w2sFloor(m.x0, m.y0);
  const [sx1, sy1] = w2sFloor(m.x1, m.y1);
  const E = _rulerDimLineEndpoints(sx0, sy0, sx1, sy1, m.axis);
  return { x: (E.qx0 + E.qx1) / 2, y: (E.qy0 + E.qy1) / 2 };
}

function _rulerHitMeasure(sx, sy) {
  for (let i = state.ruler.measures.length - 1; i >= 0; i--) {
    const p = _rulerMeasurePillPos(state.ruler.measures[i]);
    if (Math.hypot(sx - p.x, sy - p.y) < 14) return i;
  }
  return -1;
}


/* ---- Rendering ------------------------------------------------------ */

function _rulerDrawOne(m, highlighted, pending, locked, selected) {
  const [sx0, sy0] = w2sFloor(m.x0, m.y0);
  const [sx1, sy1] = w2sFloor(m.x1, m.y1);
  const E = _rulerDimLineEndpoints(sx0, sy0, sx1, sy1, m.axis);

  /* Colour priority: a hovered or pending measurement is yellow
     (live / in-flight); a selected measurement is cyan (committed
     and picked for editing); everything else is the base ink. */
  const ink = (highlighted || pending) ? PALETTE.highlight
            : (selected)               ? PALETTE.transit
            :                            PALETTE.ink;

  ctx.save();
  ctx.lineWidth = 0.9;
  ctx.strokeStyle = ink;
  if (pending) ctx.setLineDash([5, 3]);

  ctx.beginPath();
  ctx.moveTo(sx0, sy0); ctx.lineTo(E.qx0, E.qy0);
  ctx.moveTo(sx1, sy1); ctx.lineTo(E.qx1, E.qy1);
  ctx.stroke();

  ctx.beginPath();
  ctx.moveTo(E.qx0, E.qy0); ctx.lineTo(E.qx1, E.qy1);
  ctx.stroke();

  ctx.setLineDash([]);

  const dx = E.qx1 - E.qx0, dy = E.qy1 - E.qy0;
  const len = Math.hypot(dx, dy) || 1;
  const ux = dx / len, uy = dy / len;
  const px = -uy, py = ux;
  const T = DIM_TICK_LEN;
  const t1x = (ux + px) * T / Math.SQRT2;
  const t1y = (uy + py) * T / Math.SQRT2;
  const t2x = (ux - px) * T / Math.SQRT2;
  const t2y = (uy - py) * T / Math.SQRT2;
  for (const [ax2, ay2] of [[E.qx0, E.qy0], [E.qx1, E.qy1]]) {
    ctx.beginPath();
    ctx.moveTo(ax2 - t1x, ay2 - t1y);
    ctx.lineTo(ax2 + t1x, ay2 + t1y);
    ctx.stroke();
    ctx.beginPath();
    ctx.moveTo(ax2 - t2x, ay2 - t2y);
    ctx.lineTo(ax2 + t2x, ay2 + t2y);
    ctx.stroke();
  }

  for (const [ex, ey] of [[sx0, sy0], [sx1, sy1]]) {
    ctx.beginPath();
    ctx.arc(ex, ey, 3, 0, Math.PI * 2);
    ctx.fillStyle = ink;
    ctx.fill();
    ctx.strokeStyle = PALETTE.paper;
    ctx.lineWidth = 1.2;
    ctx.stroke();
  }

  /* When the second endpoint is locked (pendingEnd), give the far
     dot a cyan ring so the "this is a set value, not the cursor"
     state is visible at a glance. */
  if (locked) {
    ctx.beginPath();
    ctx.arc(sx1, sy1, 6, 0, Math.PI * 2);
    ctx.strokeStyle = PALETTE.transit;
    ctx.lineWidth = 1.6;
    ctx.stroke();
  }

  ctx.restore();

  const valueMm = (m.axis === "ew")
    ? Math.abs(m.x1 - m.x0)
    : (m.axis === "ns")
      ? Math.abs(m.y1 - m.y0)
      : Math.hypot(m.x1 - m.x0, m.y1 - m.y0);
  const text = fmtCm(valueMm) + " cm";
  const mx2 = (E.qx0 + E.qx1) / 2;
  const my2 = (E.qy0 + E.qy1) / 2;

  if (highlighted || pending) {
    _drawValuePillColored(mx2, my2, text, PALETTE.highlight);
  } else if (selected) {
    _drawValuePillColored(mx2, my2, text, PALETTE.transit);
  } else {
    _drawValuePill(mx2, my2, text);
  }
}

function drawRulers() {
  /* Completed measurements.  Selected is drawn last so its
     highlight wins over an overlapping unselected one. */
  const n = state.ruler.measures.length;
  for (let i = 0; i < n; i++) {
    if (i === state.ruler.selected) continue;
    _rulerDrawOne(state.ruler.measures[i],
                  i === state.ruler.hoverIdx, false, false, false);
  }
  if (state.ruler.selected >= 0 && state.ruler.selected < n) {
    const i = state.ruler.selected;
    _rulerDrawOne(state.ruler.measures[i],
                  i === state.ruler.hoverIdx, false, false, true);
  }

  /* Live preview from the pending point.  Uses the exact same axis
     pick and second-point resolution as the click handler, so what
     the user sees during hover is what will be committed — unless a
     locked pendingEnd is set, in which case the preview is drawn at
     that locked position and the cursor's hover point is ignored. */
  if (state.ruler.active && state.ruler.pending) {
    let r2 = null, axis = null, locked = false;
    if (state.ruler.pendingEnd) {
      r2 = state.ruler.pendingEnd;
      axis = state.ruler.pendingAxis
           || state.ruler.axisPref
           || "aligned";
      locked = true;
    } else if (state.ruler.hoverPt) {
      const p = state.ruler.pending;
      const q = state.ruler.hoverPt;
      axis = _rulerPickAxis(p.x, p.y, q.x, q.y,
                            state.ruler.axisPref, mouse.alt);
      r2 = _rulerResolveSecondPoint(p, q, axis);
    }
    if (r2 && axis) {
      _rulerDrawOne({ x0: state.ruler.pending.x,
                      y0: state.ruler.pending.y,
                      x1: r2.x, y1: r2.y, axis },
                    false, true, locked, false);
    }
  }

  if (!state.ruler.active) return;

  /* Snap indicator at the cursor.  Suppressed while pendingEnd is
     locked, because the cursor no longer controls the endpoint. */
  if (state.ruler.hoverPt && !state.ruler.pendingEnd) {
    const [hx, hy] = w2sFloor(state.ruler.hoverPt.x, state.ruler.hoverPt.y);
    ctx.save();
    if (state.ruler.hoverPt.snap !== "free") {
      ctx.beginPath();
      ctx.arc(hx, hy, 7, 0, Math.PI * 2);
      ctx.strokeStyle = PALETTE.highlight;
      ctx.lineWidth = 1.6;
      ctx.setLineDash([3, 3]);
      ctx.stroke();
      ctx.setLineDash([]);
      ctx.beginPath();
      ctx.arc(hx, hy, 2.5, 0, Math.PI * 2);
      ctx.fillStyle = PALETTE.highlight;
      ctx.fill();
    } else {
      drawSnapCrosshair(hx, hy);
      ctx.beginPath();
      ctx.arc(hx, hy, 2.5, 0, Math.PI * 2);
      ctx.fillStyle = PALETTE.ink;
      ctx.fill();
    }
    ctx.restore();
  }

  /* Pending-point marker (origin dot). */
  if (state.ruler.pending) {
    const [px, py] = w2sFloor(state.ruler.pending.x, state.ruler.pending.y);
    ctx.save();
    ctx.beginPath();
    ctx.arc(px, py, 5, 0, Math.PI * 2);
    ctx.fillStyle = PALETTE.paper;
    ctx.fill();
    ctx.strokeStyle = PALETTE.highlight;
    ctx.lineWidth = 2;
    ctx.stroke();
    ctx.restore();
  }
}


/* ---- Mode lifecycle ------------------------------------------------- */

function toggleRuler() {
  if (state.ruler.active) {
    state.ruler.active            = false;
    state.ruler.pending           = null;
    state.ruler.axisPref          = null;
    state.ruler.pendingJumpedStep = false;
    state.ruler.ignoreSteps       = false;
    state.ruler._hasLeftStepEdge  = false;
    state.ruler._lastDStep        = Infinity;
    state.ruler.pendingEnd        = null;
    state.ruler.pendingAxis       = null;
    state.ruler.selected          = -1;
    _rulerCloseNumericInput();
  } else {
    if (drawing) cancelDraw(true);
    state.ruler.active            = true;
    state.ruler.pendingJumpedStep = false;
    state.ruler.ignoreSteps       = false;
    state.ruler._hasLeftStepEdge  = false;
    state.ruler._lastDStep        = Infinity;
    state.ruler.pendingEnd        = null;
    state.ruler.pendingAxis       = null;
    state.ruler.selected          = -1;
  }
  updateRulerButton();
  draw();
}

function updateRulerButton() {
  const el = document.getElementById("rulerBtn");
  if (!el) return;
  if (state.ruler.active) {
    el.classList.add("active");
    el.textContent = T("btnExitRuler");
  } else {
    el.classList.remove("active");
    el.textContent = T("btnMeasure");
  }
}


/* ---- Ruler keys -----------------------------------------------------
   Registered as a CAPTURE-phase listener on the window so it fires
   before pg_core's and cable_live's bubble-phase listeners on the same
   element.  In particular:

     • Backspace must pop a ruler measurement, not delete a selected
       cable — the bubble listener in pg_core would otherwise do the
       latter.
     • Escape must cancel a pending ruler point without clearing the
       cable selection.

   Every ruler key requires Shift and explicitly rejects Ctrl / Meta /
   Alt.  Bare R is Ctrl+R / Cmd+R (browser reload) and a handler that
   does not check modifiers turns both the ruler and the page into a
   race.  Shift+D and Shift+A are the numeric-entry keys; Shift+X
   cycles the axis; Shift+R toggles the mode. */
window.addEventListener("keydown", (e) => {
  if (e.key === "Alt")   mouse.alt   = true;
  if (e.key === "Shift") mouse.shift = true;
});
window.addEventListener("keyup", (e) => {
  if (e.key === "Alt")   mouse.alt   = false;
  if (e.key === "Shift") {
    mouse.shift = false;
    if (state.ruler.active) {
      if (!state.ruler.pendingJumpedStep) {
        state.ruler.ignoreSteps = false;
      }
      draw();
    }
  }
});

window.addEventListener("keydown", (e) => {
  const t = e.target;
  /* The numeric input and any other text field swallow their own
     keys (Enter, Escape) via their own listeners; bail out here so a
     stray Shift+letter does not fire a ruler command mid-typing. */
  if (t && (t.tagName === "INPUT" || t.tagName === "TEXTAREA")) return;

  if ((e.key === "r" || e.key === "R") &&
      e.shiftKey && !e.ctrlKey && !e.metaKey && !e.altKey) {
    if (drawing) return;
    e.preventDefault();
    e.stopPropagation();
    toggleRuler();
    return;
  }

  if (!state.ruler.active) return;

  if (e.key === "Escape") {
    e.preventDefault();
    e.stopPropagation();
    if (state.ruler.pending) {
      state.ruler.pending           = null;
      state.ruler.axisPref          = null;
      state.ruler.pendingJumpedStep = false;
      state.ruler.ignoreSteps       = false;
      state.ruler._hasLeftStepEdge  = false;
      state.ruler._lastDStep        = Infinity;
      state.ruler.pendingEnd        = null;
      state.ruler.pendingAxis       = null;
      _rulerCloseNumericInput();
    } else if (state.ruler.selected >= 0) {
      state.ruler.selected = -1;
    } else {
      state.ruler.active            = false;
      state.ruler.pendingJumpedStep = false;
      state.ruler.ignoreSteps       = false;
      state.ruler._hasLeftStepEdge  = false;
      state.ruler._lastDStep        = Infinity;
      state.ruler.pendingEnd        = null;
      state.ruler.pendingAxis       = null;
      state.ruler.selected          = -1;
      _rulerCloseNumericInput();
      updateRulerButton();
    }
    draw();
    return;
  }

  if ((e.key === "x" || e.key === "X") &&
      e.shiftKey && !e.ctrlKey && !e.metaKey && !e.altKey) {
    const cycle = ["ew", "ns", "aligned"];
    if (state.ruler.pending) {
      const cur = state.ruler.axisPref;
      const idx = cur ? cycle.indexOf(cur) : -1;
      state.ruler.axisPref = cycle[(idx + 1) % cycle.length];
      if (state.ruler.pendingEnd) {
        const p = state.ruler.pending;
        const e0 = state.ruler.pendingEnd;
        const dist = Math.hypot(e0.x - p.x, e0.y - p.y);
        const dx = (e0.x - p.x) || 1;
        const dy = (e0.y - p.y) || 0;
        const dirx = dx / Math.hypot(dx, dy);
        const diry = dy / Math.hypot(dx, dy);
        const ax = state.ruler.axisPref;
        let nx, ny;
        if (ax === "ew") {
          nx = p.x + (dirx >= 0 ? dist : -dist);
          ny = p.y;
        } else if (ax === "ns") {
          nx = p.x;
          ny = p.y + (diry >= 0 ? dist : -dist);
        } else {
          nx = p.x + dirx * dist;
          ny = p.y + diry * dist;
        }
        state.ruler.pendingEnd  = { x: nx, y: ny };
        state.ruler.pendingAxis = ax;
      }
    } else if (state.ruler.selected >= 0) {
      const m = state.ruler.measures[state.ruler.selected];
      if (m) {
        const idx = cycle.indexOf(m.axis);
        m.axis = cycle[(idx + 1) % cycle.length];
        if (m.axis === "ew") {
          const dir = Math.sign(m.ox1 - m.x0) || 1;
          let x1 = m.ox1;
          const hit = _rulerRaycastWall(m.x0, m.y0, dir, 0);
          if (hit && Math.abs(hit.x - m.x0) <= Math.abs(m.ox1 - m.x0) + 0.5) {
            x1 = hit.x;
          }
          m.x1 = x1;
          m.y1 = m.y0;
        } else if (m.axis === "ns") {
          const dir = Math.sign(m.oy1 - m.y0) || 1;
          let y1 = m.oy1;
          const hit = _rulerRaycastWall(m.x0, m.y0, 0, dir);
          if (hit && Math.abs(hit.y - m.y0) <= Math.abs(m.oy1 - m.y0) + 0.5) {
            y1 = hit.y;
          }
          m.y1 = y1;
          m.x1 = m.x0;
        } else {
          m.x1 = m.ox1;
          m.y1 = m.oy1;
        }
      }
    } else if (state.ruler.hoverIdx >= 0) {
      const m = state.ruler.measures[state.ruler.hoverIdx];
      const idx = cycle.indexOf(m.axis);
      m.axis = cycle[(idx + 1) % cycle.length];
      if (m.axis === "ew") {
        const dir = Math.sign(m.ox1 - m.x0) || 1;
        let x1 = m.ox1;
        const hit = _rulerRaycastWall(m.x0, m.y0, dir, 0);
        if (hit && Math.abs(hit.x - m.x0) <= Math.abs(m.ox1 - m.x0) + 0.5) {
          x1 = hit.x;
        }
        m.x1 = x1;
        m.y1 = m.y0;
      } else if (m.axis === "ns") {
        const dir = Math.sign(m.oy1 - m.y0) || 1;
        let y1 = m.oy1;
        const hit = _rulerRaycastWall(m.x0, m.y0, 0, dir);
        if (hit && Math.abs(hit.y - m.y0) <= Math.abs(m.oy1 - m.y0) + 0.5) {
          y1 = hit.y;
        }
        m.y1 = y1;
        m.x1 = m.x0;
      } else {
        m.x1 = m.ox1;
        m.y1 = m.oy1;
      }
    }
    e.preventDefault();
    e.stopPropagation();
    draw();
    return;
  }

  if ((e.key === "d" || e.key === "D") &&
      e.shiftKey && !e.ctrlKey && !e.metaKey && !e.altKey) {
    e.preventDefault();
    e.stopPropagation();
    _rulerOpenNumericInput("dist");
    return;
  }

  if ((e.key === "a" || e.key === "A") &&
      e.shiftKey && !e.ctrlKey && !e.metaKey && !e.altKey) {
    e.preventDefault();
    e.stopPropagation();
    _rulerOpenNumericInput("angle");
    return;
  }

  if (e.key === "Backspace") {
    /* Delete the SELECTED measurement if there is one; otherwise
       fall back to popping the last measurement.  Matches the
       panel's Delete measurement button. */
    if (state.ruler.selected >= 0 &&
        state.ruler.selected < state.ruler.measures.length) {
      state.ruler.measures.splice(state.ruler.selected, 1);
      state.ruler.selected = -1;
      state.ruler.hoverIdx = -1;
    } else if (state.ruler.measures.length > 0 && !state.ruler.pending) {
      state.ruler.measures.pop();
    }
    e.preventDefault();
    e.stopPropagation();
    draw();
    return;
  }
}, { capture: true });
"""
