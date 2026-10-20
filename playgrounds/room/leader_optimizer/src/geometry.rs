use crate::types::*;

pub fn js_sign(x: f64) -> f64 {
    if x > 0.0 { 1.0 } else if x < 0.0 { -1.0 } else { 0.0 }
}

pub fn point_seg_dist(px: f64, py: f64, ax: f64, ay: f64, bx: f64, by: f64) -> f64 {
    let dx = bx - ax; let dy = by - ay;
    let len2 = dx * dx + dy * dy;
    if len2 < 1e-9 { return ((px - ax).powi(2) + (py - ay).powi(2)).sqrt(); }
    let mut t = ((px - ax) * dx + (py - ay) * dy) / len2;
    t = t.clamp(0.0, 1.0);
    ((px - (ax + t * dx)).powi(2) + (py - (ay + t * dy)).powi(2)).sqrt()
}

pub fn seg_seg_dist(ax: f64, ay: f64, bx: f64, by: f64,
                    cx: f64, cy: f64, dx: f64, dy: f64) -> f64 {
    let a = point_seg_dist(ax, ay, cx, cy, dx, dy);
    let b = point_seg_dist(bx, by, cx, cy, dx, dy);
    let c = point_seg_dist(cx, cy, ax, ay, bx, by);
    let d = point_seg_dist(dx, dy, ax, ay, bx, by);
    a.min(b).min(c.min(d))
}

pub fn angle_diff(a: f64, b: f64) -> f64 {
    let mut d = (a - b).abs() % std::f64::consts::PI;
    if d > std::f64::consts::PI / 2.0 { d = std::f64::consts::PI - d; }
    d
}

fn clip_axis(p: f64, q: f64, t0: &mut f64, t1: &mut f64) -> bool {
    if p.abs() < 1e-9 { return q >= 0.0; }
    let r = q / p;
    if p < 0.0 {
        if r > *t1 { return false; }
        if r > *t0 { *t0 = r; }
    } else {
        if r < *t0 { return false; }
        if r < *t1 { *t1 = r; }
    }
    true
}

pub fn seg_box_overlap(s: &Segment, b: &Aabb) -> f64 {
    let x0 = s.ax; let y0 = s.ay;
    let dx = s.bx - x0; let dy = s.by - y0;
    let mut t0 = 0.0_f64;
    let mut t1 = 1.0_f64;
    if !clip_axis(-dx, x0 - b.ql, &mut t0, &mut t1) { return 0.0; }
    if !clip_axis( dx, b.qr - x0, &mut t0, &mut t1) { return 0.0; }
    if !clip_axis(-dy, y0 - b.qt, &mut t0, &mut t1) { return 0.0; }
    if !clip_axis( dy, b.qb - y0, &mut t0, &mut t1) { return 0.0; }
    (t1 - t0).max(0.0)
}

pub fn seg_seg_proper_cross(a: &Segment, b: &Segment) -> bool {
    let d1x = a.bx - a.ax; let d1y = a.by - a.ay;
    let d2x = b.bx - b.ax; let d2y = b.by - b.ay;
    let denom = d1x * d2y - d1y * d2x;
    if denom.abs() < 1e-9 { return false; }
    let rx = b.ax - a.ax; let ry = b.ay - a.ay;
    let t = (rx * d2y - ry * d2x) / denom;
    let u = (rx * d1y - ry * d1x) / denom;
    t > 1e-6 && t < 1.0 - 1e-6 && u > 1e-6 && u < 1.0 - 1e-6
}

pub fn count_segment_crossings(a: &[Segment], b: &[Segment]) -> i32 {
    let mut n = 0i32;
    for sa in a {
        for sb in b {
            if sa.max_x + 0.5 < sb.min_x || sb.max_x + 0.5 < sa.min_x { continue; }
            if sa.max_y + 0.5 < sb.min_y || sb.max_y + 0.5 < sa.min_y { continue; }
            if seg_seg_proper_cross(sa, sb) { n += 1; }
        }
    }
    n
}

/// Faithful port of the JS `_pathSegments` — including the
/// collinear-collapse pre-pass that reconstructs T-junction crossings.
pub fn path_segments(path: &[Point], path_idx: i32, min_len: f64) -> Vec<Segment> {
    let mut pts: Vec<Point> = Vec::new();
    for &p in path {
        if pts.len() >= 2 {
            let a = pts[pts.len() - 2];
            let b = pts[pts.len() - 1];
            let abx = b.x - a.x;
            let aby = b.y - a.y;
            let bcx = p.x - b.x;
            let bcy = p.y - b.y;
            let cross = abx * bcy - aby * bcx;
            if cross.abs() < 1e-3 {
                let dot = abx * bcx + aby * bcy;
                if dot > 0.0 { pts.pop(); } else { continue; }
            }
        }
        pts.push(p);
    }

    let mut out = Vec::new();
    if pts.len() < 2 { return out; }
    for i in 0..pts.len() - 1 {
        let a = pts[i]; let b = pts[i + 1];
        let dx = b.x - a.x; let dy = b.y - a.y;
        let len = (dx * dx + dy * dy).sqrt();
        if len < min_len { continue; }
        let kind = if dx.abs() < 0.5 { 0u8 }
                   else if dy.abs() < 0.5 { 1u8 }
                   else { 2u8 };
        out.push(Segment {
            ax: a.x, ay: a.y, bx: b.x, by: b.y,
            min_x: a.x.min(b.x), max_x: a.x.max(b.x),
            min_y: a.y.min(b.y), max_y: a.y.max(b.y),
            angle: dy.atan2(dx),
            len, path_idx, kind,
        });
    }
    out
}
