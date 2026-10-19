EXPORT_JS = r"""
import {
  pairs, pairFrame, segmentCornersWorldAt, lineEndpointsWorld,
  forEachSegmentCopy, w2s, layout, view,
} from "./pg_core.js";
import { segRect } from "./pg_view_segments.js";
import { flashStatus } from "./pgkit/panel_core.js";

function _round3(v) { return Math.round(v * 1000) / 1000; }

export function buildVisualStateExport() {
  const out = {
    schemaVersion: 1,
    generatedAt:   new Date().toISOString(),
    view: {
      scale: _round3(view.scale),
      tx:    _round3(view.tx),
      ty:    _round3(view.ty),
      zoom:  _round3(view.zoom),
    },
    layout: {
      topH:      layout.topH,
      dividerY:  layout.dividerY,
      reservedY: layout.reservedY,
      reservedH: layout.reservedH,
    },
    segRect: (function () {
      const r = segRect();
      return { x0: _round3(r.x0), y0: _round3(r.y0),
               w:  _round3(r.w),  h:  _round3(r.h) };
    })(),
    segSpan: 12,
    pairs: [],
  };

  for (const p of pairs) {
    const f = pairFrame(p);
    const entry = {
      id:      p.id,
      name:    p.name,
      cx:      _round3(p.cx),
      cy:      _round3(p.cy),
      angle:   _round3(p.angle),
      metricA: _round3(p.metricA),
      metricB: _round3(p.metricB),
      offset:  _round3(p.offset),
      gap:     _round3(p.gap),
      loopbackGap: _round3((typeof p.loopbackGap === "number") ? p.loopbackGap : 0),
      scale:   _round3((typeof p.scale === "number" && isFinite(p.scale) && p.scale > 0) ? p.scale : 1),
      u: [_round3(f.ux), _round3(f.uy)],
      v: [_round3(f.vx), _round3(f.vy)],
      linesScreen: {},
      segments: [],
    };

    for (const which of ["A", "B"]) {
      const [[ax, ay], [bx, by]] = lineEndpointsWorld(p, which);
      const [sax, say] = w2s(ax, ay);
      const [sbx, sby] = w2s(bx, by);
      entry.linesScreen[which] = [
        [_round3(sax), _round3(say)],
        [_round3(sbx), _round3(sby)],
      ];
    }

    for (const seg of p.segments) {
      const copies = [];
      forEachSegmentCopy((12 + ((typeof p.loopbackGap === "number") ? p.loopbackGap : 0)),
                        seg.a, seg.b, (a, b, k) => {
        const world = segmentCornersWorldAt(p, a, b);
        const screen = world.map(([wx, wy]) => w2s(wx, wy));
        copies.push({
          k, a: _round3(a), b: _round3(b),
          cornersWorld: world.map(([wx, wy]) => [_round3(wx), _round3(wy)]),
          cornersScreen: screen.map(([sx, sy]) => [_round3(sx), _round3(sy)]),
        });
      });
      entry.segments.push({
        id: seg.id, name: seg.name,
        a: _round3(seg.a), b: _round3(seg.b),
        filled: seg.filled !== false,
        copies,
      });
    }

    out.pairs.push(entry);
  }

  return out;
}

export function exportVisualStateJSON() {
  const data = buildVisualStateExport();
  const text = JSON.stringify(data, null, 2);
  const url  = "data:application/json;charset=utf-8," + encodeURIComponent(text);
  const a = document.createElement("a");
  a.href = url;
  a.download = "pair_visual_state.json";
  a.style.display = "none";
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  flashStatus("Exported " + pairs.length + " pair(s)", "ok");
}
"""
