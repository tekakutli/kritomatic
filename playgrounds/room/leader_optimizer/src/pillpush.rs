use crate::constants::*;
use crate::geometry::*;
use crate::optimize::optimize_leader_geometry;
use crate::path::{bevel_path, apply_jogs_to_path, compute_leader_path};
use crate::snapshot::*;
use crate::types::*;

fn pill_has_same_track_collision(idx: usize, placed: &[PlacedItem]) -> bool {
    let me = &placed[idx];
    let my_track = me.track;
    let my_l = me.pill_center_x - me.w / 2.0;
    let my_r = me.pill_center_x + me.w / 2.0;
    for (j, other) in placed.iter().enumerate() {
        if j == idx { continue; }
        if other.track != my_track { continue; }
        let o_l = other.pill_center_x - other.w / 2.0;
        let o_r = other.pill_center_x + other.w / 2.0;
        if my_r + PILL_PROX > o_l && o_r + PILL_PROX > my_l { return true; }
    }
    false
}

fn separate_pills_in_tracks(placed: &mut [PlacedItem]) {
    use std::collections::BTreeMap;
    let mut by_track: BTreeMap<usize, Vec<usize>> = BTreeMap::new();
    for (i, it) in placed.iter().enumerate() {
        by_track.entry(it.track).or_default().push(i);
    }
    for (_, mut indices) in by_track {
        indices.sort_by(|&a, &b|
            placed[a].pill_center_x.partial_cmp(&placed[b].pill_center_x).unwrap());
        for k in 1..indices.len() {
            let prev = indices[k - 1];
            let cur  = indices[k];
            let prev_r = placed[prev].pill_center_x + placed[prev].w / 2.0;
            let cur_l  = placed[cur].pill_center_x  - placed[cur].w  / 2.0;
            let gap = cur_l - prev_r;
            if gap < PILL_PROX {
                placed[cur].pill_center_x += PILL_PROX - gap;
            }
        }
    }
}

