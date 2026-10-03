use crate::constants::*;
use crate::geometry::*;
use crate::path::*;
use crate::penalties::*;
use crate::snapshot::*;
use crate::types::*;

/// Field selector for the pass loop.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
enum Field { Bevel0, Bevel1, Jog0, Jog1, DiveMode, ChannelYRel, OffsetA, DetourBias }

fn get_field(it: &PlacedItem, f: Field) -> f64 {
    match f {
        Field::Bevel0     => it.bevel0,
        Field::Bevel1     => it.bevel1,
        Field::Jog0       => it.jog0,
        Field::Jog1       => it.jog1,
        Field::DiveMode   => it.dive_mode as f64,
        Field::ChannelYRel=> it.channel_y_rel,
        Field::OffsetA    => it.offset_a,
        Field::DetourBias => it.detour_bias,
    }
}
fn set_field(it: &mut PlacedItem, f: Field, v: f64) {
    match f {
        Field::Bevel0     => it.bevel0 = v,
        Field::Bevel1     => it.bevel1 = v,
        Field::Jog0       => it.jog0 = v,
        Field::Jog1       => it.jog1 = v,
        Field::DiveMode   => it.dive_mode = v as i32,
        Field::ChannelYRel=> it.channel_y_rel = v,
        Field::OffsetA    => it.offset_a = v,
        Field::DetourBias => it.detour_bias = v,
    }
}

