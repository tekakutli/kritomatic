use crate::constants::*;
use crate::geometry::*;
use crate::types::*;

#[derive(Default, Debug, Clone, Copy)]
pub struct PenaltyResult { pub count: i32, pub depth: f64 }

#[derive(Default, Debug, Clone, Copy)]
pub struct WirePenalty {
    pub count: i32, pub depth: f64,
    pub par_count: i32, pub par_depth: f64,
    pub approach_count: i32, pub approach_depth: f64,
}

pub fn boundary_overlap_penalty(
    leader_segs: &[Segment], boundaries: &[f64], strip_h: f64,
) -> PenaltyResult {
    let mut r = PenaltyResult::default();
    if boundaries.is_empty() { return r; }
    let slope_max = 1.0;
    for s in leader_segs {
        let dy = s.by - s.ay;
        if dy.abs() < 1e-6 { continue; }
        let slope = ((s.bx - s.ax) / dy).abs();
        if slope > slope_max { continue; }
        let y_lo = s.min_y.max(0.0);
        let y_hi = s.max_y.min(strip_h);
        if y_hi - y_lo < SEG_MIN_LEN { continue; }
        let dx = s.bx - s.ax;
        for &bx in boundaries {
            if s.max_x + BOUNDARY_MIN_SEP < bx { continue; }
            if s.min_x - BOUNDARY_MIN_SEP > bx { continue; }
            let mut sum = 0.0;
            let n = 8;
            for k in 0..=n {
                let y = y_lo + (y_hi - y_lo) * (k as f64) / (n as f64);
                let t = (y - s.ay) / dy;
                let x = s.ax + dx * t;
                sum += (x - bx).abs();
            }
            let avg = sum / ((n + 1) as f64);
            if avg < BOUNDARY_MIN_SEP {
                r.count += 1;
                r.depth += BOUNDARY_MIN_SEP - avg;
            }
        }
    }
    r
}

pub fn wire_overlap_penalty(
    leader_segs: &[Segment],
    placed: &[PlacedItem],
    wire_segments: &[WireSegment],
) -> WirePenalty {
    let mut r = WirePenalty::default();
    if wire_segments.is_empty() { return r; }
    for s in leader_segs {
        let it = &placed[s.path_idx as usize];
        let ax = it.anchor_cx;
        let ay = it.anchor_cy_rel;
        let s_angle = s.angle;

        let da = ((s.ax - ax).powi(2) + (s.ay - ay).powi(2)).sqrt();
        let db = ((s.bx - ax).powi(2) + (s.by - ay).powi(2)).sqrt();
        let near_anchor = da.min(db) < WIRE_ANCHOR_TRIM;
        let far = if da > db { (s.ax, s.ay) } else { (s.bx, s.by) };

        let seg_len = ((s.bx - s.ax).powi(2) + (s.by - s.ay).powi(2)).sqrt();
        if near_anchor && seg_len < WIRE_ANCHOR_TRIM { continue; }

        for w in wire_segments {
            let wmin_x = w.ax.min(w.bx);
            let wmax_x = w.ax.max(w.bx);
            let wmin_y = w.ay.min(w.by);
            let wmax_y = w.ay.max(w.by);
            if s.max_x + WIRE_APPROACH_SEP < wmin_x { continue; }
            if wmax_x + WIRE_APPROACH_SEP < s.min_x { continue; }
            if s.max_y + WIRE_APPROACH_SEP < wmin_y { continue; }
            if wmax_y + WIRE_APPROACH_SEP < s.min_y { continue; }

            let d = if near_anchor {
                point_seg_dist(far.0, far.1, w.ax, w.ay, w.bx, w.by)
            } else {
                seg_seg_dist(s.ax, s.ay, s.bx, s.by, w.ax, w.ay, w.bx, w.by)
            };

            let ad = angle_diff(s_angle, w.angle);
            if ad < WIRE_PARALLEL_TOL && d < WIRE_MIN_SEP {
                r.par_count += 1;
                r.par_depth += WIRE_MIN_SEP - d;
                r.count += 1;
                r.depth += WIRE_MIN_SEP - d;
            } else if ad < WIRE_PARALLEL_TOL && d < WIRE_APPROACH_SEP {
                r.approach_count += 1;
                r.approach_depth += WIRE_APPROACH_SEP - d;
            } else if d < SEG_MIN_SEP {
                r.count += 1;
                r.depth += SEG_MIN_SEP - d;
            }
        }
    }
    r
}

