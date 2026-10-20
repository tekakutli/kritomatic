"""
pg_export_linearize.py — anchor graph → renderable data.

Everything that turns the room model's anchors and cables into the
flat lists the renderer walks: the linearized true-cable path, the
run groupings (floor-run vs. wall-run), the wall-run chunk layout,
the segment directory, and the per-pill JSON metadata.

Also here:

    • the collinear-anchor filter and its toggle flag — the checkbox
      in the preview page flips window.filterCollinearVerticesInStrip
      and this module's _collapseStripCollinear is what reads it

    • buildCableRunMeta — the schema-6 JSON producer.  It walks the
      linearized path once and emits the walls list, the per-anchor
      metadata, the pills list, and the pillRows fill-ratio table.

Nothing here draws.  The output of this module is data.
"""


LINEARIZE_JS = r"""
/* ==========================================================================
   COLLINEAR VERTEX FILTER TOGGLE
   ========================================================================== */

window.filterCollinearVerticesInStrip = false;

/* Collinear-anchor filter.

   Drops the middle anchor of any three consecutive wall-edge anchors
   that sit on the same wall segment, at the same height, and on a
   straight line through their two neighbours.

   The same-segment check matters.  The room model can represent one
   physical wall as several segments — a column flush against it, an
   opening that punches through, a step riser that splits the stretch —
   and the boundary between those segments is a real vertex a cable can
   cross.  A cable running along a wall past the point where the wall
   was split must still show the vertex there.  Keeping the vertex also
   keeps the pill's two side-distance readings attached to the wall
   they were measured along. */
function _collapseStripCollinear(orderedIds) {
  if (!window.filterCollinearVerticesInStrip) return orderedIds.slice();
  if (orderedIds.length < 3) return orderedIds.slice();

  const out = [orderedIds[0]];
  for (let i = 1; i < orderedIds.length - 1; i++) {
    const prevId = out[out.length - 1];
    const curId  = orderedIds[i];
    const nextId = orderedIds[i + 1];
    const pa = anchors.get(prevId);
    const ca = anchors.get(curId);
    const na = anchors.get(nextId);
    if (!pa || !ca || !na ||
        pa.space !== "wall-edge" ||
        ca.space !== "wall-edge" ||
        na.space !== "wall-edge" ||
        pa.segIdx !== ca.segIdx ||
        ca.segIdx !== na.segIdx) {
      out.push(curId);
      continue;
    }
    const pu = wallAttachToU(pa.segIdx, pa.t), pv = pa.v || 0;
    const cu = wallAttachToU(ca.segIdx, ca.t), cv = ca.v || 0;
    const nu = wallAttachToU(na.segIdx, na.t), nv = na.v || 0;
    const dx1 = cu - pu, dy1 = cv - pv;
    const dx2 = nu - cu, dy2 = nv - cv;
    const cross = Math.abs(dx1 * dy2 - dy1 * dx2);
    const len = Math.hypot(dx2, dy2) || 1;
    const dist = cross / len;
    if (dist < 2.0) {
      /* skip curId */
    } else {
      out.push(curId);
    }
  }
  out.push(orderedIds[orderedIds.length - 1]);
  return out;
}

/* ==========================================================================
   CABLE RUN EXPORT
   ========================================================================== */

/* ---- Linearization ---- */

function computeAnchor3D(a) {
  if (!a) return null;
  if (a.space === "floor") return [a.x, a.y, 0];
  if (a.space === "wall-edge") {
    const p = wallAttachToPlan(a.segIdx, a.t);
    if (!p) return null;
    return [p[0], p[1], a.v || 0];
  }
  return null;
}

function totalCableLength(orderedIds) {
  let total = 0;
  for (let i = 0; i < orderedIds.length - 1; i++) {
    const p = computeAnchor3D(anchors.get(orderedIds[i]));
    const q = computeAnchor3D(anchors.get(orderedIds[i + 1]));
    if (!p || !q) continue;
    total += Math.hypot(p[0] - q[0], p[1] - q[1], p[2] - q[2]);
  }
  return total;
}

function linearizeTrueCable(tcId) {
  const parts = allCables().filter(c => trueCableIdOf(c) === tcId);
  if (!parts.length) return [];

  const anchorIdSet = new Set();
  for (const c of parts) for (const id of c.anchorIds) anchorIdSet.add(id);

  const adj = new Map();
  for (const id of anchorIdSet) adj.set(id, new Set());
  for (const c of parts) {
    for (let i = 0; i < c.anchorIds.length - 1; i++) {
      const a = c.anchorIds[i], b = c.anchorIds[i + 1];
      adj.get(a).add(b);
      adj.get(b).add(a);
    }
  }
  for (const j of JUNCTIONS) {
    const here = [];
    for (const t of j.terminals) {
      for (const aid of anchorIdSet) {
        const a = anchors.get(aid);
        if (!a || a.space !== "wall-edge") continue;
        if (a.segIdx !== t.segIdx) continue;
        const side = Math.abs(a.t) < 0.01 ? 0
                   : Math.abs(a.t - 1) < 0.01 ? 1 : -1;
        if (side !== t.side) continue;
        here.push(a);
      }
    }
    for (let i = 0; i < here.length; i++) {
      for (let k = i + 1; k < here.length; k++) {
        if (Math.abs((here[i].v || 0) - (here[k].v || 0)) > 5.0) continue;
        adj.get(here[i].id).add(here[k].id);
        adj.get(here[k].id).add(here[i].id);
      }
    }
  }

  const endpoints = [];
  for (const [id, nbrs] of adj) if (nbrs.size === 1) endpoints.push(id);
  if (endpoints.length < 2) return Array.from(anchorIdSet);

  const visited = new Set();
  const path = [];
  let cur = endpoints[0];
  while (cur != null) {
    path.push(cur);
    visited.add(cur);
    let next = null;
    for (const n of adj.get(cur)) {
      if (!visited.has(n)) { next = n; break; }
    }
    cur = next;
  }
  return path;
}

function isFloorLevel(a) {
  if (!a) return false;
  if (a.space === "floor") return true;
  return Math.abs(a.v || 0) < 1e-3;
}

function groupCableRuns(orderedIds) {
  const runs = [];
  let cur = null;
  for (const id of orderedIds) {
    const a = anchors.get(id);
    if (!a) continue;
    const space = a.space === "floor" ? "floor" : "wall-edge";
    if (!cur || cur.space !== space) {
      cur = { space, ids: [] };
      runs.push(cur);
    }
    cur.ids.push(id);
  }
  return runs;
}

function groupPlanRuns(orderedIds) {
  const runs = [];
  let cur = null;
  for (const id of orderedIds) {
    const a = anchors.get(id);
    if (!a || !isFloorLevel(a)) { cur = null; continue; }
    if (!cur) { cur = { ids: [] }; runs.push(cur); }
    cur.ids.push(id);
  }
  return runs;
}

function buildSegmentDirectory(orderedIds) {
  const dir = new Map();
  let next = 1;
  for (const id of orderedIds) {
    const a = anchors.get(id);
    if (!a || a.space !== "wall-edge") continue;
    if (dir.has(a.segIdx)) continue;
    const s = WALL.segments[a.segIdx];
    dir.set(a.segIdx, {
      order: next,
      tag:   (s && s.tag) ? s.tag : ("seg " + (a.segIdx + 1)),
    });
    next++;
  }
  return dir;
}

function layoutWallRun(ids) {
  const chunks = [];
  let cur = null;
  for (const id of ids) {
    const a = anchors.get(id);
    if (!a || a.space !== "wall-edge") continue;
    const s = WALL.segments[a.segIdx];
    if (!s) continue;
    if (!cur || cur.segIdx !== a.segIdx) {
      cur = {
        segIdx:  a.segIdx,
        len:     s.len,
        z_lo:    (typeof s.z_lo === "number") ? s.z_lo : 0,
        z_hi:    (typeof s.z_hi === "number") ? s.z_hi : WALL_HEIGHT,
        kind:    s.kind || "wall",
        anchors: [],
      };
      chunks.push(cur);
    }
    cur.anchors.push({ aid: id, localU: a.t * s.len, v: a.v || 0 });
  }
  for (const ch of chunks) {
    const first = ch.anchors[0];
    const last  = ch.anchors[ch.anchors.length - 1];
    ch.mirror = first.localU > last.localU;
  }
  let totalLen = 0;
  for (const c of chunks) totalLen += c.len;
  return { chunks, totalLen };
}

function segmentKind(aid, bid, parts) {
  for (const c of parts) {
    for (let i = 0; i < c.anchorIds.length - 1; i++) {
      const a = c.anchorIds[i], b = c.anchorIds[i + 1];
      if ((a === aid && b === bid) || (a === bid && b === aid)) {
        return state.floorCables.includes(c) ? "floor" : "wall";
      }
    }
  }
  return null;
}

function buildCableRunMeta(tcId, orderedIds, segDirectory, chunks,
                            labelPlacement, stripOffsetX, stripW,
                            stripTotalMM) {
  const pillNameOf = (labelPlacement && labelPlacement.pillNameOf)
                     ? labelPlacement.pillNameOf
                     : new Map();

  const _r2 = (v) => Math.round(v * 100) / 100;
  const _r3 = (v) => Math.round(v * 1000) / 1000;

  const pxPerMm = (stripW > 0 && stripTotalMM > 0)
    ? stripW / stripTotalMM
    : 0;

  const parts = allCables().filter(c => trueCableIdOf(c) === tcId);

  const walls = [];
  for (const [segIdx, entry] of segDirectory) {
    const s = WALL.segments[segIdx];
    walls.push({
      badge:     entry.order,
      segIdx:    segIdx,
      tag:       entry.tag,
      length_mm: s ? s.len : 0,
      z_lo_mm:   s ? ((typeof s.z_lo === "number") ? s.z_lo : 0) : 0,
      z_hi_mm:   s ? ((typeof s.z_hi === "number") ? s.z_hi : WALL_HEIGHT) : 0,
      kind:      s ? (s.kind || "wall") : "wall",
    });
  }
  walls.sort((a, b) => a.badge - b.badge);

  let wallLen = 0, floorLen = 0;
  for (let i = 0; i < orderedIds.length - 1; i++) {
    const aid = orderedIds[i], bid = orderedIds[i + 1];
    const p = computeAnchor3D(anchors.get(aid));
    const q = computeAnchor3D(anchors.get(bid));
    if (!p || !q) continue;
    const d = Math.hypot(p[0] - q[0], p[1] - q[1], p[2] - q[2]);
    if (d < 1e-3) continue;
    const kind = segmentKind(aid, bid, parts);
    if (kind === "floor")      floorLen += d;
    else if (kind === "wall")  wallLen += d;
  }

  const anchorLookup = new Map();
  for (const ch of chunks) {
    for (const a of ch.anchors) {
      anchorLookup.set(a.aid, {
        mirror:  ch.mirror,
        len:     ch.len,
        nativeU: a.nativeU,
      });
    }
  }

  const anchorsMeta = [];
  for (let i = 0; i < orderedIds.length; i++) {
    const id = orderedIds[i];
    const a = anchors.get(id);
    if (!a) continue;
    const e = { index: i, anchorId: id, space: a.space };

    if (pillNameOf) {
      const pn = pillNameOf.get(id);
      if (pn) e.pillName = pn;
    }

    if (a.space === "floor") {
      e.x_mm = a.x;
      e.y_mm = a.y;
    } else if (a.space === "wall-edge") {
      const segEntry = segDirectory.get(a.segIdx);
      const seg      = WALL.segments[a.segIdx];
      e.segIdx     = a.segIdx;
      e.segmentTag = segEntry ? segEntry.tag   : null;
      e.badge      = segEntry ? segEntry.order : null;
      e.t          = a.t;
      e.height_mm  = a.v || 0;
      e.segment_z_lo_mm = seg ? ((typeof seg.z_lo === "number") ? seg.z_lo : 0) : 0;
      e.segment_z_hi_mm = seg ? ((typeof seg.z_hi === "number") ? seg.z_hi : WALL_HEIGHT) : 0;
      e.segment_kind    = seg ? (seg.kind || "wall") : "wall";

      const info = anchorLookup.get(id);
      if (info) {
        e.distance_west_mm = info.mirror
          ? (info.len - info.nativeU)
          : info.nativeU;
        e.distance_east_mm = info.mirror
          ? info.nativeU
          : (info.len - info.nativeU);
        e.mirrored               = info.mirror;
        e.distance_from_a_end_mm = info.nativeU;
        e.distance_from_b_end_mm = info.len - info.nativeU;
      }

      const p = wallAttachToPlan(a.segIdx, a.t);
      if (p) {
        e.world_x_mm = p[0];
        e.world_y_mm = p[1];
      }
    }
    anchorsMeta.push(e);
  }

  const totalLen = totalCableLength(orderedIds);

  const pills = [];
  const pillRows = [];
  if (labelPlacement && Array.isArray(labelPlacement.placed)) {
    const areaX0 = labelPlacement.areaX0;
    const areaX1 = labelPlacement.areaX1;
    const usableWidth =
      (typeof areaX0 === "number" && typeof areaX1 === "number")
        ? areaX1 - areaX0
        : null;

    for (const it of labelPlacement.placed) {
      const members = Array.isArray(it.members) ? it.members.slice() : [];
      const memberIndices = members
        .map(id => orderedIds.indexOf(id))
        .filter(i => i >= 0);

      pills.push({
        name: it.name || null,
        track: it.track,

        x_logical:           _r2(it.pillCenterX),
        anchorX_logical:     _r2(it.anchorCx),
        displacement_logical:
          _r2(Math.abs(it.pillCenterX - it.anchorCx)),

        x_mm: (pxPerMm > 0 && typeof stripOffsetX === "number")
          ? _r2((it.pillCenterX - stripOffsetX) / pxPerMm) : null,
        anchorX_mm: (pxPerMm > 0 && typeof stripOffsetX === "number")
          ? _r2((it.anchorCx - stripOffsetX) / pxPerMm) : null,

        width_logical:  _r2(it.w),
        height_logical: _r2(it.h),

        memberAnchorIds:     members,
        memberAnchorIndices: memberIndices,

        channelYRel: (typeof it.channelYRel === "number")
                     ? _r2(it.channelYRel) : null,
        offsetA:    _r2(it.offsetA    || 0),
        detourBias: _r2(it.detourBias || 0),
        diveMode: it.diveMode || 0,
        bevel0:   _r2(it.bevel0 || 0),
        bevel1:   _r2(it.bevel1 || 0),
        jog0:     _r2(it.jog0   || 0),
        jog1:     _r2(it.jog1   || 0),

        leaderStyle: (it.leaderStyle === 1) ? "dashed" : "solid",

        lines: it.lines.map(l => ({
          left:  l.left  || "",
          right: l.right || null,
        })),
      });
    }

    const trackMap = new Map();
    for (const it of labelPlacement.placed) {
      if (!trackMap.has(it.track)) trackMap.set(it.track, []);
      trackMap.get(it.track).push(it);
    }
    const sortedTracks = Array.from(trackMap.keys()).sort((a, b) => a - b);
    for (const t of sortedTracks) {
      const row = trackMap.get(t);
      let xMin = Infinity, xMax = -Infinity;
      for (const it of row) {
        const left  = it.pillCenterX - it.w / 2;
        const right = it.pillCenterX + it.w / 2;
        if (left  < xMin) xMin = left;
        if (right > xMax) xMax = right;
      }
      const span = xMax - xMin;
      pillRows.push({
        track:         t,
        count:         row.length,
        xMin_logical:  _r2(xMin),
        xMax_logical:  _r2(xMax),
        span_logical:  _r2(span),
        fillRatio: (usableWidth && usableWidth > 0)
          ? _r3(span / usableWidth) : null,
        pillNames: row.map(it => it.name || "(anon)"),
      });
    }
  }

  return {
    schemaVersion:  6,
    cableId:        tcId,
    anchorCount:    orderedIds.length,
    partCount:      parts.length,
    totalLength_mm: totalLen,
    wallLength_mm:  wallLen,
    floorLength_mm: floorLen,

    stripScale_px_per_mm: _r3(pxPerMm),

    walls:          walls,
    anchors:        anchorsMeta,

    pills:          pills,
    pillRows:       pillRows,
  };
}
"""
