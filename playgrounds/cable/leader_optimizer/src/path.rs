use crate::channel::build_channel_to_pill;
use crate::constants::*;
use crate::geometry::js_sign;
use crate::types::*;

pub fn compute_leader_path(
    anchor_x: f64, anchor_y: f64, off_a: f64, off_p: f64,
    chan_y: f64, pill_x: f64, pill_top_y: f64,
    self_idx: usize, placed: &[PlacedItem],
    strip_bottom: f64, top_pad: f64, track_offsets: &[f64],
    dive_mode: i32,
) -> Vec<Point> {
    let descent_x = pill_x + off_p;
    let mut path = Vec::new();
    path.push(Point { x: anchor_x, y: anchor_y });
    match dive_mode {
        1 => {
            path.push(Point { x: anchor_x,  y: chan_y });
            path.push(Point { x: descent_x, y: chan_y });
        }
        2 => {
            path.push(Point { x: descent_x, y: anchor_y });
            path.push(Point { x: descent_x, y: chan_y });
        }
        _ => {
            path.push(Point { x: anchor_x + off_a, y: chan_y });
            path.push(Point { x: descent_x,       y: chan_y });
        }
    }
    for p in build_channel_to_pill(
        descent_x, pill_x, chan_y, pill_top_y, self_idx, placed,
        strip_bottom, top_pad, track_offsets,
    ) {
        path.push(p);
    }
    path
}

pub fn build_leader_path_rel(
    it: &PlacedItem, self_idx: usize, placed: &[PlacedItem],
    strip_h: f64, top_pad: f64, track_offsets: &[f64],
) -> Vec<Point> {
    let anchor_y = it.anchor_cy_rel;
    let chan_y = strip_h + it.channel_y_rel;
    let pill_top_y = strip_h + top_pad + track_offsets[it.track];
    compute_leader_path(
        it.anchor_cx, anchor_y, it.offset_a, it.offset_p,
        chan_y, it.pill_center_x, pill_top_y,
        self_idx, placed, strip_h, top_pad, track_offsets,
        it.dive_mode,
    )
}

pub fn bevel_path(path: &[Point], b0: f64, b1: f64) -> Vec<Point> {
    let n = path.len();
    if n < 4 { return path.to_vec(); }
    if b0 < BEVEL_MIN_APPLY && b1 < BEVEL_MIN_APPLY { return path.to_vec(); }

    let mut out = Vec::with_capacity(n + 2);
    out.push(path[0]);

    let a  = path[0];
    let c1 = path[1];
    let n1 = path[2];
    let len_a = ((c1.x - a.x).powi(2)  + (c1.y - a.y).powi(2)).sqrt();
    let len_b = ((n1.x - c1.x).powi(2) + (n1.y - c1.y).powi(2)).sqrt();
    let b0u = b0.min(len_a * 0.85).min(len_b * 0.85);
    if b0u >= BEVEL_MIN_APPLY && len_a > 1e-6 && len_b > 1e-6 {
        let t1 = (len_a - b0u) / len_a;
        let t2 = b0u / len_b;
        out.push(Point { x: a.x  + (c1.x - a.x)  * t1,
                         y: a.y  + (c1.y - a.y)  * t1 });
        out.push(Point { x: c1.x + (n1.x - c1.x) * t2,
                         y: c1.y + (n1.y - c1.y) * t2 });
    } else {
        out.push(c1);
    }

    if n >= 5 {
        for i in 2..=n - 3 { out.push(path[i]); }
    }

    let m2 = path[n - 3];
    let c2 = path[n - 2];
    let p  = path[n - 1];
    let len_c = ((c2.x - m2.x).powi(2) + (c2.y - m2.y).powi(2)).sqrt();
    let len_d = ((p.x  - c2.x).powi(2) + (p.y  - c2.y).powi(2)).sqrt();
    let b1u = b1.min(len_c * 0.85).min(len_d * 0.85);
    if b1u >= BEVEL_MIN_APPLY && len_c > 1e-6 && len_d > 1e-6 {
        let t1 = (len_c - b1u) / len_c;
        let t2 = b1u / len_d;
        out.push(Point { x: m2.x + (c2.x - m2.x) * t1,
                         y: m2.y + (c2.y - m2.y) * t1 });
        out.push(Point { x: c2.x + (p.x  - c2.x) * t2,
                         y: c2.y + (p.y  - c2.y) * t2 });
    } else {
        out.push(c2);
    }
    out.push(p);
    out
}

