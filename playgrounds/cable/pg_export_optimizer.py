"""
pg_export_optimizer.py — the leader-geometry optimiser.

Fallback status
---------------
As of the Rust leader-optimizer port, the two move-family passes in
this module — `optimizeLeaderGeometry` — are the JS-side fallback.  On
a server whose `leader_optimizer` binary is present and resolvable
(see `cable_playground.RUST_BIN`), this pass is invoked only via
`pg_export_rust`'s catch block when the `/optimize-leaders` round trip
fails.  The Rust port is a transcription of exactly this code;
nothing here has been changed by the port, and the two
implementations are intended to stay behaviourally identical.  If you
fix a bug in one, fix it in the other.

Six move families, all tried in one pass:

    field          moves                              step grid
    -------------  ---------------------------------  ----------
    bevel0/bevel1  the two corner cuts                3 px
    jog0/jog1      the two nearest-leg X offsets      3 px
    diveMode       0/1/2, the anchor leg's shape      discrete
    channelYRel    the horizontal run's Y             12 items
    offsetA        the anchor leg's X displacement    18 items
    detourBias     the tail's detour column offset    4 items

REASONING THAT LIVES ELSEWHERE NOW
==================================
The channel-to-pill tail builder (`_buildChannelToPill`), the main
`computeLeaderPath`, the two bevel helpers, and the two jog helpers
were defined in this file in the concatenation bundle.  They are pure
math over primitives with no optimizer state, and moving them down
into `pg_export_primitives.js` breaks the cycle

    penalties → optimizer → snapshot → penalties

that the ES-module boundary makes structural.  The bodies are
unchanged; see `pg_export_primitives.js` for the relocated code and
its own docstring for the full reasoning.

Everything this module reads is imported at the top:

    · the six move-family step constants from primitives
    · `_buildLayout`, `_countConflictsAt`, `_leaderHasConflictAt`,
      `_conflictedLeaderIndices` from snapshot
    · `_boundaryOverlapPenalty`, `_wireOverlapPenalty`,
      `_wallEdgeOverlapPenalty`, `_cornerFeature`, `_cornerDist` from
      penalties
    · `_countSegmentCrossings`, `_segBoxOverlap`, `_segSegDist`,
      `_pointSegDist`, `_angleDiff`, `_pathSegments`,
      `PILL_PROX`, `SEG_MIN_SEP`, `SEG_MIN_LEN`,
      `DOUBLE_CROSS_EXTRA_W` from geometry
"""