/// Faithful port of `pushPillsForLeaderConflicts`.  Includes both the
/// per-pill single-move pass and the coordinated pill pair moves.
pub fn push_pills_for_leader_conflicts(
    placed: &mut [PlacedItem],
    strip_h: f64, top_pad: f64, track_offsets: &[f64],
    ctx_owned: (Vec<f64>, Vec<WireSegment>, Vec<WallEdge>),
) {
    separate_pills_in_tracks(placed);

    for _ in 0..PUSH_MAX_ROUNDS {
        let mut any_moved = false;

        for li in 0..placed.len() {
            let layout = build_layout(placed, strip_h, top_pad, track_offsets);
            let ctx = ObstacleContext {
                boundaries: &ctx_owned.0,
                wire_segments: &ctx_owned.1,
                wall_edges: &ctx_owned.2,
                placed, strip_h,
            };
            let before = count_conflicts_at(li, &layout, Some(&ctx));
            if before == 0 { continue; }

            let orig_x = placed[li].pill_center_x;
            let mut best_x = orig_x;
            let mut best_n = before;

            for &dx in &PUSH_STEPS {
                placed[li].pill_center_x = orig_x + dx;
                if pill_has_same_track_collision(li, placed) { continue; }
                let layout2 = build_layout(placed, strip_h, top_pad, track_offsets);
                let ctx2 = ObstacleContext {
                    boundaries: &ctx_owned.0,
                    wire_segments: &ctx_owned.1,
                    wall_edges: &ctx_owned.2,
                    placed, strip_h,
                };
                let n = count_conflicts_at(li, &layout2, Some(&ctx2));
                if n < best_n { best_n = n; best_x = orig_x + dx; }
            }
            placed[li].pill_center_x = best_x;
            if (best_x - orig_x).abs() > 1e-9 { any_moved = true; }

            if best_n > 0 {
                let layout3 = build_layout(placed, strip_h, top_pad, track_offsets);
                let mut involved: Vec<usize> = Vec::new();
                for j in 0..placed.len() {
                    if j == li { continue; }
                    let mut cflt = false;
                    'outer: for sa in &layout3.segs[li] {
                        for sb in &layout3.segs[j] {
                            if sa.max_x + SEG_MIN_SEP < sb.min_x { continue; }
                            if sb.max_x + SEG_MIN_SEP < sa.min_x { continue; }
                            if sa.max_y + SEG_MIN_SEP < sb.min_y { continue; }
                            if sb.max_y + SEG_MIN_SEP < sa.min_y { continue; }
                            let d = seg_seg_dist(sa.ax, sa.ay, sa.bx, sa.by,
                                                 sb.ax, sb.ay, sb.bx, sb.by);
                            if d < SEG_MIN_SEP { cflt = true; break 'outer; }
                        }
                    }
                    if !cflt {
                        let box_ = layout3.boxes[j];
                        let expanded = Aabb {
                            ql: box_.ql - PILL_PROX, qr: box_.qr + PILL_PROX,
                            qt: box_.qt - PILL_PROX, qb: box_.qb + PILL_PROX,
                        };
                        for s in &layout3.segs[li] {
                            if s.max_x < expanded.ql || s.min_x > expanded.qr { continue; }
                            if s.max_y < expanded.qt || s.min_y > expanded.qb { continue; }
                            if seg_box_overlap(s, &expanded) > 0.0 { cflt = true; break; }
                        }
                    }
                    if cflt { involved.push(j); }
                }

                for &j in &involved {
                    let f_orig = placed[j].pill_center_x;
                    let mut f_best = f_orig;
                    let mut f_best_n = best_n;
                    for &dx in &PUSH_STEPS {
                        placed[j].pill_center_x = f_orig + dx;
                        if pill_has_same_track_collision(j, placed) { continue; }
                        let layout4 = build_layout(placed, strip_h, top_pad, track_offsets);
                        let ctx4 = ObstacleContext {
                            boundaries: &ctx_owned.0,
                            wire_segments: &ctx_owned.1,
                            wall_edges: &ctx_owned.2,
                            placed, strip_h,
                        };
                        let n = count_conflicts_at(li, &layout4, Some(&ctx4));
                        if n < f_best_n { f_best_n = n; f_best = f_orig + dx; }
                    }
                    placed[j].pill_center_x = f_best;
                    if (f_best - f_orig).abs() > 1e-9 {
                        any_moved = true;
                        best_n = f_best_n;
                    }
                }
            }
        }
        if !any_moved { break; }
    }

    // Coordinated pill pair moves.
    {
        let layout0 = build_layout(placed, strip_h, top_pad, track_offsets);
        let ctx0 = ObstacleContext {
            boundaries: &ctx_owned.0,
            wire_segments: &ctx_owned.1,
            wall_edges: &ctx_owned.2,
            placed, strip_h,
        };
        let mut baseline_total = 0i32;
        for i in 0..placed.len() {
            baseline_total += count_conflicts_at(i, &layout0, Some(&ctx0));
        }
        let baseline_total = baseline_total / 2;

        let mut pairs: Vec<(usize, usize, i32)> = Vec::new();
        for i in 0..placed.len() {
            for j in (i + 1)..placed.len() {
                let xc = count_segment_crossings(&layout0.segs[i], &layout0.segs[j]);
                if xc >= 2 { pairs.push((i, j, xc)); }
            }
        }
        pairs.sort_by(|a, b| b.2.cmp(&a.2));

        for &(pi, pj, _) in pairs.iter().take(MAX_PUSH_PAIRS) {
            let a_orig = placed[pi].pill_center_x;
            let b_orig = placed[pj].pill_center_x;
            let mut best_a = a_orig;
            let mut best_b = b_orig;
            let mut best_total = baseline_total;

            for &da in &PAIR_PUSH_SHIFTS {
                placed[pi].pill_center_x = a_orig + da;
                if pill_has_same_track_collision(pi, placed) { continue; }
                for &db in &PAIR_PUSH_SHIFTS {
                    placed[pj].pill_center_x = b_orig + db;
                    if pill_has_same_track_collision(pj, placed) { continue; }
                    let lay = build_layout(placed, strip_h, top_pad, track_offsets);
                    let cx2 = ObstacleContext {
                        boundaries: &ctx_owned.0,
                        wire_segments: &ctx_owned.1,
                        wall_edges: &ctx_owned.2,
                        placed, strip_h,
                    };
                    let mut tot = 0i32;
                    for q in 0..placed.len() {
                        tot += count_conflicts_at(q, &lay, Some(&cx2));
                    }
                    let tot = tot / 2;
                    if (tot as f64) < (best_total as f64) - 0.5 {
                        best_total = tot;
                        best_a = a_orig + da;
                        best_b = b_orig + db;
                    }
                }
            }
            placed[pi].pill_center_x = best_a;
            placed[pj].pill_center_x = best_b;
        }
    }
}

