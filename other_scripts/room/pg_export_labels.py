"""
pg_export_labels.py — vertex label placement and draw.

The one pass that decides where every vertex label pill sits and then
draws it.  Placement runs once per export, per cable; draw runs once.
The placement half is now async — see "Async boundary" below.

Placement
---------
    Phase 1   Row count — a property of the pill set: the minimum
              number of rows the pills' total width requires.
    Phase 2   Interleaved assignment — pill i goes to row (i % n).
    Phase 3   Compress at COMPRESS_GAP, center in the strip.

Then the pass hands the geometry to the Rust leader optimiser via
pg_export_rust's `__optimizeLeadersOnServer`, which runs the three
passes that used to live here — refineLeaderOffsets,
polishLeaderLayout, and hugOverhangingLeaders — as a subroutine of
the Python server.  Finally, the style assignment is computed using
pg_export_style's conflict graph.

Draw
----
The pill rectangles, the text lines, and the leader polylines (with
their per-pill dash style from the vertex cover).

The vertex cover that turns the style graph into a solid/dashed
assignment lives here, inline, because it produces the last field
the leader placement ever writes (it.leaderStyle) and because the
pills and the leaders are drawn in the same pass.

Async boundary
--------------
computeVertexLabelPlacement is async: the middle third of its body —
the three leader-geometry passes — is a round trip to the Python
server, which in turn spawns the compiled Rust binary.  Every caller
must await the result.

Callers, and the async propagation each one needs:

    renderCableRunToCanvas   in pg_export_render.py — awaits the
                             placement and is therefore itself async
    exportAllCableRuns       in pg_export_entry.py — awaits each
                             renderCableRunToCanvas in its loop
    rerenderAll              in pg_export_preview.py — awaits each
                             renderCableRunToCanvas; its two
                             checkbox handlers await rerenderAll

The JS-side fallback when the Rust binary is unavailable runs the
same three passes inline (see pg_export_rust.py), and that path is
synchronous — the async wrapper exists for the server round trip and
costs nothing when the fallback is what runs.

Handles for the in-page fallback
--------------------------------
The three fallback functions are still defined in the modules that
originally owned them — refineLeaderOffsets in pg_export_primitives,
polishLeaderLayout and hugOverhangingLeaders in pg_export_pillpush —
and their JS is unchanged.  They are the load-bearing path only when
the server has no `leader_optimizer` binary at the path
cable_server.RUST_BIN resolves to; on a server that does, they are
dead code kept for the graceful-degradation case.
"""