OPTIMIZER_JS = r"""
import {
  computeLeaderPath, _buildLeaderPathRel, _bevelPath, _applyJogsToPath,
  _candidatesFor,
  BEVEL_MIN_APPLY, JOG_MIN_APPLY,
} from "./pg_export_primitives.js";
import {
  _buildLayout, _countConflictsAt,
  _conflictedLeaderIndices,
} from "./pg_export_snapshot.js";
import {
  _boundaryOverlapPenalty, _wireOverlapPenalty, _wallEdgeOverlapPenalty,
  _cornerFeature, _cornerDist, CORNER_MIN_DIST,
} from "./pg_export_penalties.js";
import {
  _countSegmentCrossings, _segBoxOverlap, _segSegDist, _pointSegDist,
  _angleDiff, _pathSegments,
  PILL_PROX, SEG_MIN_SEP, SEG_MIN_LEN,
  DOUBLE_CROSS_EXTRA_W,
} from "./pg_export_geometry.js";

/* ==========================================================================
   OPTIMISER CONSTANTS
   ========================================================================== */

const OPT_MAX_PASSES = 8;

const DIVE_MODES = [0, 1, 2];

const CHANNEL_Y_STEPS = [
  -24, -16, -8, -4, 4, 8, 16, 24, 32, 40, -32, -40,
];

const OFFSET_A_STEPS = [
  -240, -200, -160, -120, -80, -48, -24, -8, -2,
     2,    8,   24,   48,  80, 120, 160, 200, 240,
];

const OFFSET_A_ABS = [
  -240, -200, -160, -120, -80, -48, -24, -16, -8, 0,
     8,   16,   24,   48,  80, 120, 160, 200, 240,
];

const DETOUR_BIAS_STEPS = [-40, -20, 20, 40];

/* ==========================================================================
   MAIN OPTIMISER
   ========================================================================== */

export function optimizeLeaderGeometry(placed, stripH, topPad, trackOffsets,
                                       boundaries, wireSegments, wallEdges) {
  for (const it of placed) {
    it.bevel0 = 0; it.bevel1 = 0;
    it.jog0   = 0; it.jog1   = 0;
    it.diveMode = 0;
    if (typeof it.detourBias !== "number") it.detourBias = 0;
  }
  if (placed.length < 2) return;

  const bxs   = Array.isArray(boundaries)   ? boundaries   : [];
  const wires = Array.isArray(wireSegments) ? wireSegments : [];
  const wes   = Array.isArray(wallEdges)    ? wallEdges    : [];

  const obstacleContext = {
    boundaries:   bxs,
    wireSegments: wires,
    wallEdges:    wes,
    placed:       placed,
    stripH:       stripH,
  };

  function evaluate() {
    const layout = _buildLayout(placed, stripH, topPad, trackOffsets);
    const pathSegs = layout.segs;
    const boxes    = layout.boxes;

    const allSegs = [];
    for (const segs of pathSegs) {
      for (const s of segs) {
        if (s.len >= SEG_MIN_LEN) allSegs.push(s);
      }
    }

    /* --- tier 1: V-V and H-H parallel collinear pairs --- */
    let vvCount = 0, vvDepth = 0;
    let hhCount = 0, hhDepth = 0;
    for (let i = 0; i < allSegs.length; i++) {
      const A = allSegs[i];
      for (let j = i + 1; j < allSegs.length; j++) {
        const B = allSegs[j];
        if (A.pathIdx === B.pathIdx) continue;

        if (A.kind === "vert" && B.kind === "vert") {
          const dx = Math.abs(A.ax - B.ax);
          if (dx >= SEG_MIN_SEP) continue;
          const oy0 = Math.max(A.minY, B.minY);
          const oy1 = Math.min(A.maxY, B.maxY);
          if (oy1 <= oy0) continue;
          vvCount++;
          vvDepth += (SEG_MIN_SEP - dx);
          continue;
        }
        if (A.kind === "horiz" && B.kind === "horiz") {
          const dy = Math.abs(A.ay - B.ay);
          if (dy >= SEG_MIN_SEP) continue;
          const ox0 = Math.max(A.minX, B.minX);
          const ox1 = Math.min(A.maxX, B.maxX);
          if (ox1 <= ox0) continue;
          hhCount++;
          hhDepth += (SEG_MIN_SEP - dy);
          continue;
        }
      }
    }

    /* --- tier 1b: double crossings --- */
    let extraCrossings = 0;
    for (let i = 0; i < pathSegs.length; i++) {
      for (let j = i + 1; j < pathSegs.length; j++) {
        const n = _countSegmentCrossings(pathSegs[i], pathSegs[j]);
        if (n >= 2) extraCrossings += (n - 1);
      }
    }

    /* --- tier 2b: segment vs foreign pill AABB --- */
    let pillCount = 0, pillDepth = 0;
    for (let i = 0; i < pathSegs.length; i++) {
      for (const s of pathSegs[i]) {
        if (s.len < 1.0) continue;
        for (let j = 0; j < boxes.length; j++) {
          if (j === i) continue;
          const box = boxes[j];
          const expanded = {
            qL: box.qL - PILL_PROX,
            qR: box.qR + PILL_PROX,
            qT: box.qT - PILL_PROX,
            qB: box.qB + PILL_PROX,
          };
          if (s.maxX < expanded.qL || s.minX > expanded.qR) continue;
          if (s.maxY < expanded.qT || s.minY > expanded.qB) continue;
          const t = _segBoxOverlap(s, expanded);
          if (t > 0) { pillCount++; pillDepth += t; }
        }
      }
    }

    /* --- tier 2: non-parallel-collinear segment pairs --- */
    let segCount = 0, segDepth = 0;
    for (let i = 0; i < allSegs.length; i++) {
      const A = allSegs[i];
      for (let j = i + 1; j < allSegs.length; j++) {
        const B = allSegs[j];
        if (A.pathIdx === B.pathIdx) continue;
        if (A.kind === "vert"  && B.kind === "vert")  continue;
        if (A.kind === "horiz" && B.kind === "horiz") continue;
        if (A.maxX + SEG_MIN_SEP < B.minX) continue;
        if (B.maxX + SEG_MIN_SEP < A.minX) continue;
        if (A.maxY + SEG_MIN_SEP < B.minY) continue;
        if (B.maxY + SEG_MIN_SEP < A.minY) continue;
        const d = _segSegDist(A.ax, A.ay, A.bx, A.by,
                              B.ax, B.ay, B.bx, B.by);
        if (d < SEG_MIN_SEP) { segCount++; segDepth += (SEG_MIN_SEP - d); }
      }
    }

    /* --- tier 3: boundary / wire / wall-edge --- */
    const bPen = _boundaryOverlapPenalty(allSegs, bxs, stripH);
    const bCount = bPen.count, bDepth = bPen.depth;

    const wPen = _wireOverlapPenalty(allSegs, placed, wires);
    const wireCount     = wPen.count;
    const wireDepth     = wPen.depth;
    const wireParCount  = wPen.parCount;
    const wireParDepth  = wPen.parDepth;
    const wireApprCount = wPen.approachCount;
    const wireApprDepth = wPen.approachDepth;

    const ePen = _wallEdgeOverlapPenalty(allSegs, wes);
    const weCount = ePen.count, weDepth = ePen.depth;

    /* --- tier 3d: arrowhead regions vs foreign segments --- */
    const HEAD_HALF = 5;
    let headCount = 0, headDepth = 0;
    for (let i = 0; i < placed.length; i++) {
      const it = placed[i];
      const anchorPt = [it.anchorCx, it.anchorCyRel];
      const pillPt   = [it.pillCenterX,
                        stripH + topPad + trackOffsets[it.track]];
      for (const [hx, hy] of [anchorPt, pillPt]) {
        const box = { qL: hx - HEAD_HALF, qR: hx + HEAD_HALF,
                      qT: hy - HEAD_HALF, qB: hy + HEAD_HALF };
        for (let j = 0; j < pathSegs.length; j++) {
          if (j === i) continue;
          for (const s of pathSegs[j]) {
            if (s.maxX < box.qL || s.minX > box.qR) continue;
            if (s.maxY < box.qT || s.minY > box.qB) continue;
            if (_segBoxOverlap(s, box) > 0) {
              headCount++;
              headDepth += HEAD_HALF;
            }
          }
        }
      }
    }

    /* --- tier 4: corner features --- */
    const corners = [];
    for (let pi = 0; pi < placed.length; pi++) {
      const it = placed[pi];
      corners.push({
        pathIdx: pi,
        ..._cornerFeature(it, placed, stripH, topPad, trackOffsets, 0),
      });
      corners.push({
        pathIdx: pi,
        ..._cornerFeature(it, placed, stripH, topPad, trackOffsets, 1),
      });
    }

    let cornerCount = 0, cornerDepth = 0;

    for (let i = 0; i < corners.length; i++) {
      for (let j = i + 1; j < corners.length; j++) {
        const A = corners[i], B = corners[j];
        if (A.pathIdx === B.pathIdx) continue;
        const d = _cornerDist(A, B);
        if (d < CORNER_MIN_DIST) {
          cornerCount++;
          cornerDepth += (CORNER_MIN_DIST - d);
        }
      }
    }

    for (const cf of corners) {
      const cfIsPt = (Math.abs(cf.x0 - cf.x1) < 0.5 &&
                      Math.abs(cf.y0 - cf.y1) < 0.5);
      for (const s of allSegs) {
        if (s.pathIdx === cf.pathIdx) continue;
        let d;
        if (cfIsPt) {
          d = _pointSegDist(cf.x0, cf.y0, s.ax, s.ay, s.bx, s.by);
        } else {
          d = _segSegDist(cf.x0, cf.y0, cf.x1, cf.y1,
                          s.ax, s.ay, s.bx, s.by);
        }
        if (d < CORNER_MIN_DIST) {
          cornerCount++;
          cornerDepth += (CORNER_MIN_DIST - d);
        }
      }
    }

    for (const cf of corners) {
      const cfIsPt = (Math.abs(cf.x0 - cf.x1) < 0.5 &&
                      Math.abs(cf.y0 - cf.y1) < 0.5);
      for (let bi = 0; bi < boxes.length; bi++) {
        if (bi === cf.pathIdx) continue;
        const box = boxes[bi];
        let hit = false;
        if (cfIsPt) {
          hit = cf.x0 >= box.qL && cf.x0 <= box.qR &&
                cf.y0 >= box.qT && cf.y0 <= box.qB;
        } else {
          const s = { ax: cf.x0, ay: cf.y0, bx: cf.x1, by: cf.y1,
                      minX: Math.min(cf.x0, cf.x1),
                      maxX: Math.max(cf.x0, cf.x1),
                      minY: Math.min(cf.y0, cf.y1),
                      maxY: Math.max(cf.y0, cf.y1) };
          hit = _segBoxOverlap(s, box) > 0;
        }
        if (hit) {
          cornerCount++;
          cornerDepth += CORNER_MIN_DIST;
        }
      }
    }

    const score =
      vvCount         * 1e12 + vvDepth         * 1e11 +
      hhCount         * 1e12 + hhDepth         * 1e11 +
      pillCount       * 5e11 + pillDepth       * 5e10 +
      wireParCount    * 1e11 + wireParDepth    * 1e10 +
      weCount         * 1e11 + weDepth         * 1e10 +
      bCount          * 1e11 + bDepth          * 1e10 +
      headCount       * 1e10 + headDepth       * 1e9  +
      extraCrossings  * DOUBLE_CROSS_EXTRA_W +
      segCount        * 1e9  + segDepth        * 1e8  +
      wireCount       * 1e9  + wireDepth       * 1e8  +
      wireApprCount   * 1e7  + wireApprDepth   * 1e6  +
      cornerCount     * 1e6  + cornerDepth     * 1e5;

    return { score };
  }

  let cur = evaluate();
  if (cur.score < 1) return;

  /* ---- Main pass loop ---- */
  for (let pass = 0; pass < OPT_MAX_PASSES; pass++) {
    const layout = _buildLayout(placed, stripH, topPad, trackOffsets);
    const conflicted = _conflictedLeaderIndices(layout, obstacleContext);

    let best = null;

    for (let li = 0; li < placed.length; li++) {
      if (!conflicted.has(li)) continue;
      const it = placed[li];

      for (const field of ["bevel0", "bevel1", "jog0", "jog1"]) {
        const curVal  = it[field] || 0;
        const isBevel = field.startsWith("bevel");
        const cands   = _candidatesFor(curVal, isBevel);

        for (const cand of cands) {
          const saved = it[field];
          it[field] = cand;
          const test = evaluate();
          it[field] = saved;

          if (test.score < cur.score - 0.5) {
            if (!best || test.score < best.test.score) {
              best = { it, field, newValue: cand, test };
            }
          }
        }
      }

      const curMode = it.diveMode || 0;
      for (const cand of DIVE_MODES) {
        if (cand === curMode) continue;
        const saved = it.diveMode;
        it.diveMode = cand;
        const test = evaluate();
        it.diveMode = saved;

        if (test.score < cur.score - 0.5) {
          if (!best || test.score < best.test.score) {
            best = { it, field: 'diveMode', newValue: cand, test };
          }
        }
      }

      const curLane = (typeof it.channelYRel === "number")
        ? it.channelYRel : 0;
      for (const dY of CHANNEL_Y_STEPS) {
        const newLane = curLane + dY;
        if (newLane < 2) continue;
        if (newLane >= topPad - 2) continue;

        const saved = it.channelYRel;
        it.channelYRel = newLane;
        const test = evaluate();
        it.channelYRel = saved;

        if (test.score < cur.score - 0.5) {
          if (!best || test.score < best.test.score) {
            best = { it, field: 'channelYRel', newValue: newLane, test };
          }
        }
      }

      const curOff = it.offsetA || 0;
      for (const dOff of OFFSET_A_STEPS) {
        const newOff = curOff + dOff;
        const saved = it.offsetA;
        it.offsetA = newOff;
        const test = evaluate();
        it.offsetA = saved;

        if (test.score < cur.score - 0.5) {
          if (!best || test.score < best.test.score) {
            best = { it, field: 'offsetA', newValue: newOff, test };
          }
        }
      }

      const curBias = it.detourBias || 0;
      for (const cand of DETOUR_BIAS_STEPS) {
        if (cand === curBias) continue;
        const saved = it.detourBias;
        it.detourBias = cand;
        const test = evaluate();
        it.detourBias = saved;

        if (test.score < cur.score - 0.5) {
          if (!best || test.score < best.test.score) {
            best = { it, field: 'detourBias', newValue: cand, test };
          }
        }
      }
    }

    if (!best) break;
    best.it[best.field] = best.newValue;
    cur = best.test;
    if (cur.score < 1) break;
  }

  /* ==========================================================================
     COORDINATED PAIR REFINEMENT
     ========================================================================== */
  {
    const laneChoices = [2, 6, 10, 14, 18, 22, 26, 30];
    const biasChoices = [0, -40, -20, 20, 40];

    const BYSTANDER_RADIUS = 100;
    const MAX_BYSTANDERS   = 4;

    for (let iter = 0; iter < 2; iter++) {
      const layout0 = _buildLayout(placed, stripH, topPad, trackOffsets);
      const badPairs = [];
      for (let i = 0; i < placed.length; i++) {
        for (let j = i + 1; j < placed.length; j++) {
          const xc = _countSegmentCrossings(layout0.segs[i], layout0.segs[j]);
          if (xc >= 2) badPairs.push({ i, j, xc });
        }
      }
      badPairs.sort((a, b) => b.xc - a.xc);

      let anyImproved = false;

      /* ---- Pass 1: 2-way coordinated ---- */
      for (let k = 0; k < Math.min(badPairs.length, 8); k++) {
        const { i, j } = badPairs[k];
        const A = placed[i], B = placed[j];

        const aOrigLane = A.channelYRel;
        const aOrigOff  = A.offsetA || 0;
        const aOrigBias = A.detourBias || 0;
        const bOrigLane = B.channelYRel;
        const bOrigOff  = B.offsetA || 0;
        const bOrigBias = B.detourBias || 0;

        let bestScore = cur.score;
        let bestALane = aOrigLane, bestAOff = aOrigOff, bestABias = aOrigBias;
        let bestBLane = bOrigLane, bestBOff = bOrigOff, bestBBias = bOrigBias;

        for (const aLane of laneChoices) {
          if (aLane >= topPad - 2) continue;
          A.channelYRel = aLane;
          for (const bLane of laneChoices) {
            if (bLane >= topPad - 2) continue;
            B.channelYRel = bLane;
            const s = evaluate().score;
            if (s < bestScore - 0.5) {
              bestScore = s;
              bestALane = aLane; bestBLane = bLane;
              bestAOff = aOrigOff; bestBOff = bOrigOff;
              bestABias = aOrigBias; bestBBias = bOrigBias;
            }
          }
        }
        A.channelYRel = aOrigLane; B.channelYRel = bOrigLane;

        for (const aOff of OFFSET_A_ABS) {
          A.offsetA = aOff;
          for (const bOff of OFFSET_A_ABS) {
            B.offsetA = bOff;
            const s = evaluate().score;
            if (s < bestScore - 0.5) {
              bestScore = s;
              bestAOff = aOff; bestBOff = bOff;
              bestALane = aOrigLane; bestBLane = bOrigLane;
              bestABias = aOrigBias; bestBBias = bOrigBias;
            }
          }
        }
        A.offsetA = aOrigOff; B.offsetA = bOrigOff;

        for (const aBias of biasChoices) {
          A.detourBias = aBias;
          for (const bBias of biasChoices) {
            B.detourBias = bBias;
            const s = evaluate().score;
            if (s < bestScore - 0.5) {
              bestScore = s;
              bestABias = aBias; bestBBias = bBias;
              bestALane = aOrigLane; bestBLane = bOrigLane;
              bestAOff = aOrigOff; bestBOff = bOrigOff;
            }
          }
        }
        A.detourBias = aOrigBias; B.detourBias = bOrigBias;

        for (const aLane of laneChoices) {
          if (aLane >= topPad - 2) continue;
          A.channelYRel = aLane;
          for (const bOff of OFFSET_A_ABS) {
            B.offsetA = bOff;
            const s = evaluate().score;
            if (s < bestScore - 0.5) {
              bestScore = s;
              bestALane = aLane; bestBOff = bOff;
              bestBLane = bOrigLane; bestAOff = aOrigOff;
              bestABias = aOrigBias; bestBBias = bOrigBias;
            }
          }
        }
        A.channelYRel = aOrigLane; B.offsetA = bOrigOff;

        for (const aOff of OFFSET_A_ABS) {
          A.offsetA = aOff;
          for (const bLane of laneChoices) {
            if (bLane >= topPad - 2) continue;
            B.channelYRel = bLane;
            const s = evaluate().score;
            if (s < bestScore - 0.5) {
              bestScore = s;
              bestAOff = aOff; bestBLane = bLane;
              bestALane = aOrigLane; bestBOff = bOrigOff;
              bestABias = aOrigBias; bestBBias = bOrigBias;
            }
          }
        }
        A.offsetA = aOrigOff; B.channelYRel = bOrigLane;

        for (const aLane of laneChoices) {
          if (aLane >= topPad - 2) continue;
          A.channelYRel = aLane;
          for (const bBias of biasChoices) {
            B.detourBias = bBias;
            const s = evaluate().score;
            if (s < bestScore - 0.5) {
              bestScore = s;
              bestALane = aLane; bestBBias = bBias;
              bestBLane = bOrigLane; bestABias = aOrigBias;
              bestAOff = aOrigOff; bestBOff = bOrigOff;
            }
          }
        }
        A.channelYRel = aOrigLane; B.detourBias = bOrigBias;

        for (const aBias of biasChoices) {
          A.detourBias = aBias;
          for (const bLane of laneChoices) {
            if (bLane >= topPad - 2) continue;
            B.channelYRel = bLane;
            const s = evaluate().score;
            if (s < bestScore - 0.5) {
              bestScore = s;
              bestABias = aBias; bestBLane = bLane;
              bestALane = aOrigLane; bestBBias = bOrigBias;
              bestAOff = aOrigOff; bestBOff = bOrigOff;
            }
          }
        }
        A.detourBias = aOrigBias; B.channelYRel = bOrigLane;

        for (const aOff of OFFSET_A_ABS) {
          A.offsetA = aOff;
          for (const bBias of biasChoices) {
            B.detourBias = bBias;
            const s = evaluate().score;
            if (s < bestScore - 0.5) {
              bestScore = s;
              bestAOff = aOff; bestBBias = bBias;
              bestALane = aOrigLane; bestBLane = bOrigLane;
              bestABias = aOrigBias; bestBOff = bOrigOff;
            }
          }
        }
        A.offsetA = aOrigOff; B.detourBias = bOrigBias;

        for (const aBias of biasChoices) {
          A.detourBias = aBias;
          for (const bOff of OFFSET_A_ABS) {
            B.offsetA = bOff;
            const s = evaluate().score;
            if (s < bestScore - 0.5) {
              bestScore = s;
              bestABias = aBias; bestBOff = bOff;
              bestALane = aOrigLane; bestBLane = bOrigLane;
              bestAOff = aOrigOff; bestBBias = bOrigBias;
            }
          }
        }
        A.detourBias = aOrigBias; B.offsetA = bOrigOff;

        A.channelYRel = bestALane;
        A.offsetA     = bestAOff;
        A.detourBias  = bestABias;
        B.channelYRel = bestBLane;
        B.offsetA     = bestBOff;
        B.detourBias  = bestBBias;

        if (bestScore < cur.score - 0.5) {
          cur = { score: bestScore };
          anyImproved = true;
        }
      }

      /* ---- Pass 1b: wire-avoidance coordinated ---- */
      {
        const layout1b = _buildLayout(placed, stripH, topPad, trackOffsets);
        for (let li = 0; li < placed.length; li++) {
          const it = placed[li];
          const wPenL = _wireOverlapPenalty(layout1b.segs[li], placed, wires);
          if (wPenL.count === 0 && wPenL.approachCount === 0) continue;

          const origDive = it.diveMode || 0;
          const origChan = it.channelYRel;
          const origOff  = it.offsetA || 0;

          let bestScore = cur.score;
          let bestDive = origDive, bestChan = origChan, bestOff = origOff;

          for (const dM of DIVE_MODES) {
            if (dM === origDive) continue;
            it.diveMode = dM;
            for (const cL of laneChoices) {
              if (cL >= topPad - 2 || cL === origChan) continue;
              it.channelYRel = cL;
              const s = evaluate().score;
              if (s < bestScore - 0.5) {
                bestScore = s;
                bestDive = dM; bestChan = cL; bestOff = origOff;
              }
            }
          }
          it.diveMode = origDive; it.channelYRel = origChan;

          for (const dM of DIVE_MODES) {
            if (dM === origDive) continue;
            it.diveMode = dM;
            for (const cO of OFFSET_A_ABS) {
              if (cO === origOff) continue;
              it.offsetA = cO;
              const s = evaluate().score;
              if (s < bestScore - 0.5) {
                bestScore = s;
                bestDive = dM; bestChan = origChan; bestOff = cO;
              }
            }
          }
          it.diveMode = origDive; it.offsetA = origOff;

          if (bestDive !== origDive ||
              bestChan !== origChan ||
              bestOff  !== origOff) {
            it.diveMode    = bestDive;
            it.channelYRel = bestChan;
            it.offsetA     = bestOff;
            cur = { score: bestScore };
            anyImproved = true;
          }
        }
      }

      /* ---- Pass 2: 3-way with the bystander ---- */
      for (let k = 0; k < Math.min(badPairs.length, 4); k++) {
        const { i, j } = badPairs[k];
        const A = placed[i], B = placed[j];

        const cps = [];
        for (const sa of layout0.segs[i]) {
          for (const sb of layout0.segs[j]) {
            if (!_segSegProperCrossLocal(sa, sb)) continue;
            const d1x = sa.bx - sa.ax, d1y = sa.by - sa.ay;
            const d2x = sb.bx - sb.ax, d2y = sb.by - sb.ay;
            const denom = d1x * d2y - d1y * d2x;
            const rx = sb.ax - sa.ax, ry = sb.ay - sa.ay;
            const t = (rx * d2y - ry * d2x) / denom;
            cps.push([sa.ax + t * d1x, sa.ay + t * d1y]);
          }
        }
        if (cps.length < 2) continue;

        const candidates = [];
        for (let q = 0; q < placed.length; q++) {
          if (q === i || q === j) continue;
          let minD = Infinity;
          for (const cp of cps) {
            for (const s of layout0.segs[q]) {
              const d = _pointSegDist(cp[0], cp[1],
                                       s.ax, s.ay, s.bx, s.by);
              if (d < minD) minD = d;
            }
          }
          if (minD < BYSTANDER_RADIUS) {
            candidates.push({ idx: q, dist: minD });
          }
        }
        candidates.sort((a, b) => a.dist - b.dist);
        if (!candidates.length) continue;

        const aSave = { diveMode: A.diveMode, channelYRel: A.channelYRel,
                        offsetA: A.offsetA, detourBias: A.detourBias };

        let fixed = false;

        for (let bIdx = 0;
             bIdx < Math.min(MAX_BYSTANDERS, candidates.length) && !fixed;
             bIdx++) {
          const bystanderIdx = candidates[bIdx].idx;
          const C = placed[bystanderIdx];
          const cSave = { diveMode: C.diveMode, channelYRel: C.channelYRel,
                          offsetA: C.offsetA, detourBias: C.detourBias };

          let bestScore = cur.score;
          let bestAMove = null, bestCMove = null;

          for (const aD of [0, 1, 2]) {
            if (aD === aSave.diveMode) continue;
            A.diveMode = aD;
            for (const cD of [0, 1, 2]) {
              if (cD === cSave.diveMode) continue;
              C.diveMode = cD;
              const s = evaluate().score;
              if (s < bestScore - 0.5) {
                bestScore = s;
                bestAMove = { field: "diveMode", value: aD };
                bestCMove = { field: "diveMode", value: cD };
              }
            }
          }
          A.diveMode = aSave.diveMode;
          C.diveMode = cSave.diveMode;

          for (const aD of [0, 1, 2]) {
            if (aD === aSave.diveMode) continue;
            A.diveMode = aD;
            for (const cL of laneChoices) {
              if (cL >= topPad - 2 || cL === cSave.channelYRel) continue;
              C.channelYRel = cL;
              const s = evaluate().score;
              if (s < bestScore - 0.5) {
                bestScore = s;
                bestAMove = { field: "diveMode", value: aD };
                bestCMove = { field: "channelYRel", value: cL };
              }
            }
          }
          A.diveMode = aSave.diveMode;
          C.channelYRel = cSave.channelYRel;

          for (const aD of [0, 1, 2]) {
            if (aD === aSave.diveMode) continue;
            A.diveMode = aD;
            for (const cO of OFFSET_A_ABS) {
              if (cO === cSave.offsetA) continue;
              C.offsetA = cO;
              const s = evaluate().score;
              if (s < bestScore - 0.5) {
                bestScore = s;
                bestAMove = { field: "diveMode", value: aD };
                bestCMove = { field: "offsetA", value: cO };
              }
            }
          }
          A.diveMode = aSave.diveMode;
          C.offsetA = cSave.offsetA;

          if (bestAMove && bestCMove) {
            A[bestAMove.field] = bestAMove.value;
            C[bestCMove.field] = bestCMove.value;
            cur = { score: bestScore };
            anyImproved = true;
            fixed = true;
          }
        }
      }

      if (!anyImproved) break;
    }
  }
}

/* Local aliases for the two geometry atoms the 3-way pass needs. */
import { _segSegProperCross as _segSegProperCrossShared } from "./pg_export_geometry.js";
function _segSegProperCrossLocal(a, b) { return _segSegProperCrossShared(a, b); }
"""