/// Faithful port of `rerouteDoubleCrossingsByHugging`.
pub fn reroute_double_crossings_by_hugging(
    placed: &mut [PlacedItem],
    strip_h: f64, top_pad: f64, track_offsets: &[f64],
    boundaries: &[f64], wire_segments: &[WireSegment], wall_edges: &[WallEdge],
) {
    let max_channel = top_pad - 2.0;
    if max_channel <= HUG_MIN_CHANNEL { return; }

    let mut layout = build_layout(placed, strip_h, top_pad, track_offsets);

    let mut pairs: Vec<(usize, usize, i32)> = Vec::new();
    for i in 0..placed.len() {
        for j in (i + 1)..placed.len() {
            let xc = count_segment_crossings(&layout.segs[i], &layout.segs[j]);
            if xc >= 2 { pairs.push((i, j, xc)); }
        }
    }
    if pairs.is_empty() { return; }
    pairs.sort_by(|a, b| b.2.cmp(&a.2));

    for &(pi, pj, _) in pairs.iter().take(MAX_HUG_PAIRS) {
        layout = build_layout(placed, strip_h, top_pad, track_offsets);
        if count_segment_crossings(&layout.segs[pi], &layout.segs[pj]) < 2 { continue; }

        let ctx = ObstacleContext {
            boundaries, wire_segments, wall_edges, placed, strip_h,
        };
        let total_before = {
            let mut n = 0i32;
            for q in 0..placed.len() {
                n += count_conflicts_at(q, &layout, Some(&ctx));
            }
            n
        };
        let mut best_total = total_before;
        let mut best_move: Option<(usize, i32, f64, f64)> = None;

        for &(idx_self, idx_other) in &[(pi, pj), (pj, pi)] {
            let b_chan_y = placed[idx_other].channel_y_rel;
            let mut b_cols = vec![placed[idx_other].pill_center_x];
            if placed[idx_other].dive_mode != 2 {
                b_cols.push(placed[idx_other].anchor_cx + placed[idx_other].offset_a);
            }
            let b_col_min = b_cols.iter().cloned().fold(f64::INFINITY, f64::min);
            let b_col_max = b_cols.iter().cloned().fold(f64::NEG_INFINITY, f64::max);

            let a_save_dive = placed[idx_self].dive_mode;
            let a_save_chan = placed[idx_self].channel_y_rel;
            let a_save_off  = placed[idx_self].offset_a;

            let mut candidates: Vec<(i32, f64, f64)> = Vec::new();

            let hug_above = b_chan_y - HUG_GAP;
            if hug_above >= HUG_MIN_CHANNEL && hug_above <= max_channel {
                candidates.push((0, hug_above, a_save_off));
                candidates.push((1, hug_above, a_save_off));
            }
            let hug_below = b_chan_y + HUG_GAP;
            if hug_below >= HUG_MIN_CHANNEL && hug_below <= max_channel {
                candidates.push((0, hug_below, a_save_off));
                candidates.push((1, hug_below, a_save_off));
            }
            candidates.push((2, a_save_chan, a_save_off));

            if placed[idx_self].anchor_cx > b_col_min {
                let new_off = (b_col_min - HUG_GAP) - placed[idx_self].anchor_cx;
                candidates.push((0, a_save_chan, new_off));
            }
            if placed[idx_self].anchor_cx < b_col_max {
                let new_off = (b_col_max + HUG_GAP) - placed[idx_self].anchor_cx;
                candidates.push((0, a_save_chan, new_off));
            }

            for &(dm, ch, off) in &candidates {
                if dm == a_save_dive
                    && (ch - a_save_chan).abs() < 0.5
                    && (off - a_save_off).abs() < 0.5
                { continue; }
                if ch < HUG_MIN_CHANNEL || ch > max_channel { continue; }

                placed[idx_self].dive_mode = dm;
                placed[idx_self].channel_y_rel = ch;
                placed[idx_self].offset_a = off;

                let new_layout = build_layout(placed, strip_h, top_pad, track_offsets);
                let new_xc = count_segment_crossings(
                    &new_layout.segs[idx_self], &new_layout.segs[idx_other]);

                if new_xc < 2 {
                    let ctx2 = ObstacleContext {
                        boundaries, wire_segments, wall_edges, placed, strip_h,
                    };
                    let mut tot = 0i32;
                    for q in 0..placed.len() {
                        tot += count_conflicts_at(q, &new_layout, Some(&ctx2));
                    }
                    if tot < best_total {
                        best_total = tot;
                        best_move = Some((idx_self, dm, ch, off));
                    }
                }

                placed[idx_self].dive_mode = a_save_dive;
                placed[idx_self].channel_y_rel = a_save_chan;
                placed[idx_self].offset_a = a_save_off;
            }
        }

        if let Some((idx, dm, ch, off)) = best_move {
            placed[idx].dive_mode = dm;
            placed[idx].channel_y_rel = ch;
            placed[idx].offset_a = off;
        }
    }
}

