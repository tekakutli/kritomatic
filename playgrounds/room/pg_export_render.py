"""
pg_export_render.py — the main renderer.

cropCanvasToContent trims the white margin, scaleCanvasToWidth
resamples to a target width, and renderCableRunToCanvas does the
whole sheet — the title, the strip, the vertex labels and leaders,
the plan view with its room-interior hatch and traversed-wall bars,
the info column with the length breakdown and the legend, the
wall-segment arrow-style table, and the dotted cross-view wires.

The layout constants (IMG_W, MARGIN, STRIP_FIXED_H, etc.) all live
inside renderCableRunToCanvas — they describe the one fixed sheet
this exporter produces and nothing else reads them.

Async boundary
--------------
computeVertexLabelPlacement in pg_export_labels.py is now an async
function: the three leader-geometry passes it used to run in-page
(refineLeaderOffsets, polishLeaderLayout, hugOverhangingLeaders)
have been ported to Rust and are invoked via POST /optimize-leaders.
This file therefore awaits that function, which makes
renderCableRunToCanvas itself async.

Every caller of renderCableRunToCanvas — exportAllCableRuns in
pg_export_entry.py and the toolbar's rerenderAll closure inside
openExportPreview in pg_export_preview.py — must await the result.
Both are edited alongside this file to do so.
"""