pub fn wall_edge_overlap_penalty(
    leader_segs: &[Segment], wall_edges: &[WallEdge],
) -> PenaltyResult {
    let mut r = PenaltyResult::default();
    if wall_edges.is_empty() { return r; }
    let slope_max = 0.4;
    for s in leader_segs {
        let dx = s.bx - s.ax; let dy = s.by - s.ay;
        if dx.abs() < 1e-6 { continue; }
        if (dy / dx).abs() > slope_max { continue; }
        for we in wall_edges {
            let x_lo = s.min_x.max(we.x0);
            let x_hi = s.max_x.min(we.x1);
            if x_hi - x_lo < 2.0 * SEG_MIN_LEN { continue; }
            if s.min_y - SEG_MIN_SEP > we.y { continue; }
            if s.max_y + SEG_MIN_SEP < we.y { continue; }
            let mut max_d: f64 = 0.0;
            let mut sum_d: f64 = 0.0;
            let n = 5;
            for k in 0..=n {
                let x = x_lo + (x_hi - x_lo) * (k as f64) / (n as f64);
                let t = (x - s.ax) / dx;
                let y = s.ay + dy * t;
                let d = (y - we.y).abs();
                if d > max_d { max_d = d; }
                sum_d += d;
            }
            if max_d < SEG_MIN_SEP {
                r.count += 1;
                r.depth += SEG_MIN_SEP - sum_d / ((n + 1) as f64);
            }
        }
    }
    r
}

#[derive(Clone, Copy, Debug)]
pub struct CornerFeature {
    pub x0: f64, pub y0: f64, pub x1: f64, pub y1: f64,
    pub path_idx: usize,
}

pub fn corner_feature(
    it: &PlacedItem, self_idx: usize, placed: &[PlacedItem],
    strip_h: f64, top_pad: f64, track_offsets: &[f64], which: u8,
) -> CornerFeature {
    let anchor_y = it.anchor_cy_rel;
    let chan_y = strip_h + it.channel_y_rel;
    let pill_top_y = strip_h + top_pad + track_offsets[it.track];
    let base = crate::path::compute_leader_path(
        it.anchor_cx, anchor_y, it.offset_a, it.offset_p,
        chan_y, it.pill_center_x, pill_top_y,
        self_idx, placed, strip_h, top_pad, track_offsets, it.dive_mode,
    );

    if base.len() < 3 {
        let p = base.get(0).copied().unwrap_or(Point { x: 0.0, y: 0.0 });
        return CornerFeature {
            x0: p.x, y0: p.y, x1: p.x, y1: p.y, path_idx: self_idx,
        };
    }

    let (a, c, n, b) = if which == 0 {
        (base[0], base[1], base[2], it.bevel0)
    } else {
        let l = base.len();
        (base[l - 3], base[l - 2], base[l - 1], it.bevel1)
    };

    if b < BEVEL_MIN_APPLY {
        return CornerFeature {
            x0: c.x, y0: c.y, x1: c.x, y1: c.y, path_idx: self_idx,
        };
    }
    let len_a = ((c.x - a.x).powi(2) + (c.y - a.y).powi(2)).sqrt();
    let len_b = ((n.x - c.x).powi(2) + (n.y - c.y).powi(2)).sqrt();
    let bu = b.min(len_a * 0.85).min(len_b * 0.85);
    if bu < BEVEL_MIN_APPLY || len_a < 1e-6 || len_b < 1e-6 {
        return CornerFeature {
            x0: c.x, y0: c.y, x1: c.x, y1: c.y, path_idx: self_idx,
        };
    }
    let t1 = (len_a - bu) / len_a;
    let t2 = bu / len_b;
    CornerFeature {
        x0: a.x + (c.x - a.x) * t1, y0: a.y + (c.y - a.y) * t1,
        x1: c.x + (n.x - c.x) * t2, y1: c.y + (n.y - c.y) * t2,
        path_idx: self_idx,
    }
}

pub fn corner_dist(fa: &CornerFeature, fb: &CornerFeature) -> f64 {
    let a_pt = (fa.x0 - fa.x1).abs() < 0.5 && (fa.y0 - fa.y1).abs() < 0.5;
    let b_pt = (fb.x0 - fb.x1).abs() < 0.5 && (fb.y0 - fb.y1).abs() < 0.5;
    if a_pt && b_pt {
        return ((fa.x0 - fb.x0).powi(2) + (fa.y0 - fb.y0).powi(2)).sqrt();
    }
    if a_pt { return point_seg_dist(fa.x0, fa.y0, fb.x0, fb.y0, fb.x1, fb.y1); }
    if b_pt { return point_seg_dist(fb.x0, fb.y0, fa.x0, fa.y0, fa.x1, fa.y1); }
    seg_seg_dist(fa.x0, fa.y0, fa.x1, fa.y1, fb.x0, fb.y0, fb.x1, fb.y1)
}