/// The tier-list score of one candidate layout.  Faithful port of the
/// JS `evaluate()` closure in `optimizeLeaderGeometry`.
fn evaluate(
    placed: &[PlacedItem],
    strip_h: f64, top_pad: f64, track_offsets: &[f64],
    bxs: &[f64], wires: &[WireSegment], wes: &[WallEdge],
) -> f64 {
    let layout = build_layout(placed, strip_h, top_pad, track_offsets);
    let path_segs = &layout.segs;
    let boxes = &layout.boxes;

    let mut all_segs: Vec<Segment> = Vec::new();
    for segs in path_segs {
        for s in segs {
            if s.len >= SEG_MIN_LEN { all_segs.push(*s); }
        }
    }

    // Tier 1: V-V and H-H parallel pairs.
    let mut vv_count = 0i32; let mut vv_depth = 0.0;
    let mut hh_count = 0i32; let mut hh_depth = 0.0;
    for i in 0..all_segs.len() {
        let a = &all_segs[i];
        for j in (i + 1)..all_segs.len() {
            let b = &all_segs[j];
            if a.path_idx == b.path_idx { continue; }
            if a.kind == 0 && b.kind == 0 {
                let dx = (a.ax - b.ax).abs();
                if dx >= SEG_MIN_SEP { continue; }
                let oy0 = a.min_y.max(b.min_y);
                let oy1 = a.max_y.min(b.max_y);
                if oy1 <= oy0 { continue; }
                vv_count += 1; vv_depth += SEG_MIN_SEP - dx;
                continue;
            }
            if a.kind == 1 && b.kind == 1 {
                let dy = (a.ay - b.ay).abs();
                if dy >= SEG_MIN_SEP { continue; }
                let ox0 = a.min_x.max(b.min_x);
                let ox1 = a.max_x.min(b.max_x);
                if ox1 <= ox0 { continue; }
                hh_count += 1; hh_depth += SEG_MIN_SEP - dy;
                continue;
            }
        }
    }

    // Tier 1b: double crossings.
    let mut extra_crossings = 0i32;
    for i in 0..path_segs.len() {
        for j in (i + 1)..path_segs.len() {
            let n = count_segment_crossings(&path_segs[i], &path_segs[j]);
            if n >= 2 { extra_crossings += n - 1; }
        }
    }

    // Tier 2b: pill AABB.
    let mut pill_count = 0i32; let mut pill_depth = 0.0;
    for i in 0..path_segs.len() {
        for s in &path_segs[i] {
            if s.len < 1.0 { continue; }
            for (j, box_) in boxes.iter().enumerate() {
                if j == i { continue; }
                let expanded = Aabb {
                    ql: box_.ql - PILL_PROX, qr: box_.qr + PILL_PROX,
                    qt: box_.qt - PILL_PROX, qb: box_.qb + PILL_PROX,
                };
                if s.max_x < expanded.ql || s.min_x > expanded.qr { continue; }
                if s.max_y < expanded.qt || s.min_y > expanded.qb { continue; }
                let t = seg_box_overlap(s, &expanded);
                if t > 0.0 { pill_count += 1; pill_depth += t; }
            }
        }
    }

    // Tier 2: non-parallel pairs.
    let mut seg_count = 0i32; let mut seg_depth = 0.0;
    for i in 0..all_segs.len() {
        let a = &all_segs[i];
        for j in (i + 1)..all_segs.len() {
            let b = &all_segs[j];
            if a.path_idx == b.path_idx { continue; }
            if a.kind == 0 && b.kind == 0 { continue; }
            if a.kind == 1 && b.kind == 1 { continue; }
            if a.max_x + SEG_MIN_SEP < b.min_x { continue; }
            if b.max_x + SEG_MIN_SEP < a.min_x { continue; }
            if a.max_y + SEG_MIN_SEP < b.min_y { continue; }
            if b.max_y + SEG_MIN_SEP < a.min_y { continue; }
            let d = seg_seg_dist(a.ax, a.ay, a.bx, a.by,
                                 b.ax, b.ay, b.bx, b.by);
            if d < SEG_MIN_SEP { seg_count += 1; seg_depth += SEG_MIN_SEP - d; }
        }
    }

    // Tier 3.
    let bp = boundary_overlap_penalty(&all_segs, bxs, strip_h);
    let wp = wire_overlap_penalty(&all_segs, placed, wires);
    let ep = wall_edge_overlap_penalty(&all_segs, wes);

    // Tier 3d: arrowhead regions.
    const HEAD_HALF: f64 = 5.0;
    let mut head_count = 0i32; let mut head_depth = 0.0;
    for (i, it) in placed.iter().enumerate() {
        let anchor_pt = (it.anchor_cx, it.anchor_cy_rel);
        let pill_pt = (it.pill_center_x, strip_h + top_pad + track_offsets[it.track]);
        for &(hx, hy) in &[anchor_pt, pill_pt] {
            let box_ = Aabb { ql: hx - HEAD_HALF, qr: hx + HEAD_HALF,
                              qt: hy - HEAD_HALF, qb: hy + HEAD_HALF };
            for (j, psegs) in path_segs.iter().enumerate() {
                if j == i { continue; }
                for s in psegs {
                    if s.max_x < box_.ql || s.min_x > box_.qr { continue; }
                    if s.max_y < box_.qt || s.min_y > box_.qb { continue; }
                    if seg_box_overlap(s, &box_) > 0.0 {
                        head_count += 1;
                        head_depth += HEAD_HALF;
                    }
                }
            }
        }
    }

    // Tier 4: corner features.
    let mut corners: Vec<CornerFeature> = Vec::with_capacity(placed.len() * 2);
    for (pi, it) in placed.iter().enumerate() {
        corners.push(corner_feature(it, pi, placed, strip_h, top_pad, track_offsets, 0));
        corners.push(corner_feature(it, pi, placed, strip_h, top_pad, track_offsets, 1));
    }
    let mut corner_count = 0i32; let mut corner_depth = 0.0;

    for i in 0..corners.len() {
        for j in (i + 1)..corners.len() {
            let a = &corners[i]; let b = &corners[j];
            if a.path_idx == b.path_idx { continue; }
            let d = corner_dist(a, b);
            if d < CORNER_MIN_DIST {
                corner_count += 1; corner_depth += CORNER_MIN_DIST - d;
            }
        }
    }
    for cf in &corners {
        let cf_is_pt = (cf.x0 - cf.x1).abs() < 0.5 && (cf.y0 - cf.y1).abs() < 0.5;
        for s in &all_segs {
            if s.path_idx as usize == cf.path_idx { continue; }
            let d = if cf_is_pt {
                point_seg_dist(cf.x0, cf.y0, s.ax, s.ay, s.bx, s.by)
            } else {
                seg_seg_dist(cf.x0, cf.y0, cf.x1, cf.y1, s.ax, s.ay, s.bx, s.by)
            };
            if d < CORNER_MIN_DIST { corner_count += 1; corner_depth += CORNER_MIN_DIST - d; }
        }
    }
    for cf in &corners {
        let cf_is_pt = (cf.x0 - cf.x1).abs() < 0.5 && (cf.y0 - cf.y1).abs() < 0.5;
        for (bi, box_) in boxes.iter().enumerate() {
            if bi == cf.path_idx { continue; }
            let hit = if cf_is_pt {
                cf.x0 >= box_.ql && cf.x0 <= box_.qr &&
                cf.y0 >= box_.qt && cf.y0 <= box_.qb
            } else {
                let s = Segment {
                    ax: cf.x0, ay: cf.y0, bx: cf.x1, by: cf.y1,
                    min_x: cf.x0.min(cf.x1), max_x: cf.x0.max(cf.x1),
                    min_y: cf.y0.min(cf.y1), max_y: cf.y0.max(cf.y1),
                    angle: 0.0, len: 0.0, path_idx: -1, kind: 0,
                };
                seg_box_overlap(&s, box_) > 0.0
            };
            if hit { corner_count += 1; corner_depth += CORNER_MIN_DIST; }
        }
    }

    let score =
        vv_count as f64     * 1e12 + vv_depth           * 1e11 +
        hh_count as f64     * 1e12 + hh_depth           * 1e11 +
        pill_count as f64   * 5e11 + pill_depth         * 5e10 +
        wp.par_count as f64 * 1e11 + wp.par_depth       * 1e10 +
        ep.count as f64     * 1e11 + ep.depth           * 1e10 +
        bp.count as f64     * 1e11 + bp.depth           * 1e10 +
        head_count as f64   * 1e10 + head_depth         * 1e9  +
        extra_crossings as f64 * DOUBLE_CROSS_EXTRA_W +
        seg_count as f64    * 1e9  + seg_depth          * 1e8  +
        wp.count as f64     * 1e9  + wp.depth           * 1e8  +
        wp.approach_count as f64 * 1e7 + wp.approach_depth * 1e6 +
        corner_count as f64 * 1e6  + corner_depth       * 1e5;
    score
}