RENDER_JS = r"""
/* ==========================================================================
   TEXT WRAPPING HELPER
   ==========================================================================
   Splits a string on whitespace and packs the words into lines no
   wider than maxW, measured with the current c.font.  Words that
   alone exceed maxW are left on their own line (rather than broken
   mid-word); the caller is expected to shrink the font in that case
   or accept the overflow.  Used by the info column so long labels
   like "Wall segments . arrow style" or "step face (forward hatch)"
   do not run past INFO_W and paint over the plan. */

function _wrapText(c, text, maxW) {
  const words = String(text).split(/\s+/).filter(w => w.length > 0);
  if (!words.length) return [""];
  const lines = [];
  let cur = "";
  for (const w of words) {
    const test = cur ? cur + " " + w : w;
    if (c.measureText(test).width <= maxW) {
      cur = test;
    } else {
      if (cur) lines.push(cur);
      cur = w;
    }
  }
  if (cur) lines.push(cur);
  return lines;
}

/* ---- Crop and rasterise ---- */

function cropCanvasToContent(srcCanvas, pad) {
  const w = srcCanvas.width, h = srcCanvas.height;
  const srcCtx = srcCanvas.getContext("2d");
  const data = srcCtx.getImageData(0, 0, w, h).data;

  let minX = w, minY = h, maxX = -1, maxY = -1;
  for (let y = 0; y < h; y++) {
    const row = y * w * 4;
    for (let x = 0; x < w; x++) {
      const i = row + x * 4;
      if (data[i] < 250 || data[i + 1] < 250 || data[i + 2] < 250) {
        if (x < minX) minX = x;
        if (y < minY) minY = y;
        if (x > maxX) maxX = x;
        if (y > maxY) maxY = y;
      }
    }
  }
  if (maxX < 0) return srcCanvas;

  minX = Math.max(0, minX - pad);
  minY = Math.max(0, minY - pad);
  maxX = Math.min(w - 1, maxX + pad);
  maxY = Math.min(h - 1, maxY + pad);

  const cw = maxX - minX + 1;
  const ch = maxY - minY + 1;
  const out = document.createElement("canvas");
  out.width  = cw;
  out.height = ch;
  out.getContext("2d").drawImage(srcCanvas, minX, minY, cw, ch, 0, 0, cw, ch);
  return out;
}

function scaleCanvasToWidth(srcCanvas, targetW) {
  const w = srcCanvas.width, h = srcCanvas.height;
  if (Math.abs(w - targetW) / targetW < 0.10) return srcCanvas;
  const scale = targetW / w;
  const out = document.createElement("canvas");
  out.width  = Math.round(w * scale);
  out.height = Math.round(h * scale);
  const octx = out.getContext("2d");
  octx.imageSmoothingEnabled = true;
  octx.imageSmoothingQuality = "high";
  octx.drawImage(srcCanvas, 0, 0, out.width, out.height);
  return out;
}

/* ---- Main renderer ----

   Async because computeVertexLabelPlacement now awaits the Rust
   leader optimiser over POST /optimize-leaders.  Every caller of
   this function must await it; see the module docstring. */
async function renderCableRunToCanvas(tcId) {
  const orderedIds = _collapseStripCollinear(linearizeTrueCable(tcId));
  if (orderedIds.length < 1) return null;

  const segDirectory = buildSegmentDirectory(orderedIds);
  const hasWalls     = segDirectory.size > 0;

  const runs     = groupCableRuns(orderedIds);
  const wallRuns = runs.filter(r => r.space === "wall-edge");
  const planRuns = groupPlanRuns(orderedIds);

  const wallLayouts  = wallRuns.map(r => layoutWallRun(r.ids));
  const stripTotalMM = wallLayouts.reduce((a, l) => a + l.totalLen, 0);
  const stripActive  = stripTotalMM > 0;
  const planActive   = planRuns.length > 0;

  const fb = GEOMETRY.bounds;
  const fw = Math.max(fb.maxX - fb.minX, 1);
  const fh = Math.max(fb.maxY - fb.minY, 1);

  const IMG_W         = 1000;
  const MARGIN        = 20;
  const TITLE_H       = 44;
  const STRIP_HEADER_H = 28;
  const STRIP_FIXED_H = 130;
  const STRIP_AXIS_W  = 52;
  const STRIP_AXIS_GAP = 8;
  const ROW_GAP_AFTER_STRIP = 20;
  const INFO_W        = 240;
  const INFO_PLAN_GAP = 26;
  const BADGE_COL_W   = 30;
  const BADGE_GAP     = 44;
  const PLAN_MAX_H    = 440;
  const RUN_GAP       = 24;
  const MIN_CHUNK_PX  = 44;
  const BADGE_STEP    = 30;

  const availW        = IMG_W - 2 * MARGIN;
  const badgeR        = 12;

  const stripAreaX0  = MARGIN + STRIP_AXIS_W + STRIP_AXIS_GAP;
  const stripAreaX1  = IMG_W - MARGIN;
  const stripAreaW   = stripAreaX1 - stripAreaX0;

  const chunkWidthsPx = [];
  let stripScale = 0, stripW = 0;
  if (stripActive) {
    const nRuns  = wallLayouts.length;
    const gapsW  = RUN_GAP * Math.max(0, nRuns - 1);
    const usable = Math.max(1, stripAreaW - gapsW);

    const lengths = [];
    for (const layout of wallLayouts)
      for (const ch of layout.chunks) lengths.push(ch.len);
    const nChunks = lengths.length;
    const sumLen  = lengths.reduce((a, b) => a + b, 0) || 1;

    if (nChunks * MIN_CHUNK_PX >= usable) {
      stripScale = usable / sumLen;
      for (const L of lengths) chunkWidthsPx.push(L * stripScale);
    } else {
      let lo = 0;
      let hi = usable / sumLen;
      for (let iter = 0; iter < 40; iter++) {
        const mid = (lo + hi) / 2;
        let total = 0;
        for (const L of lengths) total += Math.max(L * mid, MIN_CHUNK_PX);
        if (total > usable) hi = mid; else lo = mid;
      }
      stripScale = (lo + hi) / 2;
      for (const L of lengths) {
        chunkWidthsPx.push(Math.max(L * stripScale, MIN_CHUNK_PX));
      }
    }
    stripW = chunkWidthsPx.reduce((a, b) => a + b, 0) + gapsW;
  }

  const stripOffsetX = stripActive
    ? stripAreaX0 + (stripAreaW - stripW) / 2
    : 0;

  const chunks = [];
  if (stripActive) {
    let xAbs = 0;
    let chunkIdx = 0;
    for (let ri = 0; ri < wallLayouts.length; ri++) {
      const layout = wallLayouts[ri];
      for (const ch of layout.chunks) {
        const wPx = chunkWidthsPx[chunkIdx++];
        const localScale = wPx / ch.len;
        const x0 = xAbs;
        const x1 = xAbs + wPx;
        chunks.push({
          runIdx: ri, segIdx: ch.segIdx,
          x0, x1,
          mirror: ch.mirror,
          len:    ch.len,
          z_lo:   ch.z_lo,
          z_hi:   ch.z_hi,
          kind:   ch.kind,
          anchors: ch.anchors.map(a => {
            const visualU = ch.mirror ? (ch.len - a.localU) : a.localU;
            return {
              aid: a.aid,
              xPx: x0 + visualU * localScale,
              yPx: (1 - a.v / WALL_HEIGHT) * STRIP_FIXED_H,
              nativeU: a.localU,
            };
          }),
        });
        xAbs = x1;
      }
      xAbs += RUN_GAP;
    }
  }

  let labelPlacement = { placed: [], maxTrack: -1, topPad: 0,
                         trackOffsets: [], trackHeights: [],
                         pillNameOf: new Map() };
  if (stripActive) {
    const measureCtx = document.createElement("canvas").getContext("2d");
    /* Async: the leader-geometry optimiser inside this call is now
       the Rust subroutine.  See pg_export_labels.py and
       pg_export_rust.py. */
    labelPlacement = await computeVertexLabelPlacement(
      measureCtx, chunks, stripOffsetX, stripAreaX0, stripAreaW, STRIP_FIXED_H,
      orderedIds);
  }
  const totalTrackH = labelPlacement.trackHeights
    ? labelPlacement.trackHeights.reduce((a, b) => a + b, 0)
    : 0;
  const labelBandH = labelPlacement.maxTrack < 0
    ? 0
    : labelPlacement.topPad + totalTrackH + LABEL_BOTTOM_PAD;

  const planRoomAvailW = availW - INFO_W - INFO_PLAN_GAP - BADGE_GAP - BADGE_COL_W;
  let planScale = 0, planW = 0, planH = 0;
  if (planActive) {
    planScale = Math.min(planRoomAvailW / fw, PLAN_MAX_H / fh);
    planW = fw * planScale;
    planH = fh * planScale;
  }

  const INFO_HEADER_H = 22;
  const INFO_ROW_H    = 30;
  const INFO_GAP      = 16;
  const WALL_ROW_H    = 28;
  const nWalls        = segDirectory.size;

  const BREAKDOWN_H   = INFO_HEADER_H + 3 * INFO_ROW_H;
  const LEGEND_H      = INFO_HEADER_H + 5 * INFO_ROW_H;
  const WALLS_H       = hasWalls
    ? INFO_HEADER_H + nWalls * WALL_ROW_H
    : 0;

  const infoH = BREAKDOWN_H
              + INFO_GAP
              + LEGEND_H
              + INFO_GAP
              + WALLS_H;

  const badgeStackH = nWalls > 0 ? (nWalls - 1) * BADGE_STEP + 2 * badgeR : 0;

  const planRegionH = Math.max(
    planH + 2 * badgeR + 24,
    infoH + 40,
    badgeStackH + 20
  );

  let y = MARGIN + TITLE_H;
  const stripHeaderY = y;
  if (stripActive) y += STRIP_HEADER_H;
  const stripY = y;
  if (stripActive) y += STRIP_FIXED_H;
  if (stripActive) y += labelBandH;
  if (stripActive) y += ROW_GAP_AFTER_STRIP;
  const planRegionY = y;
  if (planActive || hasWalls) y += planRegionH;
  const imgH = y + 2 * MARGIN;

  const DPR = 3;
  const cv = document.createElement("canvas");
  cv.width  = IMG_W * DPR;
  cv.height = imgH * DPR;
  const c = cv.getContext("2d");
  c.scale(DPR, DPR);
  c.fillStyle = "#ffffff";
  c.fillRect(0, 0, IMG_W, imgH);

  const fmtM = (mm) => (mm / 1000).toFixed(2) + " m";
  const totalLen = totalCableLength(orderedIds);
  const nParts = allCables().filter(x => trueCableIdOf(x) === tcId).length;

  c.font = "700 22px " + FONT_SANS;
  c.fillStyle = "#000000";
  c.textAlign = "left"; c.textBaseline = "middle";
  c.fillText(T("cableTitle") + " " + tcId, MARGIN, MARGIN + TITLE_H / 2);

  c.font = "600 13px " + FONT_MONO;
  c.fillStyle = "#333333";
  c.textAlign = "right";
  c.fillText(
    T("subtitle")(orderedIds.length, nParts, fmtM(totalLen)),
    IMG_W - MARGIN, MARGIN + TITLE_H / 2
  );
  c.textAlign = "left"; c.textBaseline = "top";

  const anchorStripPos = new Map();
  if (stripActive) {
    const yFloor  = stripY + STRIP_FIXED_H;
    const yCeil   = stripY;
    const voidX0  = stripOffsetX - 8;
    const voidX1  = stripOffsetX + stripW + 8;

    c.fillStyle = _pat(c, "void");
    c.fillRect(voidX0, yCeil, voidX1 - voidX0, yFloor - yCeil);

    c.save();
    c.strokeStyle = "rgba(0, 0, 0, 0.35)";
    c.lineWidth = 1;
    c.setLineDash([4, 4]);
    c.beginPath();
    c.moveTo(voidX0, yCeil + 0.5); c.lineTo(voidX1, yCeil + 0.5);
    c.moveTo(voidX0, yFloor - 0.5); c.lineTo(voidX1, yFloor - 0.5);
    c.stroke();
    c.setLineDash([]);
    c.restore();

    for (const ch of chunks) {
      const rx   = stripOffsetX + ch.x0;
      const rw   = ch.x1 - ch.x0;
      const yTop = stripY + (1 - ch.z_hi / WALL_HEIGHT) * STRIP_FIXED_H;
      const yBot = stripY + (1 - ch.z_lo / WALL_HEIGHT) * STRIP_FIXED_H;
      const rh   = Math.max(1, yBot - yTop);

      const isStep = (ch.kind === "step");
      c.fillStyle = isStep ? _pat(c, "step") : "#ffffff";
      c.fillRect(rx, yTop, rw, rh);
      c.strokeStyle = "#000000";
      c.lineWidth = isStep ? 1.2 : 0.8;
      c.strokeRect(rx + 0.5, yTop + 0.5, rw - 1, rh - 1);
    }

    const stripRuns = [];
    for (let ri = 0; ri < wallLayouts.length; ri++) {
      const lineAnchors = [];
      for (const ch of chunks) {
        if (ch.runIdx !== ri) continue;
        for (const a of ch.anchors) lineAnchors.push(a);
      }
      for (const a of lineAnchors) {
        anchorStripPos.set(a.aid, [stripOffsetX + a.xPx, stripY + a.yPx]);
      }
      stripRuns.push(lineAnchors);
    }

    const _strokeRun = (lineAnchors) => {
      if (lineAnchors.length < 2) return;
      c.beginPath();
      c.moveTo(stripOffsetX + lineAnchors[0].xPx,
               stripY      + lineAnchors[0].yPx);
      for (let i = 1; i < lineAnchors.length; i++) {
        c.lineTo(stripOffsetX + lineAnchors[i].xPx,
                 stripY      + lineAnchors[i].yPx);
      }
      c.stroke();
    };

    c.save();
    c.lineCap = "round"; c.lineJoin = "round";

    c.strokeStyle = "#ffffff";
    c.lineWidth = 7;
    for (const run of stripRuns) _strokeRun(run);

    c.strokeStyle = "#000000";
    c.lineWidth = 3.2;
    for (const run of stripRuns) _strokeRun(run);

    c.fillStyle = "#000000";
    for (const run of stripRuns) {
      if (run.length !== 1) continue;
      c.beginPath();
      c.arc(stripOffsetX + run[0].xPx, stripY + run[0].yPx, 5, 0,
            Math.PI * 2);
      c.fill();
    }
    c.restore();

    for (const ch of chunks) {
      const entry = segDirectory.get(ch.segIdx);
      if (!entry) continue;
      const cx = stripOffsetX + (ch.x0 + ch.x1) / 2;
      const by = stripHeaderY + STRIP_HEADER_H / 2;
      c.strokeStyle = "#000000";
      c.lineWidth = 1;
      c.beginPath();
      c.moveTo(cx, by + badgeRadiusFor(entry.order) + 1);
      c.lineTo(cx, stripY);
      c.stroke();
      drawBadge(c, cx, by, entry.order);
    }

    c.font = "700 18px " + FONT_SANS;
    c.fillStyle = "#333333";
    c.textAlign = "center"; c.textBaseline = "middle";
    for (let ri = 0; ri < wallLayouts.length - 1; ri++) {
      let lastX1 = 0;
      for (const ch of chunks) if (ch.runIdx === ri) lastX1 = ch.x1;
      const xMid = stripOffsetX + lastX1 + RUN_GAP / 2;
      const yMid = stripY + STRIP_FIXED_H / 2;
      c.fillText("...", xMid, yMid);
    }
    c.textAlign = "left"; c.textBaseline = "top";

    const axisRight = stripOffsetX - STRIP_AXIS_GAP;
    const axisLeft  = axisRight - STRIP_AXIS_W;
    const ticks = [
      { v: 0,             label: "0" },
      { v: WALL_HEIGHT/2, label: String(Math.round(WALL_HEIGHT / 2 / 10)) },
      { v: WALL_HEIGHT,   label: String(Math.round(WALL_HEIGHT / 10)) },
    ];
    c.save();
    c.strokeStyle = "#000000";
    c.fillStyle = "#000000";
    c.lineWidth = 1;
    c.font = "600 11px " + FONT_MONO;
    c.textAlign = "right";
    c.textBaseline = "middle";
    for (const t of ticks) {
      const ty = stripY + (1 - t.v / WALL_HEIGHT) * STRIP_FIXED_H;
      c.beginPath();
      c.moveTo(axisRight - 6, ty);
      c.lineTo(axisRight, ty);
      c.stroke();
      c.fillText(t.label, axisRight - 9, ty);
    }
    c.strokeStyle = "#666666";
    c.beginPath();
    c.moveTo(axisRight + 0.5, stripY);
    c.lineTo(axisRight + 0.5, stripY + STRIP_FIXED_H);
    c.stroke();
    c.save();
    c.translate(axisLeft + 4, stripY + STRIP_FIXED_H / 2);
    c.rotate(-Math.PI / 2);
    c.textAlign = "center"; c.textBaseline = "middle";
    c.font = "600 10px " + FONT_MONO;
    c.fillStyle = "#333333";
    c.fillText(T("heightAxis"), 0, 0);
    c.restore();
    c.restore();
  }

  const anchorPlanPos = new Map();
  if (planActive || hasWalls) {
    let infoX, planOriginX, badgeColX;

    if (planActive) {
      const totalRowW = INFO_W + INFO_PLAN_GAP + planW + BADGE_GAP + BADGE_COL_W;
      const rowX0 = (IMG_W - totalRowW) / 2;
      infoX       = rowX0;
      planOriginX = infoX + INFO_W + INFO_PLAN_GAP;
      badgeColX   = planOriginX + planW + BADGE_GAP + BADGE_COL_W / 2;
    } else {
      infoX       = (IMG_W - INFO_W) / 2;
      planOriginX = 0;
      badgeColX   = 0;
    }

    const infoY       = planRegionY + 20;
    const planOriginY = planRegionY + (planRegionH - planH) / 2;

    if (planActive) {
      const f2p = (px, py) => [
        planOriginX + (px - fb.minX) * planScale,
        planOriginY + (fb.maxY - py) * planScale,
      ];

      for (const face of GEOMETRY.faces) {
        const outer = face.outer;
        if (!outer || !outer.length) continue;
        c.beginPath();
        let [sx, sy] = f2p(outer[0][0], outer[0][1]);
        c.moveTo(sx, sy);
        for (let i = 1; i < outer.length; i++) {
          const [x, y] = f2p(outer[i][0], outer[i][1]);
          c.lineTo(x, y);
        }
        c.closePath();
        for (const h of face.holes) {
          if (!h || !h.length) continue;
          let [x0, y0] = f2p(h[0][0], h[0][1]);
          c.moveTo(x0, y0);
          for (let i = 1; i < h.length; i++) {
            const [x, y] = f2p(h[i][0], h[i][1]);
            c.lineTo(x, y);
          }
          c.closePath();
        }
        c.fillStyle = _pat(c, "roomInterior");
        c.fill("evenodd");
        c.strokeStyle = "#000000";
        c.lineWidth = 0.8;
        c.stroke();
      }

      for (const [segIdx] of segDirectory) {
        const seg = WALL.segments[segIdx];
        if (!seg) continue;
        const [ax, ay] = f2p(seg.a[0], seg.a[1]);
        const [bx, by] = f2p(seg.b[0], seg.b[1]);
        c.strokeStyle = "#000000";
        c.lineWidth = 7;
        c.lineCap = "round";
        c.beginPath();
        c.moveTo(ax, ay);
        c.lineTo(bx, by);
        c.stroke();
      }

      const planPts = [];
      for (const run of planRuns) {
        const pts = [];
        for (const id of run.ids) {
          const a = anchors.get(id);
          if (!a) continue;
          let px, py;
          if (a.space === "floor") { px = a.x; py = a.y; }
          else { const p = wallAttachToPlan(a.segIdx, a.t); px = p[0]; py = p[1]; }
          const [cx, cy] = f2p(px, py);
          pts.push([cx, cy]);
          anchorPlanPos.set(id, [cx, cy]);
        }
        planPts.push(pts);
      }

      const _strokePlan = (pts) => {
        if (pts.length < 2) return;
        c.beginPath();
        c.moveTo(pts[0][0], pts[0][1]);
        for (let i = 1; i < pts.length; i++) c.lineTo(pts[i][0], pts[i][1]);
        c.stroke();
      };

      c.save();
      c.lineCap = "round"; c.lineJoin = "round";

      c.strokeStyle = "#ffffff";
      c.lineWidth = 7;
      for (const pts of planPts) _strokePlan(pts);

      c.strokeStyle = "#000000";
      c.lineWidth = 3.2;
      for (const pts of planPts) _strokePlan(pts);

      c.fillStyle = "#000000";
      for (const pts of planPts) {
        if (pts.length !== 1) continue;
        c.beginPath();
        c.arc(pts[0][0], pts[0][1], 5, 0, Math.PI * 2);
        c.fill();
      }
      c.restore();

      for (const [, [px, py]] of anchorPlanPos) {
        c.beginPath();
        c.arc(px, py, 4, 0, Math.PI * 2);
        c.fillStyle = "#ffffff"; c.fill();
        c.strokeStyle = "#000000"; c.lineWidth = 1.8; c.stroke();
      }

      if (hasWalls) {
        const arrows = [];
        for (const [segIdx, entry] of segDirectory) {
          const seg = WALL.segments[segIdx];
          if (!seg) continue;
          const [ax, ay] = f2p(seg.a[0], seg.a[1]);
          const [bx, by] = f2p(seg.b[0], seg.b[1]);
          const wmx = (ax + bx) / 2;
          const wmy = (ay + by) / 2;
          arrows.push({
            order:    entry.order,
            wallMidX: wmx,
            wallMidY: wmy,
            style:    styleFor(entry.order),
            badgeX:   badgeColX,
            badgeY:   wmy,
          });
        }
        arrows.sort((a, b) => a.badgeY - b.badgeY);

        const MIN_GAP = 2 * badgeR + 6;
        for (let pass = 0; pass < 8; pass++) {
          let moved = false;
          for (let i = 1; i < arrows.length; i++) {
            const gap = arrows[i].badgeY - arrows[i-1].badgeY;
            if (gap < MIN_GAP) {
              const push = (MIN_GAP - gap) / 2 + 0.1;
              arrows[i-1].badgeY -= push;
              arrows[i].badgeY   += push;
              moved = true;
            }
          }
          if (!moved) break;
        }

        const topB = planRegionY + badgeR + 6;
        const botB = planRegionY + planRegionH - badgeR - 6;
        if (arrows.length) {
          const minY = Math.min(...arrows.map(a => a.badgeY));
          const maxY = Math.max(...arrows.map(a => a.badgeY));
          let shift = 0;
          if (minY < topB) shift = topB - minY;
          else if (maxY > botB) shift = botB - maxY;
          if (shift) for (const a of arrows) a.badgeY += shift;
        }

        for (const a of arrows) {
          const startX = a.badgeX - badgeR - 1;
          const path = [
            [startX, a.badgeY],
            [a.wallMidX, a.wallMidY],
          ];
          drawStyledPath(c, path, a.style, 0);
        }
        for (const a of arrows) {
          drawBadge(c, a.badgeX, a.badgeY, a.order);
        }
      }
    }

    {
      /* The info column is 240 px wide and its text was previously
         drawn at a fixed 14 px with no wrapping.  The old system
         fonts just barely fit the longest labels; the printable
         fonts are wider, so those labels now ran past INFO_W and
         painted on top of the plan.  The fix is to wrap every text
         block against its real available width and let the row
         height grow when a label needs a second line.  INFO_LINE_H
         matches the natural line height of the 14 px font. */
      let infoCurY = infoY;
      const infoX0 = infoX;

      const INFO_LINE_H = 14;
      const INFO_TEXT_W = INFO_W - 8;   // 4 px on each side

      /* Draw a string wrapped to fit the info column, advancing
         the local cursor by the block's actual height (INFO_HEADER_H
         for a one-line header, more when it wraps). */
      const _drawHeader = (text) => {
        c.fillStyle = "#000000";
        c.textAlign = "left";
        c.textBaseline = "top";
        c.font = "700 14px " + FONT_SANS;
        const lines = _wrapText(c, text, INFO_TEXT_W);
        for (let i = 0; i < lines.length; i++) {
          c.fillText(lines[i], infoX0, infoCurY + i * INFO_LINE_H);
        }
        infoCurY += Math.max(INFO_HEADER_H,
                             lines.length * INFO_LINE_H + 4);
      };

      _drawHeader(T("lengthBreakdown"));

      const parts = allCables().filter(x => trueCableIdOf(x) === tcId);
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
      const rows = [];
      if (wallLen  > 0.5) rows.push([T("wallRuns"),  fmtM(wallLen)]);
      if (floorLen > 0.5) rows.push([T("floorRuns"), fmtM(floorLen)]);
      rows.push([T("totalRow"), fmtM(totalLen)]);

      /* Label column is capped so the value column can always sit
         inside INFO_W.  Under the old fonts the natural measured
         width was under 96 px; if a future translation or font
         pushes a label past 96 px the label itself wraps rather
         than the value column sliding out of the box. */
      const LABEL_MAX_COL_W = 96;
      const VALUE_GUTTER_PX = 10;
      const valueColX = infoX0 + 4 + LABEL_MAX_COL_W + VALUE_GUTTER_PX;
      const valueMaxW = infoX0 + INFO_W - 4 - valueColX;

      for (const [label, val] of rows) {
        c.fillStyle = "#000000";
        c.textAlign = "left"; c.textBaseline = "top";

        c.font = "500 14px " + FONT_SANS;
        const labelLines = _wrapText(c, label, LABEL_MAX_COL_W);
        for (let i = 0; i < labelLines.length; i++) {
          c.fillText(labelLines[i], infoX0 + 4,
                     infoCurY + i * INFO_LINE_H);
        }

        c.font = "500 14px " + FONT_MONO;
        const valLines = _wrapText(c, val, valueMaxW);
        for (let i = 0; i < valLines.length; i++) {
          c.fillText(valLines[i], valueColX,
                     infoCurY + i * INFO_LINE_H);
        }

        const n = Math.max(labelLines.length, valLines.length);
        infoCurY += Math.max(INFO_ROW_H, n * INFO_LINE_H + 4);
      }
      infoCurY += INFO_GAP;

      _drawHeader(T("legend"));

      /* A legend row is a swatch plus a text label.  The swatch is
         fixed-geometry (28 px wide plus an 8 px gap); the label
         gets the remaining width and wraps freely.  The swatch is
         centred vertically on the text block, and the row height
         grows to fit whichever is taller. */
      const _swatch = (label, h, draw) => {
        const swX = infoX0 + 4;
        const swW = 28;
        const textX = swX + swW + 8;
        const textW = infoX0 + INFO_W - 4 - textX;

        c.font = "500 14px " + FONT_SANS;
        const lines = _wrapText(c, label, textW);
        const textH = lines.length * INFO_LINE_H;
        const blockH = Math.max(INFO_ROW_H, textH + 4);
        const swY = infoCurY + blockH / 2;

        c.save();
        c.strokeStyle = "#000000";
        c.fillStyle   = "#000000";
        c.lineCap     = "round";
        c.lineJoin    = "round";
        draw(swX, swY, swW, h);
        c.restore();

        c.fillStyle = "#000000";
        c.font = "500 14px " + FONT_SANS;
        c.textAlign = "left"; c.textBaseline = "top";
        const textTop = infoCurY + (blockH - textH) / 2;
        for (let i = 0; i < lines.length; i++) {
          c.fillText(lines[i], textX, textTop + i * INFO_LINE_H);
        }

        infoCurY += blockH;
      };

      _swatch(T("legendCable"), 9, (x, y, w) => {
        c.lineWidth = 3.2;
        c.beginPath(); c.moveTo(x, y); c.lineTo(x + w, y); c.stroke();
      });
      _swatch(T("legendWallFloorLink"), 9, (x, y, w) => {
        c.lineWidth = 1.4;
        c.setLineDash([2, 3]);
        c.beginPath(); c.moveTo(x, y); c.lineTo(x + w, y); c.stroke();
        c.setLineDash([]);
      });
      _swatch(T("legendTraversedWall"), 9, (x, y, w) => {
        c.lineWidth = 6;
        c.beginPath(); c.moveTo(x + 3, y); c.lineTo(x + w - 3, y); c.stroke();
      });
      _swatch(T("legendStepFace"), 11, (x, y, w, h) => {
        c.fillStyle = _pat(c, "step");
        c.fillRect(x, y - h / 2, w, h);
        c.strokeStyle = "#000000";
        c.lineWidth = 0.8;
        c.strokeRect(x + 0.5, y - h / 2 + 0.5, w - 1, h - 1);
      });
      _swatch(T("legendVoid"), 11, (x, y, w, h) => {
        c.fillStyle = _pat(c, "void");
        c.fillRect(x, y - h / 2, w, h);
        c.strokeStyle = "rgba(0, 0, 0, 0.35)";
        c.lineWidth = 0.6;
        c.setLineDash([2, 2]);
        c.strokeRect(x + 0.5, y - h / 2 + 0.5, w - 1, h - 1);
        c.setLineDash([]);
      });

      infoCurY += INFO_GAP;

      if (hasWalls) {
        _drawHeader(T("wallSegmentsArrowStyle"));

        const arrowLen = 36;
        for (const [segIdx, entry] of segDirectory) {
          const r = badgeRadiusFor(entry.order);
          drawBadge(c, infoX0 + 4 + r, infoCurY + WALL_ROW_H / 2, entry.order);
          const ax0 = infoX0 + 4 + r * 2 + 6;
          drawArrowSample(c, ax0, infoCurY + WALL_ROW_H / 2, arrowLen,
                          styleFor(entry.order));

          /* Tag sits to the right of the arrow sample.  Fit the
             tag against the remaining column width so a long tag
             (e.g. "SR.edge[1]") cannot slide past INFO_W. */
          const tagX = ax0 + arrowLen + 8;
          const tagMaxW = infoX0 + INFO_W - 4 - tagX;
          c.fillStyle = "#000000";
          c.textAlign = "left"; c.textBaseline = "middle";
          c.font = "600 14px " + FONT_MONO;
          const tagLines = _wrapText(c, entry.tag, tagMaxW);
          c.fillText(tagLines[0], tagX, infoCurY + WALL_ROW_H / 2);

          infoCurY += WALL_ROW_H;
        }
        c.textBaseline = "top";
      }
    }
  }

  c.setLineDash([2, 3]);
  c.strokeStyle = "#000000";
  c.lineWidth = 1.4;

  const drawDottedWire = (wp, fp) => {
    c.beginPath();
    c.moveTo(wp[0], wp[1]);
    c.lineTo(fp[0], fp[1]);
    c.stroke();
    c.setLineDash([]);

    c.fillStyle = "#ffffff";
    c.beginPath(); c.arc(wp[0], wp[1], 6.5, 0, Math.PI * 2); c.fill();
    c.beginPath(); c.arc(fp[0], fp[1], 6.5, 0, Math.PI * 2); c.fill();

    c.fillStyle = "#000000";
    c.beginPath(); c.arc(wp[0], wp[1], 4, 0, Math.PI * 2); c.fill();
    c.beginPath(); c.arc(fp[0], fp[1], 4, 0, Math.PI * 2); c.fill();

    c.setLineDash([2, 3]);
  };

  for (const id of orderedIds) {
    const sp = anchorStripPos.get(id);
    const pp = anchorPlanPos.get(id);
    if (!sp || !pp) continue;
    drawDottedWire(sp, pp);
  }
  for (let i = 0; i < orderedIds.length - 1; i++) {
    const a = anchors.get(orderedIds[i]);
    const b = anchors.get(orderedIds[i + 1]);
    if (!a || !b) continue;
    const aE = a.space === "wall-edge" && Math.abs(a.v || 0) > 1e-3;
    const bE = b.space === "wall-edge" && Math.abs(b.v || 0) > 1e-3;
    if (aE && b.space === "floor") {
      const wp = anchorStripPos.get(a.id);
      const fp = anchorPlanPos.get(b.id);
      if (wp && fp) drawDottedWire(wp, fp);
    }
    if (bE && a.space === "floor") {
      const wp = anchorStripPos.get(b.id);
      const fp = anchorPlanPos.get(a.id);
      if (wp && fp) drawDottedWire(wp, fp);
    }
  }
  c.setLineDash([]);

  /* Vertex labels paint last so the cross-view wires never run
     through a pill.  The pills have opaque white plates (fillRect
     in drawVertexLabels), so drawing them here occludes every
     wire segment that would otherwise cross a pill's text.  The
     leader lines that connect each pill back to its strip anchor
     are drawn in the same pass, which puts them on top of the
     wires too — same z-rule, same reason. */
  if (stripActive) {
    drawVertexLabels(c, labelPlacement, stripY, STRIP_FIXED_H);
  }

  const cropped = cropCanvasToContent(cv, 14 * DPR);
  const canvas = scaleCanvasToWidth(cropped, 2200);

  const meta = buildCableRunMeta(tcId, orderedIds, segDirectory, chunks,
                                  labelPlacement, stripOffsetX, stripW,
                                  stripTotalMM);

  return { canvas, meta };
}
"""