/// Faithful port of `hugOverhangingLeaders`.
pub fn hug_overhanging_leaders(
    placed: &mut [PlacedItem],
    strip_area_x0: f64, strip_area_x1: f64,
    strip_h: f64, top_pad: f64, track_offsets: &[f64],
) {
    if placed.is_empty() { return; }
    let lo_base = strip_area_x0 + HUG_MARGIN;
    let hi_base = strip_area_x1 - HUG_MARGIN;

    struct Work { idx: usize, path: Vec<Point>, side: u8, depth: f64 }
    let mut work: Vec<Work> = Vec::new();

    for i in 0..placed.len() {
        let it = &placed[i];
        let chan_y = strip_h + it.channel_y_rel;
        let pill_top_y = strip_h + top_pad + track_offsets[it.track];
        let base = compute_leader_path(
            it.anchor_cx, it.anchor_cy_rel, it.offset_a, it.offset_p,
            chan_y, it.pill_center_x, pill_top_y,
            i, placed, strip_h, top_pad, track_offsets, it.dive_mode,
        );
        let bev = bevel_path(&base, it.bevel0, it.bevel1);
        let path = apply_jogs_to_path(&bev, it);

        let mut min_x = f64::INFINITY;
        let mut max_x = f64::NEG_INFINITY;
        for p in &path {
            if p.x < min_x { min_x = p.x; }
            if p.x > max_x { max_x = p.x; }
        }
        let over_l = lo_base - min_x;
        let over_r = max_x - hi_base;
        if over_l <= 0.0 && over_r <= 0.0 { continue; }
        work.push(Work {
            idx: i, path,
            side: if over_l >= over_r { 0 } else { 1 },
            depth: over_l.max(over_r).max(0.0),
        });
    }
    if work.is_empty() { return; }

    work.sort_by(|a, b| b.depth.partial_cmp(&a.depth).unwrap());

    let mut lane_count = [0usize; 2];
    let mut final_paths: Vec<(usize, Vec<Point>)> = Vec::new();

    for job in &work {
        let side_idx = job.side as usize;
        let mut assigned: Option<(usize, Vec<Point>)> = None;

        for lane in lane_count[side_idx]..HUG_MAX_LANES {
            let inset = HUG_MARGIN + (lane as f64) * HUG_LANE_STEP;
            let lo_lane = strip_area_x0 + inset;
            let hi_lane = strip_area_x1 - inset;
            let candidate: Vec<Point> = job.path.iter().enumerate().map(|(k, p)| {
                if k == 0 || k == job.path.len() - 1 {
                    *p
                } else {
                    Point { x: p.x.max(lo_lane).min(hi_lane), y: p.y }
                }
            }).collect();

            let segs = path_segments(&candidate, -1, 0.5);
            let mut clear = true;
            'outer: for (j, other) in placed.iter().enumerate() {
                if j == job.idx { continue; }
                let qt = strip_h + top_pad + track_offsets[other.track];
                let bx = Aabb {
                    ql: other.pill_center_x - other.w / 2.0 - PILL_PROX,
                    qr: other.pill_center_x + other.w / 2.0 + PILL_PROX,
                    qt: qt - PILL_PROX,
                    qb: qt + other.h + PILL_PROX,
                };
                for s in &segs {
                    if s.max_x < bx.ql || s.min_x > bx.qr { continue; }
                    if s.max_y < bx.qt || s.min_y > bx.qb { continue; }
                    if seg_box_overlap(s, &bx) > 0.0 { clear = false; break 'outer; }
                }
            }
            if clear {
                assigned = Some((lane, candidate));
                break;
            }
        }

        let (lane, path) = assigned.unwrap_or_else(|| {
            let inset = HUG_MARGIN + ((HUG_MAX_LANES - 1) as f64) * HUG_LANE_STEP;
            let lo_lane = strip_area_x0 + inset;
            let hi_lane = strip_area_x1 - inset;
            let candidate: Vec<Point> = job.path.iter().enumerate().map(|(k, p)| {
                if k == 0 || k == job.path.len() - 1 {
                    *p
                } else {
                    Point { x: p.x.max(lo_lane).min(hi_lane), y: p.y }
                }
            }).collect();
            (HUG_MAX_LANES - 1, candidate)
        });

        final_paths.push((job.idx, path));
        lane_count[side_idx] = lane_count[side_idx].max(lane + 1);
    }

    for (idx, path) in final_paths {
        placed[idx].final_path = Some(path);
    }
}

