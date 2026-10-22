"""
pg_export_pillpush.py — the pill push, the double-crossing hug pass,
the cross-track descent separation, and the polish loop.

Fallback status
---------------
As of the Rust leader-optimizer port, the four passes in this module
— `pushPillsForLeaderConflicts`, `rerouteDoubleCrossingsByHugging`,
`_separateCrossTrackDescents`, and `polishLeaderLayout` — are the
JS-side fallback.  On a server whose `leader_optimizer` binary is
present and resolvable (see `cable_playground.RUST_BIN`), they are
invoked only via `pg_export_rust`'s catch block when the
`/optimize-leaders` round trip fails.  The Rust port is a
transcription of exactly this code.  If you fix a bug in one, fix it
in the other.

ONE PASS AT A TIME
==================
    pushPillsForLeaderConflicts   moves pill boxes when the optimiser
                                  has exhausted every leader move

    rerouteDoubleCrossingsByHugging   flips a single leader's geometry
                                  around a double-crossing partner

    _separateCrossTrackDescents   nudges descent columns apart when
                                  two pills on different tracks land
                                  at nearly the same x

    polishLeaderLayout            orchestrates the three, restores
                                  the best-scoring state across
                                  rounds, then returns

The optimiser itself (`pg_export_optimizer.optimizeLeaderGeometry`) is
called once at the start of each polish round — same as the
concatenation bundle.

DEPENDENCY EDGES
================
    optimizer     → snapshot, penalties, geometry, primitives
    pillpush      → optimizer, snapshot, penalties, geometry, primitives
    labels        → optimizer, pillpush, snapshot, penalties,
                    geometry, primitives, style

The three top-level files form a one-directional chain; nothing
imports back up.
"""