LABELS_JS = r"""
/* ==========================================================================
   ANGLE-DISPLAY TOGGLE
   ==========================================================================
   Flipped by the "Show cable angles" checkbox on the preview page (see
   pg_export_preview.py).  When true, computeVertexLabelPlacement adds
   one line per cable edge meeting at a vertex pill, showing that
   edge's slope angle from horizontal. */

window.showCableAngles = false;

/* ==========================================================================
   VERTEX-LABEL TYPOGRAPHY
   ==========================================================================
   One knob: VLABEL_FONT_SIZE.  Every dimension the pill draws — its
   own inner padding, its line pitch, and the box that surrounds the
   name — is derived from that one number (plus the ratios below), so
   bumping the size here reflows every pill in the drawing without
   touching any of the placement or drawing math.

   Changing the FONT FAMILY alone is also covered: the name box's
   WIDTH is measured from the actual glyphs at draw time, so a wider
   or narrower face shifts the box with the text. */

const VLABEL_FONT_SIZE   = 12;
const VLABEL_FONT_WEIGHT = 700;

const VLABEL_PADX_RATIO  = 0.65;   // pill inner padding, horizontal
const VLABEL_PADY_RATIO  = 0.35;   // pill inner padding, vertical
const VLABEL_LINE_RATIO  = 1.35;   // line pitch  (LINE_H  / FONT_SIZE)

const VLABEL_BOXPAD_RATIO = 0.30;  // name-box padding around the text
const VLABEL_BOXH_RATIO   = 1.15;  // name-box height / FONT_SIZE
const VLABEL_BOX_TOPAIR_RATIO = 0.22;  // extra top-side air inside the name box

/* ==========================================================================
   OVERHANG-HUGGING FINAL PASS
   ==========================================================================

   The optimiser's offsetA, detourBias, bevel, and jog moves can push a
   leader's intermediate geometry past the strip band's horizontal
   edges — offsetA alone reaches ±240 px, and a jog applied on top of an
   already-out-of-band joint can put a vertical rule hundreds of pixels
   outside [stripAreaX0, stripAreaX1].  The canvas clips the overflow
   silently, so the exported PNG shows the leader truncated on the
   right.

   This pass runs AFTER the polish loop and leaves the optimiser's
   decisions alone everywhere they fit.  Only the offending leaders are
   reshaped.  The endpoints are never touched: pillX is clamped to the
   band by the pill-placing code above, and anchorCx is inside the
   strip.  What is clamped is the interior geometry.

   Leaders that overhang the same edge are given different "hugging
   depths" — lane 0 hugs HUG_MARGIN px from the edge, lane 1 hugs
   HUG_MARGIN + HUG_LANE_STEP, and so on — so two hugged leaders cannot
   share a vertical run.  Deepest overhang first, so the leader that
   lost the most from the clamp ends up in the lane closest to the
   edge.

   Each candidate lane is checked against every foreign pill's expanded
   AABB.  A lane that would put the clamped path through a foreign pill
   is rejected and the leader advances to the next lane.  After
   HUG_MAX_LANES tries the leader is left at the last lane — a
   pathological case that cannot fit is at least inside the band. */

const HUG_MARGIN    = 6;
const HUG_LANE_STEP = 5;
const HUG_MAX_LANES = 20;

function hugOverhangingLeaders(placed, stripAreaX0, stripAreaX1,
                               stripH, topPad, trackOffsets) {
  if (!placed.length) return;

  const loBase = stripAreaX0 + HUG_MARGIN;
  const hiBase = stripAreaX1 - HUG_MARGIN;

  /* 1. Compute every leader's base path, classify overhang. */
  const work = [];
  for (const it of placed) {
    it.finalPath = null;

    const chanY    = stripH + (it.channelYRel || 0);
    const pillTopY = stripH + topPad + trackOffsets[it.track];
    const base = computeLeaderPath(
      it.anchorCx, it.anchorCyRel, it.offsetA || 0, it.offsetP || 0,
      chanY, it.pillCenterX, pillTopY,
      it, placed, stripH, topPad, trackOffsets, it.diveMode || 0);
    const bevelled = _bevelPath(base, it.bevel0 || 0, it.bevel1 || 0);
    const path = _applyJogsToPath(bevelled, it);

    let minX = Infinity, maxX = -Infinity;
    for (const p of path) {
      if (p[0] < minX) minX = p[0];
      if (p[0] > maxX) maxX = p[0];
    }

    const overLeft  = loBase - minX;
    const overRight = maxX - hiBase;
    if (overLeft <= 0 && overRight <= 0) continue;

    work.push({
      it,
      path,
      side: (overLeft >= overRight) ? "L" : "R",
      depth: Math.max(overLeft, overRight, 0),
    });
  }
  if (!work.length) return;

  /* 2. Deepest overhang first; the deepest one gets lane 0. */
  work.sort((a, b) => b.depth - a.depth);

  const laneCount = { L: 0, R: 0 };

  for (const job of work) {
    const { it, path, side } = job;
    let assigned = null;

    /* 3. Try each lane from the first unoccupied one onward. */
    for (let lane = laneCount[side]; lane < HUG_MAX_LANES; lane++) {
      const inset  = HUG_MARGIN + lane * HUG_LANE_STEP;
      const loLane = stripAreaX0 + inset;
      const hiLane = stripAreaX1 - inset;

      /* Clamp every intermediate point to the lane; leave the two
         endpoints where they are — they anchor to the pill and the
         strip and must not move. */
      const candidate = path.map((p, i) =>
        (i === 0 || i === path.length - 1)
          ? [p[0], p[1]]
          : [Math.max(loLane, Math.min(hiLane, p[0])), p[1]]);

      /* 4. Reject the lane if the clamped path would run through a
         foreign pill's expanded AABB.  Same PILL_PROX test the
         optimiser's own collision checks use, so the hugging pass
         respects the same no-overlap invariant. */
      const segs = _pathSegments(candidate, -1, 0.5);
      let clear = true;
      for (let j = 0; j < placed.length && clear; j++) {
        const other = placed[j];
        if (other === it) continue;
        const qT = stripH + topPad + trackOffsets[other.track];
        const box = {
          qL: other.pillCenterX - other.w / 2 - PILL_PROX,
          qR: other.pillCenterX + other.w / 2 + PILL_PROX,
          qT: qT - PILL_PROX,
          qB: qT + other.h + PILL_PROX,
        };
        for (const s of segs) {
          if (s.maxX < box.qL || s.minX > box.qR) continue;
          if (s.maxY < box.qT || s.minY > box.qB) continue;
          if (_segBoxOverlap(s, box) > 0) { clear = false; break; }
        }
      }

      if (clear) { assigned = { lane, path: candidate }; break; }
    }

    /* 5. Fallback: last lane, accept whatever overlaps it has.  The
       pathological leader is at least inside the band, which is the
       whole point of the pass. */
    if (!assigned) {
      const inset  = HUG_MARGIN + (HUG_MAX_LANES - 1) * HUG_LANE_STEP;
      const loLane = stripAreaX0 + inset;
      const hiLane = stripAreaX1 - inset;
      const candidate = path.map((p, i) =>
        (i === 0 || i === path.length - 1)
          ? [p[0], p[1]]
          : [Math.max(loLane, Math.min(hiLane, p[0])), p[1]]);
      assigned = { lane: HUG_MAX_LANES - 1, path: candidate };
    }

    it.finalPath = assigned.path;
    laneCount[side] = Math.max(laneCount[side], assigned.lane + 1);
  }
}

/* ---- Vertex coordinate labels ---- */

async function computeVertexLabelPlacement(c, chunks, stripOffsetX,
                                           stripAreaX0, stripAreaW, stripH,
                                           orderedIds) {
  const fmtCm = (mm) => String(Math.round(mm / 10));
  const FONT  = VLABEL_FONT_WEIGHT + " " + VLABEL_FONT_SIZE + "px " + FONT_MONO;
  const PAD_X = Math.round(VLABEL_FONT_SIZE * VLABEL_PADX_RATIO);
  const PAD_Y = Math.round(VLABEL_FONT_SIZE * VLABEL_PADY_RATIO);
  const LINE_H = Math.round(VLABEL_FONT_SIZE * VLABEL_LINE_RATIO);
  const TRACK_GAP_X   = 10;
  const TRACK_V_GAP   = 8;
  const GROUP_TOL     = 8;
  const MAX_TRACKS    = 20;
  const AREA_X0 = stripAreaX0;
  const AREA_X1 = stripAreaX0 + stripAreaW;

  const COMPRESS_GAP = 20;

  const boundaries = [];
  const seenBx = new Set();
  for (const ch of chunks) {
    const cands = [stripOffsetX + ch.x0, stripOffsetX + ch.x1];
    for (const bx of cands) {
      const key = Math.round(bx * 2) / 2;
      if (seenBx.has(key)) continue;
      seenBx.add(key);
      boundaries.push(bx);
    }
  }

  const wireSegments = [];
  {
    const byRun = new Map();
    for (const ch of chunks) {
      let list = byRun.get(ch.runIdx);
      if (!list) { list = []; byRun.set(ch.runIdx, list); }
      list.push(ch);
    }
    for (const [, chList] of byRun) {
      const flat = [];
      for (const ch of chList) {
        for (const a of ch.anchors) flat.push(a);
      }
      for (let i = 0; i < flat.length - 1; i++) {
        const ax = stripOffsetX + flat[i].xPx;
        const ay = flat[i].yPx;
        const bx = stripOffsetX + flat[i + 1].xPx;
        const by = flat[i + 1].yPx;
        wireSegments.push({
          ax, ay, bx, by,
          angle: Math.atan2(by - ay, bx - ax),
        });
      }
    }
  }

  const wallEdges = [];
  {
    let stripX0 = Infinity, stripX1 = -Infinity;
    for (const ch of chunks) {
      const x0 = stripOffsetX + ch.x0;
      const x1 = stripOffsetX + ch.x1;
      const yTop = (1 - ch.z_hi / WALL_HEIGHT) * stripH;
      const yBot = (1 - ch.z_lo / WALL_HEIGHT) * stripH;
      wallEdges.push({ y: yTop, x0, x1 });
      wallEdges.push({ y: yBot, x0, x1 });
      if (x0 < stripX0) stripX0 = x0;
      if (x1 > stripX1) stripX1 = x1;
    }
    if (isFinite(stripX0) && isFinite(stripX1)) {
      wallEdges.push({ y: 0,      x0: stripX0, x1: stripX1 });
      wallEdges.push({ y: stripH, x0: stripX0, x1: stripX1 });
    }
  }

  const orderIdx = new Map();
  if (Array.isArray(orderedIds)) {
    orderedIds.forEach((id, i) => orderIdx.set(id, i));
  }

  const rawAnchors = [];
  for (const ch of chunks) {
    const chunkMid = stripOffsetX + (ch.x0 + ch.x1) / 2;
    for (const a of ch.anchors) {
      const anc = anchors.get(a.aid);
      if (!anc || anc.space !== "wall-edge") continue;
      rawAnchors.push({
        anchorCx: stripOffsetX + a.xPx,
        anchorCyRel: a.yPx,
        h: anc.v || 0,
        nativeU: a.nativeU,
        len: ch.len,
        mirror: ch.mirror,
        segIdx: ch.segIdx,
        chunkMid,
        aid: a.aid,
      });
    }
  }

  const clusters = [];
  const used = new Array(rawAnchors.length).fill(false);
  for (let i = 0; i < rawAnchors.length; i++) {
    if (used[i]) continue;
    const A = rawAnchors[i];
    const grp = [A];
    used[i] = true;
    for (let j = i + 1; j < rawAnchors.length; j++) {
      if (used[j]) continue;
      const B = rawAnchors[j];
      if (Math.abs(A.anchorCx - B.anchorCx) < GROUP_TOL &&
          Math.abs(A.anchorCyRel - B.anchorCyRel) < GROUP_TOL) {
        grp.push(B);
        used[j] = true;
      }
    }
    clusters.push(grp);
  }

  const sideLineFor = (m) => {
    const wDist = m.mirror ? (m.len - m.nativeU) : m.nativeU;
    const eDist = m.mirror ? m.nativeU : (m.len - m.nativeU);
    return "W " + fmtCm(wDist) + " . E " + fmtCm(eDist);
  };

  const items = [];
  c.save();
  c.font = FONT;

  /* Pre-compute the pill name for every member anchor, so the angle
     lines below can name the neighbour pill each angle points at.
     A pill's name is "V" + (its minimum ordered index + 1) — the same
     rule the main loop below uses — so this map stays consistent with
     the names drawn on the pills themselves. */
  const anchorPillName = new Map();
  for (const grp of clusters) {
    let minIdx = Infinity;
    for (const m of grp) {
      const oi = orderIdx.get(m.aid);
      if (typeof oi === "number" && oi < minIdx) minIdx = oi;
    }
    if (minIdx === Infinity) continue;
    const pn = "V" + (minIdx + 1);
    for (const m of grp) anchorPillName.set(m.aid, pn);
  }

  for (const grp of clusters) {
    let minIdx = Infinity;
    for (const m of grp) {
      const oi = orderIdx.get(m.aid);
      if (typeof oi === "number" && oi < minIdx) minIdx = oi;
    }
    const name = (minIdx !== Infinity) ? ("V" + (minIdx + 1)) : null;

    const lines = [];
    if (grp.length === 1) {
      const m = grp[0];
      lines.push({ left:  name || "", right: "h " + fmtCm(m.h),
                   isName: !!name });
      lines.push({ left:  sideLineFor(m), right: null });
    } else {
      grp.sort((a, b) => {
        const sa = a.chunkMid < a.anchorCx ? 0 : 1;
        const sb = b.chunkMid < b.anchorCx ? 0 : 1;
        return sa - sb;
      });
      for (let gi = 0; gi < grp.length; gi++) {
        const m = grp[gi];
        lines.push({
          left:  (gi === 0 && name) ? name : "",
          right: "h " + fmtCm(m.h),
          isName: (gi === 0 && !!name),
        });
        lines.push({ left: sideLineFor(m), right: null });
      }
    }

    /* ---- optional angle lines --------------------------------
       When the preview page's "Show cable angles" checkbox is on,
       every cable edge that touches this cluster contributes one
       extra line to the pill: the bearing of that edge in the
       strip's own coordinate frame, measured counter-clockwise
       from the positive-u axis.

       The scale is zero at the right, +180 and −180 at the left.
       The two extremes describe the same ray; the sign is what
       distinguishes the direction of rotation to reach it:

           0°   due-east in the strip render   (increasing u)
         +90°   straight up                    (increasing height)
        +180°   due-west, reached from above
         −90°   straight down                  (decreasing height)
        −180°   due-west, reached from below

       The strip frame is the visual frame of the exported drawing,
       so the numbers match what a reader sees on the page.

       Only wall-edge neighbours contribute an angle.  A floor
       neighbour would be off-strip and has no visible direction
       in this view, so it is skipped.  Identical integers collapse
       to a single line, so a straight run through a mid-cable
       vertex shows one angle rather than two. */
    if (window.showCableAngles && Array.isArray(orderedIds)) {
      const memberSet = new Set(grp.map(m => m.aid));
      const seenAngles = new Set();
      for (const m of grp) {
        const idx = orderIdx.get(m.aid);
        if (typeof idx !== "number") continue;
        const anchorA = anchors.get(m.aid);
        if (!anchorA || anchorA.space !== "wall-edge") continue;
        for (const nIdx of [idx - 1, idx + 1]) {
          if (nIdx < 0 || nIdx >= orderedIds.length) continue;
          const nId = orderedIds[nIdx];
          if (memberSet.has(nId)) continue;
          const anchorB = anchors.get(nId);
          if (!anchorB || anchorB.space !== "wall-edge") continue;

          /* Strip-frame deltas: u is the unrolled perimeter
             coordinate, v is the anchor's height.  Same (u, v)
             frame the strip is drawn in, so the resulting angle
             matches the visual render. */
          const uA = wallAttachToU(anchorA.segIdx, anchorA.t);
          const vA = anchorA.v || 0;
          const uB = wallAttachToU(anchorB.segIdx, anchorB.t);
          const vB = anchorB.v || 0;
          const du = uB - uA;
          const dv = vB - vA;
          if (Math.hypot(du, dv) < 1e-3) continue;

          /* atan2 already gives a counter-clockwise signed angle
             from the +u axis in (−180, 180], which is exactly the
             convention the pill shows: 0 at the right, positive
             for rotations into the upper half, negative for the
             lower half, and ±180 meeting at the left. */
          const ang = Math.atan2(dv, du) * 180 / Math.PI;
          const rounded = Math.round(ang);
          if (seenAngles.has(rounded)) continue;
          seenAngles.add(rounded);

          let text;
          if (rounded === 0) {
            text = "0\u00B0";
          } else if (rounded > 0) {
            text = "+" + rounded + "\u00B0";
          } else {
            text = rounded + "\u00B0";
          }
          /* The ∠ line names the pill the angle points AT — the
             neighbour this vertex's cable segment connects to, which
             is the pill whose anchor is nId.  If the neighbour is on
             a pill that the collinear filter removed, we still show
             the angle but drop the name rather than show a label that
             does not correspond to any drawn pill. */
          const neighbourPillName = anchorPillName.get(nId);
          const leftText = neighbourPillName
            ? "\u2220 " + neighbourPillName
            : "\u2220";
          lines.push({ left: leftText, right: text });
        }
      }
    }

    let w = 0;
    for (const l of lines) {
      const lw = l.left  ? c.measureText(l.left).width  : 0;
      const rw = l.right ? c.measureText(l.right).width : 0;
      const gap = (l.left && l.right) ? LABEL_LEFT_RIGHT_GAP : 0;
      if (lw + gap + rw > w) w = lw + gap + rw;
    }

    items.push({
      anchorCx: grp[0].anchorCx,
      anchorCyRel: grp[0].anchorCyRel,
      lines,
      w: w + PAD_X * 2,
      h: lines.length * LINE_H + PAD_Y * 2,
      name,
      members: grp.map(x => x.aid),
      fontSize: VLABEL_FONT_SIZE,
      offsetP: 0,
    });
  }
  c.restore();

  if (window.filterCollinearVerticesInStrip && Array.isArray(orderedIds)) {
    const oi = new Map();
    orderedIds.forEach((id, i) => oi.set(id, i));

    const anchorMm = new Map();
    const segOfAid = new Map();
    {
      const byRun = new Map();
      for (const ch of chunks) {
        let list = byRun.get(ch.runIdx);
        if (!list) { list = []; byRun.set(ch.runIdx, list); }
        list.push(ch);
      }
      for (const [, chs] of byRun) {
        let cumU = 0;
        for (const ch of chs) {
          for (const a of ch.anchors) {
            const anc = anchors.get(a.aid);
            if (!anc || anc.space !== "wall-edge") continue;
            const uInChunk = ch.mirror
              ? (1 - anc.t) * ch.len
              : anc.t * ch.len;
            anchorMm.set(a.aid, { u: cumU + uInChunk, v: anc.v || 0 });
            segOfAid.set(a.aid, ch.segIdx);
          }
          cumU += ch.len;
        }
      }
    }

    const TOL_MM        = 2.0;
    const LEN_RATIO_MAX = 3.0;
    const kept = [];

    for (const it of items) {
      const idxs = it.members
        .map(id => oi.get(id))
        .filter(v => typeof v === "number")
        .sort((a, b) => a - b);

      if (idxs.length === 0) { kept.push(it); continue; }

      const lo = idxs[0];
      const hi = idxs[idxs.length - 1];

      if (hi - lo + 1 !== idxs.length) { kept.push(it); continue; }
      if (lo === 0 || hi === orderedIds.length - 1) {
        kept.push(it); continue;
      }

      const bP = anchorMm.get(orderedIds[lo - 1]);
      const aP = anchorMm.get(orderedIds[hi + 1]);
      const cP = anchorMm.get(it.members[0]);
      if (!bP || !aP || !cP) { kept.push(it); continue; }

      const CORNER_COS_MIN = 0.985;
      const segs = new Set();
      for (const id of it.members) {
        const s = segOfAid.get(id);
        if (s != null) segs.add(s);
      }
      if (segs.size >= 2) {
        const segArr = Array.from(segs);
        const lens = segArr
          .map(si => (WALL.segments[si] && WALL.segments[si].len) || 0)
          .filter(l => l > 0);

        let realCorner = false;
        if (segArr.length >= 2) {
          const s0 = WALL.segments[segArr[0]];
          if (s0) {
            const d0x = s0.b[0] - s0.a[0], d0y = s0.b[1] - s0.a[1];
            const l0 = Math.hypot(d0x, d0y) || 1;
            for (let i = 1; i < segArr.length; i++) {
              const si = WALL.segments[segArr[i]];
              if (!si) continue;
              const dix = si.b[0] - si.a[0], diy = si.b[1] - si.a[1];
              const li = Math.hypot(dix, diy) || 1;
              const cos = Math.abs((d0x * dix + d0y * diy) / (l0 * li));
              if (cos < CORNER_COS_MIN) { realCorner = true; break; }
            }
          }
        }

        if (realCorner && lens.length >= 2) {
          const ratio = Math.max(...lens) / Math.min(...lens);
          if (ratio > LEN_RATIO_MAX) { kept.push(it); continue; }
        }
      }

      const dx1 = cP.u - bP.u, dy1 = cP.v - bP.v;
      const dx2 = aP.u - cP.u, dy2 = aP.v - cP.v;
      const len2 = Math.hypot(dx2, dy2);
      if (len2 < 0.5) { kept.push(it); continue; }

      const dist = Math.abs(dx1 * dy2 - dy1 * dx2) / len2;
      if (dist >= TOL_MM) kept.push(it);
    }

    items.length = 0;
    for (const it of kept) items.push(it);
  }

  const pillNameOf = new Map();
  for (const it of items) {
    if (it.name && it.members) {
      for (const aid of it.members) pillNameOf.set(aid, it.name);
    }
  }

  if (!items.length) {
    return {
      placed: [], maxTrack: -1, topPad: 0,
      trackOffsets: [], trackHeights: [],
      PAD_X, PAD_Y, LINE_H,
      fontSize: VLABEL_FONT_SIZE,
      pillNameOf,
      areaX0: AREA_X0,
      areaX1: AREA_X1,
    };
  }

  items.sort((a, b) => a.anchorCx - b.anchorCx);

  const tracks = [];

  const usableWidth = AREA_X1 - AREA_X0;
  const totalPillW  = items.reduce((a, it) => a + it.w, 0);
  let numRows = 1;
  while (numRows < MAX_TRACKS) {
    const needed = totalPillW + (items.length - numRows) * TRACK_GAP_X;
    if (needed <= numRows * usableWidth) break;
    numRows++;
  }

  const rows = [];
  for (let i = 0; i < numRows; i++) rows.push([]);
  for (let i = 0; i < items.length; i++) {
    rows[i % numRows].push(items[i]);
  }

  for (let t = 0; t < rows.length; t++) {
    const row = rows[t];
    if (row.length === 0) continue;

    const k = row.length;
    const sumW = row.reduce((a, it) => a + it.w, 0);
    const rowWidth = sumW + (k - 1) * COMPRESS_GAP;
    const edgePad = Math.max(0, (usableWidth - rowWidth) / 2);

    let cur = AREA_X0 + edgePad + row[0].w / 2;
    row[0].pillCenterX = cur;
    for (let i = 1; i < k; i++) {
      cur += row[i - 1].w / 2 + COMPRESS_GAP + row[i].w / 2;
      row[i].pillCenterX = cur;
    }

    const rowRight = row[k - 1].pillCenterX + row[k - 1].w / 2;
    if (rowRight > AREA_X1) {
      const dx = rowRight - AREA_X1;
      for (const it of row) it.pillCenterX -= dx;
    }

    /* Clamp each pill so both of its edges stay inside the label
       band.  Without this a pill anchored near the far right of
       the strip can extend past the canvas edge — the canvas
       clips it silently and the exported PNG shows the strip
       truncated on the right.  That is exactly the symptom that
       appeared once the fonts got wider and every pill grew.  A
       pill wider than the whole band is centred on the band and
       accepted as-is; that case cannot be fixed by translation
       and would need a wider IMG_W. */
    for (const it of row) {
      const halfW = it.w / 2;
      const lo = AREA_X0 + halfW;
      const hi = AREA_X1 - halfW;
      if (lo <= hi) {
        it.pillCenterX = Math.max(lo, Math.min(hi, it.pillCenterX));
      } else {
        it.pillCenterX = (AREA_X0 + AREA_X1) / 2;
      }
    }

    for (const it of row) {
      it.track     = t;
      it._baseDisp = Math.abs(it.pillCenterX - it.anchorCx);
    }
    tracks.push(row);
  }

  const trackHeights = new Array(tracks.length).fill(0);
  for (let t = 0; t < tracks.length; t++) {
    let maxH = 0;
    for (const it of tracks[t]) maxH = Math.max(maxH, it.h);
    trackHeights[t] = maxH + TRACK_V_GAP;
  }
  const trackOffsets = new Array(tracks.length).fill(0);
  let accOff = 0;
  for (let t = 0; t < tracks.length; t++) {
    trackOffsets[t] = accOff;
    accOff += trackHeights[t];
  }

  const placed = [];
  for (const arr of tracks) for (const it of arr) {
    if (it.track != null) placed.push(it);
  }

  const leaders = placed.map(it => ({
    it,
    anchorX: it.anchorCx,
    pillX:   it.pillCenterX,
    x0: Math.min(it.anchorCx, it.pillCenterX),
    x1: Math.max(it.anchorCx, it.pillCenterX),
  }));
  const sortedBySpan =
    [...leaders].sort((a, b) => (b.x1 - b.x0) - (a.x1 - a.x0));
  const slots = [];
  for (const L of sortedBySpan) {
    let assigned = null;
    for (const slot of slots) {
      let overlap = false;
      for (const [l, r] of slot.spans) {
        if (L.x0 < r + 4 && L.x1 > l - 4) { overlap = true; break; }
      }
      if (!overlap) {
        slot.spans.push([L.x0, L.x1]);
        assigned = slot;
        break;
      }
    }
    if (!assigned) {
      const relY = LABEL_CHANNEL_Y0 + slots.length * LABEL_CHANNEL_STEP;
      assigned = { relY, spans: [[L.x0, L.x1]] };
      slots.push(assigned);
    }
    L.it.channelYRel = assigned.relY;
  }
  const numChannels = slots.length;

  const topPad = numChannels === 0
    ? 0
    : LABEL_CHANNEL_Y0 + (numChannels - 1) * LABEL_CHANNEL_STEP
      + LABEL_CHANNEL_GAP;

  /* The three leader-geometry passes that used to run inline here —
     refineLeaderOffsets, polishLeaderLayout, and
     hugOverhangingLeaders — have been ported to Rust and are now a
     single subroutine call to the Python server.  The bridge posts
     the current `placed` state plus its obstacle context to
     /optimize-leaders, waits for the optimised fields back, and
     splices them into `placed` in place.

     The JS-side versions of all three still exist (in
     pg_export_primitives, pg_export_pillpush, and this module
     itself) and are invoked by the bridge's fallback path when the
     Rust binary is unavailable — so the export still completes,
     just more slowly.

     This is the async boundary of the whole exporter: every caller
     of computeVertexLabelPlacement must await the result, which
     means every caller of renderCableRunToCanvas must await too.
     See the renderCableRunToCanvas module docstring and the two
     call sites in pg_export_preview and pg_export_entry. */
  await window.__optimizeLeadersOnServer(placed, {
    stripH, topPad, trackOffsets, boundaries, wireSegments, wallEdges,
    stripAreaX0, stripAreaW,
  });

  const conflictAdj = _buildStyleConflictGraph(placed, stripH, topPad,
                                                trackOffsets);

  const styleMap = new Map();
  const orderedForStyle =
    [...placed].sort((a, b) => a.anchorCx - b.anchorCx);

  const seen = new Set();
  for (const seed of orderedForStyle) {
    if (seen.has(seed)) continue;

    const comp = [];
    const queue = [seed];
    seen.add(seed);
    while (queue.length) {
      const u = queue.shift();
      comp.push(u);
      for (const v of conflictAdj.get(u) || []) {
        if (!seen.has(v)) { seen.add(v); queue.push(v); }
      }
    }

    if (comp.length === 1) {
      styleMap.set(comp[0], 0);
      continue;
    }

    let bipartite = true;
    const color = new Map([[comp[0], 0]]);
    const q2 = [comp[0]];
    while (q2.length && bipartite) {
      const u = q2.shift();
      const cu = color.get(u);
      for (const v of conflictAdj.get(u) || []) {
        if (!color.has(v)) { color.set(v, 1 - cu); q2.push(v); }
        else if (color.get(v) === cu) { bipartite = false; break; }
      }
    }

    if (bipartite) {
      let n0 = 0, n1 = 0;
      for (const u of comp) (color.get(u) === 0 ? n0++ : n1++);
      const solidColor = (n0 >= n1) ? 0 : 1;
      for (const u of comp) {
        styleMap.set(u, color.get(u) === solidColor ? 0 : 1);
      }
      continue;
    }

    const dashed = new Set();
    {
      const removed = new Set();
      while (true) {
        let best = null, bestDeg = 0;
        for (const u of comp) {
          if (removed.has(u)) continue;
          let d = 0;
          for (const v of conflictAdj.get(u) || [])
            if (!removed.has(v)) d++;
          if (d > bestDeg) { bestDeg = d; best = u; }
        }
        if (best === null || bestDeg === 0) break;
        dashed.add(best);
        removed.add(best);
      }
    }

    for (let outer = 0; outer < 30; outer++) {
      let changed = false;

      for (const v of Array.from(dashed)) {
        let hasSolid = false;
        for (const u of conflictAdj.get(v) || []) {
          if (!dashed.has(u)) { hasSolid = true; break; }
        }
        if (!hasSolid) { dashed.delete(v); changed = true; }
      }

      for (const v of Array.from(dashed)) {
        let ok = true;
        for (const u of conflictAdj.get(v) || []) {
          if (dashed.has(u)) continue;
          let stillCovered = false;
          for (const w of conflictAdj.get(u) || []) {
            if (w !== v && dashed.has(w)) { stillCovered = true; break; }
          }
          if (!stillCovered) { ok = false; break; }
        }
        if (ok) { dashed.delete(v); changed = true; }
      }

      if (!changed) break;
    }

    for (const u of comp) styleMap.set(u, dashed.has(u) ? 1 : 0);
  }

  for (const it of placed) it.leaderStyle = styleMap.get(it) ?? 0;

  return {
    placed,
    maxTrack: tracks.length - 1,
    topPad,
    trackOffsets,
    trackHeights,
    PAD_X, PAD_Y, LINE_H,
    fontSize: VLABEL_FONT_SIZE,
    pillNameOf,
    areaX0: AREA_X0,
    areaX1: AREA_X1,
  };
}

/* ---- Vertex labels ---- */

function drawVertexLabels(c, placement, stripY, stripH) {
  if (!placement.placed.length) return;
  const { placed, PAD_X, PAD_Y, LINE_H, topPad, trackOffsets,
          fontSize } = placement;
  const stripBottom = stripY + stripH;

  const pillTopYFor = (it) =>
    stripBottom + topPad + trackOffsets[it.track];

  /* Same font the placement pass measured against.  Without this the
     text is drawn in whatever font the caller last set — usually the
     subtitle's 13 px / 600-weight stack — and a right-aligned label
     overflows the pill's right edge by a few pixels. */
  c.font = VLABEL_FONT_WEIGHT + " " + fontSize + "px " + FONT_MONO;

  /* Name-box geometry, all scaled from the font size.  Both the
     horizontal padding and the box height move with fontSize, so a
     bump to VLABEL_FONT_SIZE grows the box, the pill, and the line
     pitch together instead of leaving the box stranded at a fixed
     pixel size. */
  const namePadX    = Math.max(2, fontSize * VLABEL_BOXPAD_RATIO);
  const nameBoxH    = fontSize * VLABEL_BOXH_RATIO;
  const nameStrokeW = Math.max(0.9, fontSize / 12);
  const nameTopAir  = fontSize * VLABEL_BOX_TOPAIR_RATIO;

  for (const it of placed) {
    const pillTopY = pillTopYFor(it);
    const rx = it.pillCenterX - it.w / 2;
    const ry = pillTopY;

    c.fillStyle = "#ffffff";
    c.fillRect(rx, ry, it.w, it.h);
    c.strokeStyle = "#000000";
    c.lineWidth = 1.2;
    c.strokeRect(rx + 0.5, ry + 0.5, it.w - 1, it.h - 1);

    c.fillStyle = "#000000";
    c.textBaseline = "middle";
    let ty = ry + PAD_Y + LINE_H / 2;
    for (const line of it.lines) {
      if (line.left) {
        c.textAlign = "left";
        c.fillText(line.left, rx + PAD_X, ty);

        /* Box around the pill name.  Width is measured from the
           actual glyphs at the current font, so a wider face widens
           the box; height and stroke scale from fontSize.  The box
           is centred on the line's text baseline (`ty`), which keeps
           it symmetric around the text at any size. */
        if (line.isName) {
          const lw   = c.measureText(line.left).width;
          const boxW = lw + 2 * namePadX;
          const boxX = rx + PAD_X - namePadX;

          /* Top edge is pushed up by nameTopAir and the height grows
             to match, so the bottom edge — and therefore the text
             position — does not move.  This balances the two gaps
             around the caps: monospace faces carry descender space
             below the baseline that makes a symmetric box read as
             tight at the top and loose at the bottom. */
          const boxY = ty - nameBoxH / 2 - nameTopAir;
          const boxH = nameBoxH + nameTopAir;

          c.strokeStyle = "#000000";
          c.lineWidth   = nameStrokeW;
          c.strokeRect(boxX + 0.5, boxY + 0.5, boxW - 1, boxH - 1);
        }
      }
      if (line.right) {
        c.textAlign = "right";
        c.fillText(line.right, rx + it.w - PAD_X, ty);
      }
      ty += LINE_H;
    }
  }

  c.save();
  c.strokeStyle = "#000000";
  c.lineWidth = 1.4;
  c.lineJoin = "round";
  c.lineCap = "butt";
  for (const it of placed) {
    if (it.channelYRel == null) continue;
    const anchorX  = it.anchorCx;
    const anchorY  = stripY + it.anchorCyRel;
    const pillX    = it.pillCenterX;
    const chanY    = stripBottom + it.channelYRel;
    const pillTopY = pillTopYFor(it);
    const offA     = it.offsetA || 0;
    const offP     = it.offsetP || 0;

    let path;
    if (it.finalPath) {
      /* The overhang-hugging pass stores its result in PLACEMENT
         coordinates — the top of the strip band is y = 0 there,
         whereas the drawing pass works in canvas coordinates where
         the strip starts at stripY.  Translate the stored y by
         stripY; the x coordinates are identical in both systems. */
      path = it.finalPath.map(p => [p[0], p[1] + stripY]);
    } else {
      const basePath = computeLeaderPath(
        anchorX, anchorY, offA, offP, chanY, pillX, pillTopY,
        it, placed, stripBottom, topPad, trackOffsets, it.diveMode || 0);
      const bevelled = _bevelPath(basePath, it.bevel0 || 0, it.bevel1 || 0);
      path = _applyJogsToPath(bevelled, it);
    }

    const style = LEADER_STYLES[it.leaderStyle % LEADER_STYLES.length];
    c.setLineDash(style.dash || []);
    c.beginPath();
    c.moveTo(path[0][0], path[0][1]);
    for (let i = 1; i < path.length; i++) {
      c.lineTo(path[i][0], path[i][1]);
    }
    c.stroke();
  }
  c.setLineDash([]);
  c.restore();
}
"""