/// How much horizontal room the pill has on one side before it runs
/// into a same-track neighbour.  Used by
/// `separate_cross_track_descents` to pick which of two colliding
/// descents to nudge.
fn descent_slack(placed: &[PlacedItem], idx: usize, side: &str) -> f64 {
    let it = &placed[idx];
    let my_l = it.pill_center_x - it.w / 2.0;
    let my_r = it.pill_center_x + it.w / 2.0;
    let my_track = it.track;
    let mut nearest_left  = f64::NEG_INFINITY;
    let mut nearest_right = f64::INFINITY;
    for (k, other) in placed.iter().enumerate() {
        if k == idx { continue; }
        if other.track != my_track { continue; }
        let o_l = other.pill_center_x - other.w / 2.0;
        let o_r = other.pill_center_x + other.w / 2.0;
        if side == "left" && o_r <= my_l {
            nearest_left = nearest_left.max(o_r);
        } else if side == "right" && o_l >= my_r {
            nearest_right = nearest_right.min(o_l);
        }
    }
    if side == "left" {
        if nearest_left == f64::NEG_INFINITY { 1e6 } else { my_l - nearest_left }
    } else {
        if nearest_right == f64::INFINITY { 1e6 } else { nearest_right - my_r }
    }
}

/// Separate vertical tails that descend at nearly the same x across
/// two different tracks.
///
/// The pill push nudges the pill horizontally, but the tail's descent
/// column has always been hard-wired to the pill's centre — so two
/// pills on different tracks whose x happens to land within
/// `SEG_MIN_SEP` of each other produce two parallel verticals that
/// read as one thick stroke.  `offset_p` decouples the descent column
/// from the pill's centre; this pass nudges `offset_p` until the two
/// descents are separated, preferring to move whichever pill has more
/// horizontal room to spare on its own track.
///
/// Runs after the pill push each polish round, so it sees the final
/// pill positions.  Same-track pairs are skipped — their descents go
/// to the same row and are already separated by the same-track pill
/// guard in `separate_pills_in_tracks`.
fn separate_cross_track_descents(
    placed: &mut [PlacedItem],
    _strip_h: f64, top_pad: f64, track_offsets: &[f64],
) {
    const MIN_SEP: f64 = SEG_MIN_SEP;
    const MIN_Y_OVERLAP: f64 = 5.0;
    const MAX_ITERS: usize = 6;

    for _ in 0..MAX_ITERS {
        let mut any_moved = false;
        let n = placed.len();
        for i in 0..n {
            for j in (i + 1)..n {
                if placed[i].track == placed[j].track { continue; }

                let ax = placed[i].pill_center_x + placed[i].offset_p;
                let bx = placed[j].pill_center_x + placed[j].offset_p;
                if (ax - bx).abs() >= MIN_SEP { continue; }

                // Strip-relative y ranges of the two descents: from
                // each leader's channel down to the top of its pill.
                let a_top = placed[i].channel_y_rel;
                let a_bot = top_pad + track_offsets[placed[i].track];
                let b_top = placed[j].channel_y_rel;
                let b_bot = top_pad + track_offsets[placed[j].track];
                let lo_y = a_top.max(b_top);
                let hi_y = a_bot.min(b_bot);
                if hi_y - lo_y < MIN_Y_OVERLAP { continue; }

                let dx = bx - ax;
                let sgn = if dx >= 0.0 { 1.0 } else { -1.0 };
                let push = (MIN_SEP - (ax - bx).abs()) / 2.0 + 0.5;

                let a_slack = descent_slack(placed, i, "left")
                            + descent_slack(placed, i, "right");
                let b_slack = descent_slack(placed, j, "left")
                            + descent_slack(placed, j, "right");

                if a_slack >= b_slack {
                    placed[i].offset_p -= sgn * push;
                } else {
                    placed[j].offset_p += sgn * push;
                }
                any_moved = true;
            }
        }
        if !any_moved { break; }
    }
}