PILLPUSH_JS = r"""
import {
  computeLeaderPath, _bevelPath, _applyJogsToPath, _buildLeaderPathRel,
  _pathSegments, _segBoxOverlap,
  PILL_PROX, SEG_MIN_LEN,
} from "./pg_export_primitives.js";
import {
  _segSegDist, _pointSegDist,
} from "./pg_export_geometry.js";
import { optimizeLeaderGeometry } from "./pg_export_optimizer.js";
import {
  _buildLayout, _countConflictsAt, _expandBox,
} from "./pg_export_snapshot.js";
import {
  _wireOverlapPenalty,
} from "./pg_export_penalties.js";

/* ==========================================================================
   PILL PUSH PASS
   ========================================================================== */

export function buildPathSegsFor(it, placed, stripH, topPad, trackOffsets) {
  const chanY    = stripH + (it.channelYRel || 0);
  const pillTopY = stripH + topPad + trackOffsets[it.track];
  const base = computeLeaderPath(
    it.anchorCx, it.anchorCyRel, it.offsetA || 0, it.offsetP || 0,
    chanY, it.pillCenterX, pillTopY,
    it, placed, stripH, topPad, trackOffsets, it.diveMode || 0);
  const path = _applyJogsToPath(
    _bevelPath(base, it.bevel0 || 0, it.bevel1 || 0), it);
  return _pathSegments(path, -1, SEG_MIN_LEN);
}

function _pillHasSameTrackCollision(idx, placed) {
  const me = placed[idx];
  const myTrack = me.track;
  const myL = me.pillCenterX - me.w / 2;
  const myR = me.pillCenterX + me.w / 2;

  for (let j = 0; j < placed.length; j++) {
    if (j === idx) continue;
    const other = placed[j];
    if (other.track !== myTrack) continue;
    const oL = other.pillCenterX - other.w / 2;
    const oR = other.pillCenterX + other.w / 2;
    if (myR + PILL_PROX > oL && oR + PILL_PROX > myL) return true;
  }
  return false;
}

function _separatePillsInTracks(placed) {
  const byTrack = new Map();
  for (let i = 0; i < placed.length; i++) {
    const t = placed[i].track;
    if (!byTrack.has(t)) byTrack.set(t, []);
    byTrack.get(t).push(i);
  }
  for (const [, indices] of byTrack) {
    indices.sort((a, b) =>
      placed[a].pillCenterX - placed[b].pillCenterX);
    for (let k = 1; k < indices.length; k++) {
      const prev = placed[indices[k - 1]];
      const cur  = placed[indices[k]];
      const prevR = prev.pillCenterX + prev.w / 2;
      const curL  = cur.pillCenterX - cur.w / 2;
      const gap = curL - prevR;
      if (gap < PILL_PROX) {
        cur.pillCenterX += (PILL_PROX - gap);
      }
    }
  }
}

export function pushPillsForLeaderConflicts(placed, stripH, topPad,
                                            trackOffsets, obstacleContext) {
  const PUSH = [];
  for (let d = 4; d <= 32; d += 4) { PUSH.push(d); PUSH.push(-d); }

  const MAX_ROUNDS = 3;

  _separatePillsInTracks(placed);

  for (let round = 0; round < MAX_ROUNDS; round++) {
    let anyMoved = false;

    for (let li = 0; li < placed.length; li++) {
      const it = placed[li];
      let layout = _buildLayout(placed, stripH, topPad, trackOffsets);
      const before = _countConflictsAt(li, layout, obstacleContext);
      if (before === 0) continue;

      const origX = it.pillCenterX;
      let bestX = origX, bestN = before;
      for (const dX of PUSH) {
        it.pillCenterX = origX + dX;
        if (_pillHasSameTrackCollision(li, placed)) continue;
        layout = _buildLayout(placed, stripH, topPad, trackOffsets);
        const n = _countConflictsAt(li, layout, obstacleContext);
        if (n < bestN) { bestN = n; bestX = origX + dX; }
      }
      it.pillCenterX = bestX;
      if (bestX !== origX) anyMoved = true;

      if (bestN > 0) {
        const involved = new Set();
        const selfSegs = layout.segs[li];

        for (let j = 0; j < placed.length; j++) {
          if (j === li) continue;
          const otherSegs = layout.segs[j];
          let cflt = false;

          for (const sa of selfSegs) {
            if (cflt) break;
            for (const sb of otherSegs) {
              if (sa.maxX + PILL_PROX < sb.minX) continue;
              if (sb.maxX + PILL_PROX < sa.minX) continue;
              if (sa.maxY + PILL_PROX < sb.minY) continue;
              if (sb.maxY + PILL_PROX < sa.minY) continue;
              const d = _segSegDist(sa.ax, sa.ay, sa.bx, sa.by,
                                    sb.ax, sb.ay, sb.bx, sb.by);
              if (d < PILL_PROX) { cflt = true; break; }
            }
          }

          if (!cflt) {
            const box = layout.boxes[j];
            const expanded = _expandBox(box, PILL_PROX);
            for (const s of selfSegs) {
              if (s.maxX < expanded.qL || s.minX > expanded.qR) continue;
              if (s.maxY < expanded.qT || s.minY > expanded.qB) continue;
              if (_segBoxOverlap(s, expanded) > 0) { cflt = true; break; }
            }
          }

          if (cflt) involved.add(j);
        }

        for (const j of involved) {
          const foreign = placed[j];
          const fOrigX = foreign.pillCenterX;
          let fBestX = fOrigX, fBestN = bestN;
          for (const dX of PUSH) {
            foreign.pillCenterX = fOrigX + dX;
            if (_pillHasSameTrackCollision(j, placed)) continue;
            layout = _buildLayout(placed, stripH, topPad, trackOffsets);
            const n = _countConflictsAt(li, layout, obstacleContext);
            if (n < fBestN) { fBestN = n; fBestX = fOrigX + dX; }
          }
          foreign.pillCenterX = fBestX;
          if (fBestX !== fOrigX) { anyMoved = true; bestN = fBestN; }
        }
      }
    }
    if (!anyMoved) break;
  }

  /* ---- Coordinated pill pair moves ---- */
  {
    const SHIFTS = [0, -8, 8, -16, 16, -24, 24];
    const layout0 = _buildLayout(placed, stripH, topPad, trackOffsets);

    const baselineTotal = (() => {
      let n = 0;
      for (let i = 0; i < placed.length; i++) {
        n += _countConflictsAt(i, layout0, obstacleContext);
      }
      return Math.floor(n / 2);
    })();

    const pairs = [];
    for (let i = 0; i < placed.length; i++) {
      for (let j = i + 1; j < placed.length; j++) {
        const xc = _countSegmentCrossingsLocal(layout0.segs[i], layout0.segs[j]);
        if (xc >= 2) pairs.push({ i, j, xc });
      }
    }
    pairs.sort((a, b) => b.xc - a.xc);

    for (const p of pairs.slice(0, 4)) {
      const A = placed[p.i];
      const B = placed[p.j];
      const aOrig = A.pillCenterX;
      const bOrig = B.pillCenterX;

      let bestA = aOrig, bestB = bOrig, bestTotal = baselineTotal;

      for (const da of SHIFTS) {
        A.pillCenterX = aOrig + da;
        if (_pillHasSameTrackCollision(p.i, placed)) continue;
        for (const db of SHIFTS) {
          B.pillCenterX = bOrig + db;
          if (_pillHasSameTrackCollision(p.j, placed)) continue;
          const lay = _buildLayout(placed, stripH, topPad, trackOffsets);
          let tot = 0;
          for (let q = 0; q < placed.length; q++) {
            tot += _countConflictsAt(q, lay, obstacleContext);
          }
          tot = Math.floor(tot / 2);
          if (tot < bestTotal - 0.5) {
            bestTotal = tot;
            bestA = aOrig + da;
            bestB = bOrig + db;
          }
        }
      }
      A.pillCenterX = bestA;
      B.pillCenterX = bestB;
    }
  }
}

/* `_countSegmentCrossings` is defined in geometry.  Local alias so the
   couple of call sites read cleanly. */
import { _countSegmentCrossings as _countSegmentsXShared } from "./pg_export_geometry.js";
function _countSegmentCrossingsLocal(a, b) { return _countSegmentsXShared(a, b); }

/* ==========================================================================
   DOUBLE-CROSSING HUG PASS
   ========================================================================== */

export function rerouteDoubleCrossingsByHugging(placed, stripH, topPad,
                                                 trackOffsets,
                                                 boundaries, wireSegments,
                                                 wallEdges) {
  const HUG_GAP      = 8.0;
  const MIN_CHANNEL  = 2;
  const MAX_CHANNEL  = topPad - 2;
  const MAX_PAIRS    = 8;

  if (MAX_CHANNEL <= MIN_CHANNEL) return;

  const obstacleContext = {
    boundaries:   boundaries   || [],
    wireSegments: wireSegments || [],
    wallEdges:    wallEdges    || [],
    placed, stripH,
  };

  let layout = _buildLayout(placed, stripH, topPad, trackOffsets);

  const pairs = [];
  for (let i = 0; i < placed.length; i++) {
    for (let j = i + 1; j < placed.length; j++) {
      const xc = _countSegmentCrossingsLocal(layout.segs[i], layout.segs[j]);
      if (xc >= 2) pairs.push({ i, j, xc });
    }
  }
  if (!pairs.length) return;
  pairs.sort((a, b) => b.xc - a.xc);

  const totalConflicts = (lay) => {
    let n = 0;
    for (let q = 0; q < placed.length; q++) {
      n += _countConflictsAt(q, lay, obstacleContext);
    }
    return n;
  };

  for (const p of pairs.slice(0, MAX_PAIRS)) {
    layout = _buildLayout(placed, stripH, topPad, trackOffsets);
    if (_countSegmentCrossingsLocal(layout.segs[p.i], layout.segs[p.j]) < 2) {
      continue;
    }

    const totalBefore = totalConflicts(layout);
    let bestTotal = totalBefore;
    let bestMove  = null;

    for (const [idxSelf, idxOther] of [[p.i, p.j], [p.j, p.i]]) {
      const A = placed[idxSelf];
      const B = placed[idxOther];

      const aSave = {
        diveMode:    A.diveMode || 0,
        channelYRel: A.channelYRel,
        offsetA:     A.offsetA || 0,
      };

      const bChanY = B.channelYRel || 0;

      const bCols = [B.pillCenterX];
      if ((B.diveMode || 0) !== 2) {
        bCols.push(B.anchorCx + (B.offsetA || 0));
      }
      const bColMin = Math.min(...bCols);
      const bColMax = Math.max(...bCols);

      const candidates = [];

      const hugAbove = bChanY - HUG_GAP;
      if (hugAbove >= MIN_CHANNEL && hugAbove <= MAX_CHANNEL) {
        candidates.push({ diveMode: 0, channelYRel: hugAbove,
                          offsetA: aSave.offsetA });
        candidates.push({ diveMode: 1, channelYRel: hugAbove,
                          offsetA: aSave.offsetA });
      }

      const hugBelow = bChanY + HUG_GAP;
      if (hugBelow >= MIN_CHANNEL && hugBelow <= MAX_CHANNEL) {
        candidates.push({ diveMode: 0, channelYRel: hugBelow,
                          offsetA: aSave.offsetA });
        candidates.push({ diveMode: 1, channelYRel: hugBelow,
                          offsetA: aSave.offsetA });
      }

      candidates.push({ diveMode: 2, channelYRel: aSave.channelYRel,
                        offsetA: aSave.offsetA });

      if (A.anchorCx > bColMin) {
        const newOffA = (bColMin - HUG_GAP) - A.anchorCx;
        candidates.push({ diveMode: 0, channelYRel: aSave.channelYRel,
                          offsetA: newOffA });
      }
      if (A.anchorCx < bColMax) {
        const newOffA = (bColMax + HUG_GAP) - A.anchorCx;
        candidates.push({ diveMode: 0, channelYRel: aSave.channelYRel,
                          offsetA: newOffA });
      }

      for (const c of candidates) {
        if (c.diveMode === aSave.diveMode &&
            Math.abs(c.channelYRel - aSave.channelYRel) < 0.5 &&
            Math.abs(c.offsetA     - aSave.offsetA)     < 0.5) {
          continue;
        }
        if (c.channelYRel < MIN_CHANNEL || c.channelYRel > MAX_CHANNEL) {
          continue;
        }

        A.diveMode    = c.diveMode;
        A.channelYRel = c.channelYRel;
        A.offsetA     = c.offsetA;

        const newLayout = _buildLayout(placed, stripH, topPad, trackOffsets);
        const newXc = _countSegmentCrossingsLocal(newLayout.segs[idxSelf],
                                                  newLayout.segs[idxOther]);

        if (newXc < 2) {
          const totalAfter = totalConflicts(newLayout);
          if (totalAfter < bestTotal) {
            bestTotal = totalAfter;
            bestMove  = { A, c };
          }
        }

        A.diveMode    = aSave.diveMode;
        A.channelYRel = aSave.channelYRel;
        A.offsetA     = aSave.offsetA;
      }
    }

    if (bestMove) {
      bestMove.A.diveMode    = bestMove.c.diveMode;
      bestMove.A.channelYRel = bestMove.c.channelYRel;
      bestMove.A.offsetA     = bestMove.c.offsetA;
    }
  }
}

/* ==========================================================================
   CROSS-TRACK DESCENT SEPARATION
   ========================================================================== */

function _descentSlack(placed, idx, side) {
  const it = placed[idx];
  const myL = it.pillCenterX - it.w / 2;
  const myR = it.pillCenterX + it.w / 2;
  const myTrack = it.track;
  let nearestLeft  = -Infinity;
  let nearestRight =  Infinity;
  for (let k = 0; k < placed.length; k++) {
    if (k === idx) continue;
    const other = placed[k];
    if (other.track !== myTrack) continue;
    const oL = other.pillCenterX - other.w / 2;
    const oR = other.pillCenterX + other.w / 2;
    if (side === "left" && oR <= myL) {
      nearestLeft = Math.max(nearestLeft, oR);
    } else if (side === "right" && oL >= myR) {
      nearestRight = Math.min(nearestRight, oL);
    }
  }
  if (side === "left") {
    return nearestLeft === -Infinity ? 1e6 : myL - nearestLeft;
  }
  return nearestRight === Infinity ? 1e6 : nearestRight - myR;
}

export function _separateCrossTrackDescents(placed, stripH, topPad,
                                             trackOffsets) {
  const MIN_SEP       = PILL_PROX;
  const MIN_Y_OVERLAP = 5;
  const MAX_ITERS     = 6;

  for (let iter = 0; iter < MAX_ITERS; iter++) {
    let anyMoved = false;
    const n = placed.length;
    for (let i = 0; i < n; i++) {
      for (let j = i + 1; j < n; j++) {
        if (placed[i].track === placed[j].track) continue;

        const ax = placed[i].pillCenterX + (placed[i].offsetP || 0);
        const bx = placed[j].pillCenterX + (placed[j].offsetP || 0);
        if (Math.abs(ax - bx) >= MIN_SEP) continue;

        const aTop = placed[i].channelYRel;
        const aBot = topPad + trackOffsets[placed[i].track];
        const bTop = placed[j].channelYRel;
        const bBot = topPad + trackOffsets[placed[j].track];
        const loY = Math.max(aTop, bTop);
        const hiY = Math.min(aBot, bBot);
        if (hiY - loY < MIN_Y_OVERLAP) continue;

        const dx = bx - ax;
        const sgn = dx >= 0 ? 1 : -1;
        const push = (MIN_SEP - Math.abs(dx)) / 2 + 0.5;

        const aSlack = _descentSlack(placed, i, "left")
                     + _descentSlack(placed, i, "right");
        const bSlack = _descentSlack(placed, j, "left")
                     + _descentSlack(placed, j, "right");

        if (aSlack >= bSlack) {
          placed[i].offsetP = (placed[i].offsetP || 0) - sgn * push;
        } else {
          placed[j].offsetP = (placed[j].offsetP || 0) + sgn * push;
        }
        anyMoved = true;
      }
    }
    if (!anyMoved) break;
  }
}

/* ==========================================================================
   POLISH LOOP
   ========================================================================== */

export function polishLeaderLayout(placed, stripH, topPad, trackOffsets,
                                    boundaries, wireSegments, wallEdges) {
  const MAX_ROUNDS = 6;

  const obstacleContext = {
    boundaries:   boundaries   || [],
    wireSegments: wireSegments || [],
    wallEdges:    wallEdges    || [],
    placed:       placed,
    stripH:       stripH,
  };

  const snap = () => placed.map(p => ({
    bevel0: p.bevel0 || 0, bevel1: p.bevel1 || 0,
    jog0:   p.jog0   || 0, jog1:   p.jog1   || 0,
    diveMode: p.diveMode || 0,
    channelYRel: p.channelYRel,
    offsetA:     p.offsetA || 0,
    offsetP:     p.offsetP || 0,
    detourBias:  p.detourBias || 0,
    pillCenterX: p.pillCenterX,
  }));
  const restore = (s) => {
    for (let i = 0; i < placed.length; i++) {
      const p = placed[i], t = s[i];
      p.bevel0 = t.bevel0; p.bevel1 = t.bevel1;
      p.jog0   = t.jog0;   p.jog1   = t.jog1;
      p.diveMode = t.diveMode;
      p.channelYRel = t.channelYRel;
      p.offsetA = t.offsetA;
      p.offsetP = t.offsetP;
      p.detourBias = t.detourBias;
      p.pillCenterX = t.pillCenterX;
    }
  };

  const count = () => {
    const layout = _buildLayout(placed, stripH, topPad, trackOffsets);
    let n = 0;
    for (let i = 0; i < placed.length; i++) {
      n += _countConflictsAt(i, layout, obstacleContext);
    }
    return Math.floor(n / 2);
  };

  let prev = count();
  if (prev === 0) return;
  let bestSnap  = snap();
  let bestScore = prev;

  for (let round = 0; round < MAX_ROUNDS; round++) {
    optimizeLeaderGeometry(placed, stripH, topPad, trackOffsets,
                           boundaries, wireSegments, wallEdges);

    rerouteDoubleCrossingsByHugging(placed, stripH, topPad, trackOffsets,
                                    boundaries, wireSegments, wallEdges);

    pushPillsForLeaderConflicts(placed, stripH, topPad, trackOffsets,
                                obstacleContext);

    _separateCrossTrackDescents(placed, stripH, topPad, trackOffsets);

    const now = count();
    if (now < bestScore) {
      bestScore = now;
      bestSnap  = snap();
    }
    if (now >= prev) break;
    prev = now;
    if (now === 0) break;
  }

  restore(bestSnap);
}
"""
