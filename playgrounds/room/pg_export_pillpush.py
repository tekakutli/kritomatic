"""
pg_export_pillpush.py — the pill push, the double-crossing hug pass,
and the polish loop.

Fallback status
---------------
As of the Rust leader-optimizer port, the functions in this module
are the JS-side fallback.  On a server whose `leader_optimizer`
binary is present and resolvable (see cable_server.RUST_BIN), the
two passes below — pushPillsForLeaderConflicts and
rerouteDoubleCrossingsByHugging — and the polish loop that drives
them, polishLeaderLayout, are invoked only via pg_export_rust's
catch block when the /optimize-leaders round trip fails.  The Rust
port is a transcription of exactly this code; nothing here has been
changed by the port, and the two implementations are intended to
stay behaviourally identical.  If you fix a bug in one, fix it in
the other.

When the optimiser's six move families have all been exhausted and a
leader is still conflicted, the pill is the movable object.

Three cases are covered by the pill push:

    1.  The acting leader's OWN pill is nudged, small horizontal
        steps, accepted only when the acting leader's conflict count
        drops.

    2.  Every foreign pill whose OWNER is in conflict with the acting
        leader is nudged.  "In conflict" means segment-segment OR
        segment-pillbox OR boundary / wall-edge / wire proximity.

    3.  For pairs of leaders whose polylines cross 2 or more times, a
        coordinated move is tried: all combinations of small shifts on
        BOTH pills of the pair, scored by the TOTAL layout conflict
        count.  This is the fix for double crossings that survive the
        single-pill pass — moving one pill alone breaks the pair's
        crossings but creates a new conflict for the same leader
        elsewhere, so the single-move rule rejects it; a joint move
        can avoid both.

A same-track pill-collision guard
---------------------------------
Two pills in the same track — the same horizontal row — must not
overlap.  Two pills in DIFFERENT tracks are at different y and can
safely overlap horizontally; that is what the track system is for.

The pill push respects this with a hard gate: before any candidate
position is scored, the pill is checked against every other pill in
its own track.  If the moved pill would come within PILL_PROX of a
same-track neighbour, that candidate is skipped.  A one-time
_separatePillsInTracks pre-pass at the top of the push runs first
and pushes any already-too-close pairs apart, left to right per
track.

Threading the obstacle context
------------------------------
The push receives the same obstacleContext polishLeaderLayout
builds: boundaries, wireSegments, wallEdges, placed, and stripH.
It passes that context into every _countConflictsAt call so the
counter counts boundary, wall-edge, and wire conflicts as well as
the segment-segment and segment-pill ones.

Double-crossing hug pass
------------------------
When two leaders cross more than once, the optimiser's six move
families and its two coordinated pair passes (2-way and 3-way) can
stall: the 2-way pass does not try diveMode, and the 3-way pass
requires both diveMode values to change, so a "flip exactly one
leader's diveMode" fix slips through.

rerouteDoubleCrossingsByHugging handles that case directly.  For
every pair whose paths properly cross twice or more, it tries a
small set of discrete "hug" reroutes on one of the two leaders —
channel above/below the other's, diveMode 2 (horizontal rides up to
the leader's own anchorY and the descent drops at its own pill
column), and a descent-column shift just outside the other's column
range at HUG_GAP.  These mirror the detour idiom _buildChannelToPill
already applies around pill boxes.

Each candidate is accepted only when it both (a) drops the pair's
own proper crossing count below 2 and (b) strictly reduces the total
layout conflict count — so the pass can never make the drawing worse,
only resolve an otherwise-stuck pair.

The pass runs inside polishLeaderLayout between
optimizeLeaderGeometry and pushPillsForLeaderConflicts, once per
round, so a hug-cleaned layout is what the pill push then refines.

Cross-track descent separation
------------------------------
The pill push nudges the pill horizontally, but the tail's descent
column has always been hard-wired to the pill's centre — so two
pills on different tracks whose x happens to land within
SEG_MIN_SEP of each other produce two parallel verticals that
read as one thick stroke.  offsetP decouples the descent column
from the pill's centre; separateCrossTrackDescents nudges offsetP
until the two descents are separated, preferring to move whichever
pill has more horizontal room to spare on its own track.

Runs after the pill push each polish round, so it sees the final
pill positions.  Same-track pairs are skipped — their descents go
to the same row and are already separated by the same-track pill
guard in _separatePillsInTracks.

Best-state preservation in the polish loop
------------------------------------------
Each round of the polish loop starts with optimizeLeaderGeometry,
whose first action is to reset bevel, jog, and diveMode to 0 on
every leader before it begins trying candidate values.  That reset
is correct inside one optimiser invocation, but it means a polish
round can leave the layout in a worse state than the round before
it if the new round's search fails to find as good a configuration.

polishLeaderLayout therefore snapshots the full optimiser state
after every round and restores the best-scoring snapshot when the
loop ends.  Without this, a round that finds no improvement over
the previous round could still cost the layout a previously-found
fix.
"""