/// Faithful port of `polishLeaderLayout` — the alternating loop of
/// optimize / reroute-hug / pill-push / descent-separation, with
/// best-state preservation.
pub fn polish_leader_layout(
    placed: &mut [PlacedItem],
    strip_h: f64, top_pad: f64, track_offsets: &[f64],
    boundaries: &[f64], wire_segments: &[WireSegment], wall_edges: &[WallEdge],
) {
    let ctx_owned = (
        boundaries.to_vec(),
        wire_segments.to_vec(),
        wall_edges.to_vec(),
    );

    // (bevel0, bevel1, jog0, jog1, dive_mode, channel_y_rel,
    //  offset_a, offset_p, detour_bias, pill_center_x)
    type Snap = Vec<(f64, f64, f64, f64, i32, f64, f64, f64, f64, f64)>;

    let snap = |p: &[PlacedItem]| -> Snap {
        p.iter().map(|x| (
            x.bevel0, x.bevel1, x.jog0, x.jog1,
            x.dive_mode, x.channel_y_rel,
            x.offset_a, x.offset_p,
            x.detour_bias, x.pill_center_x,
        )).collect()
    };
    let restore = |p: &mut [PlacedItem], s: &Snap| {
        for (i, t) in s.iter().enumerate() {
            p[i].bevel0 = t.0;
            p[i].bevel1 = t.1;
            p[i].jog0   = t.2;
            p[i].jog1   = t.3;
            p[i].dive_mode = t.4;
            p[i].channel_y_rel = t.5;
            p[i].offset_a = t.6;
            p[i].offset_p = t.7;
            p[i].detour_bias = t.8;
            p[i].pill_center_x = t.9;
        }
    };

    let count = |p: &[PlacedItem]| -> i32 {
        let layout = build_layout(p, strip_h, top_pad, track_offsets);
        let ctx = ObstacleContext {
            boundaries, wire_segments, wall_edges, placed: p, strip_h,
        };
        let mut n = 0i32;
        for i in 0..p.len() {
            n += count_conflicts_at(i, &layout, Some(&ctx));
        }
        n / 2
    };

    let mut prev = count(placed);
    if prev == 0 { return; }
    let mut best_snap = snap(placed);
    let mut best_score = prev;

    for _ in 0..POLISH_MAX_ROUNDS {
        optimize_leader_geometry(placed, strip_h, top_pad, track_offsets,
                                 boundaries, wire_segments, wall_edges);
        reroute_double_crossings_by_hugging(placed, strip_h, top_pad, track_offsets,
                                            boundaries, wire_segments, wall_edges);
        push_pills_for_leader_conflicts(placed, strip_h, top_pad, track_offsets,
                                        ctx_owned.clone());
        // Runs last so it sees the final pill x positions.
        separate_cross_track_descents(placed, strip_h, top_pad, track_offsets);

        let now = count(placed);
        if now < best_score {
            best_score = now;
            best_snap = snap(placed);
        }
        if now >= prev { break; }
        prev = now;
        if now == 0 { break; }
    }

    restore(placed, &best_snap);
}