fn jog_segment(points: &[Point], seg_idx: usize, shift: f64) -> Vec<Point> {
    if shift.abs() < JOG_MIN_APPLY { return points.to_vec(); }
    let n = points.len();
    if n < 2 { return points.to_vec(); }
    if seg_idx >= n - 1 { return points.to_vec(); }
    let a = points[seg_idx];
    let b = points[seg_idx + 1];
    if (a.x - b.x).abs() > 0.5 { return points.to_vec(); }
    let d = shift.abs();
    let x = a.x;
    let ya = a.y; let yb = b.y;
    let l = (yb - ya).abs();
    if l < 4.0 * d { return points.to_vec(); }
    let sgn = if yb > ya { 1.0 } else { -1.0 };
    let m1 = Point { x: x + shift, y: ya + sgn * d };
    let m2 = Point { x: x + shift, y: yb - sgn * d };
    let mut out = Vec::with_capacity(n + 2);
    out.extend_from_slice(&points[..seg_idx + 1]);
    out.push(m1);
    out.push(m2);
    out.extend_from_slice(&points[seg_idx + 1..]);
    out
}

pub fn apply_jogs_to_path(path: &[Point], it: &PlacedItem) -> Vec<Point> {
    let mut p: Vec<Point> = path.to_vec();
    let anchor_end_x = it.anchor_cx + it.offset_a;
    let fwd = js_sign(it.pill_center_x - anchor_end_x);
    let allowed = |j: f64| -> bool {
        if fwd == 0.0 { return true; }
        if j == 0.0   { return true; }
        js_sign(j) == fwd
    };
    if it.jog0 != 0.0 && it.jog0.abs() >= JOG_MIN_APPLY && p.len() >= 2 && allowed(it.jog0) {
        p = jog_segment(&p, 0, it.jog0);
    }
    if it.jog1 != 0.0 && it.jog1.abs() >= JOG_MIN_APPLY && p.len() >= 2 && allowed(it.jog1) {
        p = jog_segment(&p, p.len() - 2, it.jog1);
    }
    p
}

pub fn candidates_for(cur: f64, is_bevel: bool) -> Vec<f64> {
    let step = if is_bevel { BEVEL_STEP } else { JOG_STEP };
    let max_v = if is_bevel { BEVEL_MAX } else { JOG_MAX };
    let min_v = if is_bevel { BEVEL_MIN_APPLY } else { JOG_MIN_APPLY };
    let allow_neg = !is_bevel;

    let mut set: Vec<f64> = Vec::with_capacity(8);

    let snap = |v: f64| -> Option<f64> {
        if v == 0.0 { return Some(0.0); }
        if !allow_neg && v < 0.0 { return None; }
        let a = v.abs();
        if a < min_v { return None; }
        let c = a.min(max_v);
        Some(if allow_neg { js_sign(v) * c } else { c })
    };

    if let Some(s) = snap(cur + step) { set.push(s); }
    if let Some(s) = snap(cur - step) { set.push(s); }

    if cur == 0.0 {
        set.push(min_v);
        if allow_neg { set.push(-min_v); }
    }
    if cur != 0.0 { set.push(0.0); }

    let mut out: Vec<f64> = Vec::with_capacity(set.len());
    for s in set {
        if (s - cur).abs() < 1e-9 { continue; }
        if out.iter().any(|&x| (x - s).abs() < 1e-9) { continue; }
        out.push(s);
    }
    out
}