PILLPUSH_JS = r"""
/* ==========================================================================
   PILL PUSH PASS
   ========================================================================== */

function buildPathSegsFor(it, placed, stripH, topPad, trackOffsets) {
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

function pushPillsForLeaderConflicts(placed, stripH, topPad, trackOffsets,
                                     obstacleContext) {
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
              if (sa.maxX + SEG_MIN_SEP < sb.minX) continue;
              if (sb.maxX + SEG_MIN_SEP < sa.minX) continue;
              if (sa.maxY + SEG_MIN_SEP < sb.minY) continue;
              if (sb.maxY + SEG_MIN_SEP < sa.minY) continue;
              const d = _segSegDist(sa.ax, sa.ay, sa.bx, sa.by,
                                    sb.ax, sb.ay, sb.bx, sb.by);
              if (d < SEG_MIN_SEP) { cflt = true; break; }
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

  /* ==========================================================================
     COORDINATED PILL PAIR MOVES
     ==========================================================================

     For pairs of leaders whose polylines cross more than once, no
     single pill move can always fix the pair — moving A alone often
     breaks a conflict with a third leader, and moving B alone breaks
     a different conflict.  This pass tries all combinations of small
     shifts on BOTH pills of a double-crossing pair and scores each
     combination by the TOTAL conflict count across the whole layout,
     not just the pair's own.  This is stricter than the earlier
     version, which only measured A + B's conflicts and could
     therefore accept a shift that fixed the pair while introducing
     two conflicts elsewhere. */
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
        const xc = _countSegmentCrossings(layout0.segs[i], layout0.segs[j]);
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

/* ==========================================================================
   DOUBLE-CROSSING HUG PASS
   ==========================================================================

   When two leaders cross more than once, the current optimiser stalls:
   the 2-way coordinated pass does not try diveMode, and the 3-way pass
   only accepts candidate pairs where BOTH diveMode values differ from
   their current values.  A pair like V5 × V6 — where flipping a single
   leader's diveMode from 0 to 2 resolves both crossings — falls through
   both.

   This pass handles that case directly.  For each pair whose paths
   properly cross twice or more, it tries a small set of "hug"
   reroutes on ONE of the two leaders, mirroring the detour idiom
   _buildChannelToPill already uses around pill boxes:

     • hug above the other's channel   (A.channelYRel = B.channelYRel − HUG_GAP)
     • hug below the other's channel   (A.channelYRel = B.channelYRel + HUG_GAP)
     • diveMode 2                       (A's horizontal moves up to A's
                                          own anchorY, A's descent moves
                                          to A's own pill column — this is
                                          the "hug the pill column" move)
     • hug the other's descent columns  (A's descent column shifts to just
                                          outside B's column range, at
                                          HUG_GAP to the near side)

   Every candidate is only accepted when:

     • the pair's own proper crossing count drops below 2, AND
     • the layout's TOTAL conflict count strictly improves.

   The pass runs after optimizeLeaderGeometry in each polish round, so
   it composes with (and never undoes) the optimiser's work. */
function rerouteDoubleCrossingsByHugging(placed, stripH, topPad, trackOffsets,
                                         boundaries, wireSegments, wallEdges) {
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

  /* Find every double-crossing pair. */
  const pairs = [];
  for (let i = 0; i < placed.length; i++) {
    for (let j = i + 1; j < placed.length; j++) {
      const xc = _countSegmentCrossings(layout.segs[i], layout.segs[j]);
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
    /* Re-build the layout for the pair and re-verify the double crossing
       is still present — an earlier pair in this pass may have changed
       one of these leaders. */
    layout = _buildLayout(placed, stripH, topPad, trackOffsets);
    if (_countSegmentCrossings(layout.segs[p.i], layout.segs[p.j]) < 2) {
      continue;
    }

    const totalBefore = totalConflicts(layout);
    let bestTotal = totalBefore;
    let bestMove  = null;

    /* Try both directions: A hugging B, and B hugging A. */
    for (const [idxSelf, idxOther] of [[p.i, p.j], [p.j, p.i]]) {
      const A = placed[idxSelf];
      const B = placed[idxOther];

      const aSave = {
        diveMode:    A.diveMode || 0,
        channelYRel: A.channelYRel,
        offsetA:     A.offsetA || 0,
      };

      /* --- Analyse B's structure ---------------------------------- */
      /* B's channel Y in strip-local coords (relative to stripH). */
      const bChanY = B.channelYRel || 0;

      /* B's structural columns: the x-positions of B's verticals.
         A diveMode 2 leader has only its pill column; other modes
         also carry a column at anchorCx + offsetA. */
      const bCols = [B.pillCenterX];
      if ((B.diveMode || 0) !== 2) {
        bCols.push(B.anchorCx + (B.offsetA || 0));
      }
      const bColMin = Math.min(...bCols);
      const bColMax = Math.max(...bCols);

      /* --- Build candidate moves for A ---------------------------- */
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

      /* Dive mode 2: A's horizontal rides up to A's own anchorY and
         A's descent drops at A's own pill column.  This is the move
         that resolves V5 × V6 — it clears both crossings at once by
         pulling A's structure above and to the side of B's. */
      candidates.push({ diveMode: 2, channelYRel: aSave.channelYRel,
                        offsetA: aSave.offsetA });

      /* Hug the other leader's descent column: shift A's descent
         column to just outside B's column range.  Same idiom the
         pill-box detour uses (qL − M / qR + M). */
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

      /* --- Test each candidate ------------------------------------ */
      for (const c of candidates) {
        /* Skip no-ops. */
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
        const newXc = _countSegmentCrossings(newLayout.segs[idxSelf],
                                              newLayout.segs[idxOther]);

        /* The move must actually resolve the pair's double crossing,
           not just nudge it. */
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
   ==========================================================================
   See the module docstring for the reasoning.  In one sentence: two
   tails on different tracks whose descent columns land within
   SEG_MIN_SEP of each other read as one thick stroke; nudge one
   leader's offsetP — the horizontal distance between its descent
   column and its pill's centre — until they separate, preferring the
   leader with more room on its own track. */

function _descentSlack(placed, idx, side) {
  const it = placed[idx];
  const myL = it.pillCenterX - it.w / 2;
  const myR = it.pillCenterX + it.w / 2;
  const myTrack = it.track;
  let nearestLeft  = -Infinity;
  let nearestRight = Infinity;
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

function _separateCrossTrackDescents(placed, stripH, topPad, trackOffsets) {
  const MIN_SEP       = SEG_MIN_SEP;
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

        /* Strip-relative y ranges of the two descents: from each
           leader's channel down to the top of its pill. */
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

function polishLeaderLayout(placed, stripH, topPad, trackOffsets,
                            boundaries, wireSegments, wallEdges) {
  const MAX_ROUNDS = 6;

  const obstacleContext = {
    boundaries:   boundaries   || [],
    wireSegments: wireSegments || [],
    wallEdges:    wallEdges    || [],
    placed:       placed,
    stripH:       stripH,
  };

  /* Snapshot a full optimiser state.  deepClone covers the fields the
     optimiser mutates: bevel, jog, diveMode, channelYRel, offsetA,
     offsetP, detourBias, and pillCenterX.  Everything else —
     anchorCx, w, h, track — is immutable during the polish loop. */
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
  let bestSnap = snap();
  let bestScore = prev;

  for (let round = 0; round < MAX_ROUNDS; round++) {
    optimizeLeaderGeometry(placed, stripH, topPad, trackOffsets,
                           boundaries, wireSegments, wallEdges);

    /* Resolve any remaining double crossings by hugging one leader
       around the other.  Runs after the optimiser (so it composes with,
       rather than fights, the optimiser's six move families) and before
       the pill push (so the pill push sees the hug-cleaned layout). */
    rerouteDoubleCrossingsByHugging(placed, stripH, topPad, trackOffsets,
                                    boundaries, wireSegments, wallEdges);

    pushPillsForLeaderConflicts(placed, stripH, topPad, trackOffsets,
                                obstacleContext);

    /* Runs last so it sees the final pill x positions. */
    _separateCrossTrackDescents(placed, stripH, topPad, trackOffsets);

    const now = count();
    if (now < bestScore) {
      bestScore = now;
      bestSnap = snap();
    }
    if (now >= prev) break;
    prev = now;
    if (now === 0) break;
  }

  /* Restore the best-scoring state across all rounds.  Without this,
     a round that resets diveMode / bevel / jog to 0 at the start of
     optimizeLeaderGeometry would discard the previous round's gains
     whenever the new round failed to improve on them. */
  restore(bestSnap);
}
"""