pub fn optimize_leader_geometry(
    placed: &mut [PlacedItem],
    strip_h: f64, top_pad: f64, track_offsets: &[f64],
    boundaries: &[f64], wire_segments: &[WireSegment], wall_edges: &[WallEdge],
) {
    for it in placed.iter_mut() {
        it.bevel0 = 0.0; it.bevel1 = 0.0;
        it.jog0 = 0.0; it.jog1 = 0.0;
        it.dive_mode = 0;
        if !it.detour_bias.is_finite() { it.detour_bias = 0.0; }
    }
    if placed.len() < 2 { return; }

    let mut cur = evaluate(placed, strip_h, top_pad, track_offsets,
                           boundaries, wire_segments, wall_edges);
    if cur < 1.0 { return; }

    // ---- Main pass loop ----
    for _ in 0..OPT_MAX_PASSES {
        let layout = build_layout(placed, strip_h, top_pad, track_offsets);
        let ctx = ObstacleContext {
            boundaries, wire_segments, wall_edges,
            placed, strip_h,
        };
        let conflicted = conflicted_leader_indices(&layout, Some(&ctx));

        let mut best: Option<(usize, Field, f64, f64)> = None;

        for li in 0..placed.len() {
            if !conflicted[li] { continue; }

            // Bevel and jog fields
            for &f in &[Field::Bevel0, Field::Bevel1, Field::Jog0, Field::Jog1] {
                let cur_val = get_field(&placed[li], f);
                let is_bevel = matches!(f, Field::Bevel0 | Field::Bevel1);
                for cand in candidates_for(cur_val, is_bevel) {
                    let saved = get_field(&placed[li], f);
                    set_field(&mut placed[li], f, cand);
                    let test = evaluate(placed, strip_h, top_pad, track_offsets,
                                        boundaries, wire_segments, wall_edges);
                    set_field(&mut placed[li], f, saved);
                    if test < cur - 0.5 {
                        if best.is_none() || test < best.as_ref().unwrap().3 {
                            best = Some((li, f, cand, test));
                        }
                    }
                }
            }

            // Dive mode
            let cur_mode = placed[li].dive_mode;
            for &cand in &DIVE_MODES {
                if cand == cur_mode { continue; }
                let saved = placed[li].dive_mode;
                placed[li].dive_mode = cand;
                let test = evaluate(placed, strip_h, top_pad, track_offsets,
                                    boundaries, wire_segments, wall_edges);
                placed[li].dive_mode = saved;
                if test < cur - 0.5 {
                    if best.is_none() || test < best.as_ref().unwrap().3 {
                        best = Some((li, Field::DiveMode, cand as f64, test));
                    }
                }
            }

            // Channel Y
            let cur_lane = placed[li].channel_y_rel;
            for &dy in &CHANNEL_Y_STEPS {
                let new_lane = cur_lane + dy;
                if new_lane < 2.0 { continue; }
                if new_lane >= top_pad - 2.0 { continue; }
                let saved = placed[li].channel_y_rel;
                placed[li].channel_y_rel = new_lane;
                let test = evaluate(placed, strip_h, top_pad, track_offsets,
                                    boundaries, wire_segments, wall_edges);
                placed[li].channel_y_rel = saved;
                if test < cur - 0.5 {
                    if best.is_none() || test < best.as_ref().unwrap().3 {
                        best = Some((li, Field::ChannelYRel, new_lane, test));
                    }
                }
            }

            // Offset A
            let cur_off = placed[li].offset_a;
            for &doff in &OFFSET_A_STEPS {
                let new_off = cur_off + doff;
                let saved = placed[li].offset_a;
                placed[li].offset_a = new_off;
                let test = evaluate(placed, strip_h, top_pad, track_offsets,
                                    boundaries, wire_segments, wall_edges);
                placed[li].offset_a = saved;
                if test < cur - 0.5 {
                    if best.is_none() || test < best.as_ref().unwrap().3 {
                        best = Some((li, Field::OffsetA, new_off, test));
                    }
                }
            }

            // Detour bias
            let cur_bias = placed[li].detour_bias;
            for &cand in &DETOUR_BIAS_STEPS {
                if (cand - cur_bias).abs() < 1e-9 { continue; }
                let saved = placed[li].detour_bias;
                placed[li].detour_bias = cand;
                let test = evaluate(placed, strip_h, top_pad, track_offsets,
                                    boundaries, wire_segments, wall_edges);
                placed[li].detour_bias = saved;
                if test < cur - 0.5 {
                    if best.is_none() || test < best.as_ref().unwrap().3 {
                        best = Some((li, Field::DetourBias, cand, test));
                    }
                }
            }
        }

        match best {
            Some((li, f, v, test)) => {
                set_field(&mut placed[li], f, v);
                cur = test;
                if cur < 1.0 { break; }
            }
            None => break,
        }
    }

    // ---- Coordinated pair refinement ----
    for _ in 0..PAIR_REFINE_ITERS {
        let layout0 = build_layout(placed, strip_h, top_pad, track_offsets);
        let mut bad_pairs: Vec<(usize, usize, i32)> = Vec::new();
        for i in 0..placed.len() {
            for j in (i + 1)..placed.len() {
                let xc = count_segment_crossings(&layout0.segs[i], &layout0.segs[j]);
                if xc >= 2 { bad_pairs.push((i, j, xc)); }
            }
        }
        bad_pairs.sort_by(|a, b| b.2.cmp(&a.2));

        let mut any_improved = false;

        // Pass 1 — 2-way.
        let np = bad_pairs.len().min(MAX_PAIRS_2WAY);
        for &(i, j, _) in bad_pairs.iter().take(np) {
            let a_lane0 = placed[i].channel_y_rel;
            let a_off0  = placed[i].offset_a;
            let a_bias0 = placed[i].detour_bias;
            let b_lane0 = placed[j].channel_y_rel;
            let b_off0  = placed[j].offset_a;
            let b_bias0 = placed[j].detour_bias;

            let mut best_score = cur;
            let (mut best_al, mut best_ao, mut best_ab) = (a_lane0, a_off0, a_bias0);
            let (mut best_bl, mut best_bo, mut best_bb) = (b_lane0, b_off0, b_bias0);

            // lane × lane
            for &al in &LANE_CHOICES {
                if al >= top_pad - 2.0 { continue; }
                placed[i].channel_y_rel = al;
                for &bl in &LANE_CHOICES {
                    if bl >= top_pad - 2.0 { continue; }
                    placed[j].channel_y_rel = bl;
                    let s = evaluate(placed, strip_h, top_pad, track_offsets,
                                     boundaries, wire_segments, wall_edges);
                    if s < best_score - 0.5 {
                        best_score = s;
                        best_al = al; best_bl = bl;
                        best_ao = a_off0; best_bo = b_off0;
                        best_ab = a_bias0; best_bb = b_bias0;
                    }
                }
            }
            placed[i].channel_y_rel = a_lane0;
            placed[j].channel_y_rel = b_lane0;

            // off × off
            for &ao in &OFFSET_A_ABS {
                placed[i].offset_a = ao;
                for &bo in &OFFSET_A_ABS {
                    placed[j].offset_a = bo;
                    let s = evaluate(placed, strip_h, top_pad, track_offsets,
                                     boundaries, wire_segments, wall_edges);
                    if s < best_score - 0.5 {
                        best_score = s;
                        best_ao = ao; best_bo = bo;
                        best_al = a_lane0; best_bl = b_lane0;
                        best_ab = a_bias0; best_bb = b_bias0;
                    }
                }
            }
            placed[i].offset_a = a_off0;
            placed[j].offset_a = b_off0;

            // bias × bias
            for &ab in &BIAS_CHOICES {
                placed[i].detour_bias = ab;
                for &bb in &BIAS_CHOICES {
                    placed[j].detour_bias = bb;
                    let s = evaluate(placed, strip_h, top_pad, track_offsets,
                                     boundaries, wire_segments, wall_edges);
                    if s < best_score - 0.5 {
                        best_score = s;
                        best_ab = ab; best_bb = bb;
                        best_al = a_lane0; best_bl = b_lane0;
                        best_ao = a_off0; best_bo = b_off0;
                    }
                }
            }
            placed[i].detour_bias = a_bias0;
            placed[j].detour_bias = b_bias0;

            // lane × off
            for &al in &LANE_CHOICES {
                if al >= top_pad - 2.0 { continue; }
                placed[i].channel_y_rel = al;
                for &bo in &OFFSET_A_ABS {
                    placed[j].offset_a = bo;
                    let s = evaluate(placed, strip_h, top_pad, track_offsets,
                                     boundaries, wire_segments, wall_edges);
                    if s < best_score - 0.5 {
                        best_score = s;
                        best_al = al; best_bo = bo;
                        best_bl = b_lane0; best_ao = a_off0;
                        best_ab = a_bias0; best_bb = b_bias0;
                    }
                }
            }
            placed[i].channel_y_rel = a_lane0; placed[j].offset_a = b_off0;

            // off × lane
            for &ao in &OFFSET_A_ABS {
                placed[i].offset_a = ao;
                for &bl in &LANE_CHOICES {
                    if bl >= top_pad - 2.0 { continue; }
                    placed[j].channel_y_rel = bl;
                    let s = evaluate(placed, strip_h, top_pad, track_offsets,
                                     boundaries, wire_segments, wall_edges);
                    if s < best_score - 0.5 {
                        best_score = s;
                        best_ao = ao; best_bl = bl;
                        best_al = a_lane0; best_bo = b_off0;
                        best_ab = a_bias0; best_bb = b_bias0;
                    }
                }
            }
            placed[i].offset_a = a_off0; placed[j].channel_y_rel = b_lane0;

            // lane × bias
            for &al in &LANE_CHOICES {
                if al >= top_pad - 2.0 { continue; }
                placed[i].channel_y_rel = al;
                for &bb in &BIAS_CHOICES {
                    placed[j].detour_bias = bb;
                    let s = evaluate(placed, strip_h, top_pad, track_offsets,
                                     boundaries, wire_segments, wall_edges);
                    if s < best_score - 0.5 {
                        best_score = s;
                        best_al = al; best_bb = bb;
                        best_bl = b_lane0; best_ab = a_bias0;
                        best_ao = a_off0; best_bo = b_off0;
                    }
                }
            }
            placed[i].channel_y_rel = a_lane0; placed[j].detour_bias = b_bias0;

            // bias × lane
            for &ab in &BIAS_CHOICES {
                placed[i].detour_bias = ab;
                for &bl in &LANE_CHOICES {
                    if bl >= top_pad - 2.0 { continue; }
                    placed[j].channel_y_rel = bl;
                    let s = evaluate(placed, strip_h, top_pad, track_offsets,
                                     boundaries, wire_segments, wall_edges);
                    if s < best_score - 0.5 {
                        best_score = s;
                        best_ab = ab; best_bl = bl;
                        best_al = a_lane0; best_bb = b_bias0;
                        best_ao = a_off0; best_bo = b_off0;
                    }
                }
            }
            placed[i].detour_bias = a_bias0; placed[j].channel_y_rel = b_lane0;

            // off × bias
            for &ao in &OFFSET_A_ABS {
                placed[i].offset_a = ao;
                for &bb in &BIAS_CHOICES {
                    placed[j].detour_bias = bb;
                    let s = evaluate(placed, strip_h, top_pad, track_offsets,
                                     boundaries, wire_segments, wall_edges);
                    if s < best_score - 0.5 {
                        best_score = s;
                        best_ao = ao; best_bb = bb;
                        best_al = a_lane0; best_bl = b_lane0;
                        best_ab = a_bias0; best_bo = b_off0;
                    }
                }
            }
            placed[i].offset_a = a_off0; placed[j].detour_bias = b_bias0;

            // bias × off
            for &ab in &BIAS_CHOICES {
                placed[i].detour_bias = ab;
                for &bo in &OFFSET_A_ABS {
                    placed[j].offset_a = bo;
                    let s = evaluate(placed, strip_h, top_pad, track_offsets,
                                     boundaries, wire_segments, wall_edges);
                    if s < best_score - 0.5 {
                        best_score = s;
                        best_ab = ab; best_bo = bo;
                        best_al = a_lane0; best_bl = b_lane0;
                        best_ao = a_off0; best_bb = b_bias0;
                    }
                }
            }
            placed[i].detour_bias = a_bias0; placed[j].offset_a = b_off0;

            placed[i].channel_y_rel = best_al;
            placed[i].offset_a      = best_ao;
            placed[i].detour_bias   = best_ab;
            placed[j].channel_y_rel = best_bl;
            placed[j].offset_a      = best_bo;
            placed[j].detour_bias   = best_bb;

            if best_score < cur - 0.5 {
                cur = best_score;
                any_improved = true;
            }
        }

        // Pass 1b — wire-avoidance coordinated.
        {
            let layout1b = build_layout(placed, strip_h, top_pad, track_offsets);
            for li in 0..placed.len() {
                let wpl = wire_overlap_penalty(&layout1b.segs[li], placed, wire_segments);
                if wpl.count == 0 && wpl.approach_count == 0 { continue; }

                let orig_dive = placed[li].dive_mode;
                let orig_chan = placed[li].channel_y_rel;
                let orig_off  = placed[li].offset_a;

                let mut best_score = cur;
                let (mut bd, mut bc, mut bo) = (orig_dive, orig_chan, orig_off);

                for &dm in &DIVE_MODES {
                    if dm == orig_dive { continue; }
                    placed[li].dive_mode = dm;
                    for &cl in &LANE_CHOICES {
                        if cl >= top_pad - 2.0 || (cl - orig_chan).abs() < 1e-9 { continue; }
                        placed[li].channel_y_rel = cl;
                        let s = evaluate(placed, strip_h, top_pad, track_offsets,
                                         boundaries, wire_segments, wall_edges);
                        if s < best_score - 0.5 {
                            best_score = s;
                            bd = dm; bc = cl; bo = orig_off;
                        }
                    }
                }
                placed[li].dive_mode = orig_dive;
                placed[li].channel_y_rel = orig_chan;

                for &dm in &DIVE_MODES {
                    if dm == orig_dive { continue; }
                    placed[li].dive_mode = dm;
                    for &co in &OFFSET_A_ABS {
                        if (co - orig_off).abs() < 1e-9 { continue; }
                        placed[li].offset_a = co;
                        let s = evaluate(placed, strip_h, top_pad, track_offsets,
                                         boundaries, wire_segments, wall_edges);
                        if s < best_score - 0.5 {
                            best_score = s;
                            bd = dm; bc = orig_chan; bo = co;
                        }
                    }
                }
                placed[li].dive_mode = orig_dive;
                placed[li].offset_a = orig_off;

                if bd != orig_dive || (bc - orig_chan).abs() > 1e-9 || (bo - orig_off).abs() > 1e-9 {
                    placed[li].dive_mode = bd;
                    placed[li].channel_y_rel = bc;
                    placed[li].offset_a = bo;
                    cur = best_score;
                    any_improved = true;
                }
            }
        }

        // Pass 2 — 3-way with bystander.
        let np2 = bad_pairs.len().min(MAX_PAIRS_3WAY);
        for &(i, j, _) in bad_pairs.iter().take(np2) {
            let mut cps: Vec<(f64, f64)> = Vec::new();
            for sa in &layout0.segs[i] {
                for sb in &layout0.segs[j] {
                    if !seg_seg_proper_cross(sa, sb) { continue; }
                    let d1x = sa.bx - sa.ax; let d1y = sa.by - sa.ay;
                    let d2x = sb.bx - sb.ax; let d2y = sb.by - sb.ay;
                    let denom = d1x * d2y - d1y * d2x;
                    let rx = sb.ax - sa.ax; let ry = sb.ay - sa.ay;
                    let t = (rx * d2y - ry * d2x) / denom;
                    cps.push((sa.ax + t * d1x, sa.ay + t * d1y));
                }
            }
            if cps.len() < 2 { continue; }

            let mut candidates: Vec<(usize, f64)> = Vec::new();
            for q in 0..placed.len() {
                if q == i || q == j { continue; }
                let mut min_d = f64::INFINITY;
                for &(cx, cy) in &cps {
                    for s in &layout0.segs[q] {
                        let d = point_seg_dist(cx, cy, s.ax, s.ay, s.bx, s.by);
                        if d < min_d { min_d = d; }
                    }
                }
                if min_d < BYSTANDER_RADIUS { candidates.push((q, min_d)); }
            }
            candidates.sort_by(|a, b| a.1.partial_cmp(&b.1).unwrap());
            if candidates.is_empty() { continue; }

            let a_save = (placed[i].dive_mode, placed[i].channel_y_rel, placed[i].offset_a, placed[i].detour_bias);
            let mut fixed = false;

            let limit = MAX_BYSTANDERS.min(candidates.len());
            for &(bystander_idx, _) in candidates.iter().take(limit) {
                if fixed { break; }
                let c_save = (placed[bystander_idx].dive_mode,
                              placed[bystander_idx].channel_y_rel,
                              placed[bystander_idx].offset_a,
                              placed[bystander_idx].detour_bias);

                let mut best_score = cur;
                let mut best_a_move: Option<(Field, f64)> = None;
                let mut best_c_move: Option<(Field, f64)> = None;

                // (diveMode, diveMode)
                for &ad in &DIVE_MODES {
                    if ad == a_save.0 { continue; }
                    placed[i].dive_mode = ad;
                    for &cd in &DIVE_MODES {
                        if cd == c_save.0 { continue; }
                        placed[bystander_idx].dive_mode = cd;
                        let s = evaluate(placed, strip_h, top_pad, track_offsets,
                                         boundaries, wire_segments, wall_edges);
                        if s < best_score - 0.5 {
                            best_score = s;
                            best_a_move = Some((Field::DiveMode, ad as f64));
                            best_c_move = Some((Field::DiveMode, cd as f64));
                        }
                    }
                }
                placed[i].dive_mode = a_save.0;
                placed[bystander_idx].dive_mode = c_save.0;

                // (diveMode, channelYRel)
                for &ad in &DIVE_MODES {
                    if ad == a_save.0 { continue; }
                    placed[i].dive_mode = ad;
                    for &cl in &LANE_CHOICES {
                        if cl >= top_pad - 2.0 || (cl - c_save.1).abs() < 1e-9 { continue; }
                        placed[bystander_idx].channel_y_rel = cl;
                        let s = evaluate(placed, strip_h, top_pad, track_offsets,
                                         boundaries, wire_segments, wall_edges);
                        if s < best_score - 0.5 {
                            best_score = s;
                            best_a_move = Some((Field::DiveMode, ad as f64));
                            best_c_move = Some((Field::ChannelYRel, cl));
                        }
                    }
                }
                placed[i].dive_mode = a_save.0;
                placed[bystander_idx].channel_y_rel = c_save.1;

                // (diveMode, offsetA)
                for &ad in &DIVE_MODES {
                    if ad == a_save.0 { continue; }
                    placed[i].dive_mode = ad;
                    for &co in &OFFSET_A_ABS {
                        if (co - c_save.2).abs() < 1e-9 { continue; }
                        placed[bystander_idx].offset_a = co;
                        let s = evaluate(placed, strip_h, top_pad, track_offsets,
                                         boundaries, wire_segments, wall_edges);
                        if s < best_score - 0.5 {
                            best_score = s;
                            best_a_move = Some((Field::DiveMode, ad as f64));
                            best_c_move = Some((Field::OffsetA, co));
                        }
                    }
                }
                placed[i].dive_mode = a_save.0;
                placed[bystander_idx].offset_a = c_save.2;

                if let (Some((af, av)), Some((cf, cv))) = (best_a_move, best_c_move) {
                    set_field(&mut placed[i], af, av);
                    set_field(&mut placed[bystander_idx], cf, cv);
                    cur = best_score;
                    any_improved = true;
                    fixed = true;
                }
            }
        }

        if !any_improved { break; }
    }
}
