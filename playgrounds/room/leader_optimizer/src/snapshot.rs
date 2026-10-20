use crate::constants::*;
use crate::geometry::*;
use crate::path::*;
use crate::penalties::*;
use crate::types::*;

pub struct Layout {
    pub paths: Vec<Vec<Point>>,
    pub segs:  Vec<Vec<Segment>>,
    pub boxes: Vec<Aabb>,
}

pub fn build_layout(
    placed: &[PlacedItem], strip_h: f64, top_pad: f64, track_offsets: &[f64],
) -> Layout {
    let n = placed.len();
    let mut paths: Vec<Vec<Point>>   = Vec::with_capacity(n);
    let mut segs:  Vec<Vec<Segment>> = Vec::with_capacity(n);
    let mut boxes: Vec<Aabb>         = Vec::with_capacity(n);
    for (i, it) in placed.iter().enumerate() {
        let p = if let Some(fp) = &it.final_path {
            fp.clone()
        } else {
            let base = build_leader_path_rel(it, i, placed, strip_h, top_pad, track_offsets);
            let bev = bevel_path(&base, it.bevel0, it.bevel1);
            apply_jogs_to_path(&bev, it)
        };
        let s = path_segments(&p, i as i32, 1.0);
        let qt = strip_h + top_pad + track_offsets[it.track];
        let b = Aabb {
            ql: it.pill_center_x - it.w / 2.0,
            qr: it.pill_center_x + it.w / 2.0,
            qt, qb: qt + it.h,
        };
        paths.push(p); segs.push(s); boxes.push(b);
    }
    Layout { paths, segs, boxes }
}

pub fn expand_box(b: &Aabb, prox: f64) -> Aabb {
    Aabb { ql: b.ql - prox, qr: b.qr + prox,
           qt: b.qt - prox, qb: b.qb + prox }
}

/// The obstacle context carrying the wire/boundary/wall-edge sets the
/// counter needs.  Borrowed to avoid clones in the hot loop.
pub struct ObstacleContext<'a> {
    pub boundaries: &'a [f64],
    pub wire_segments: &'a [WireSegment],
    pub wall_edges: &'a [WallEdge],
    pub placed: &'a [PlacedItem],
    pub strip_h: f64,
}

pub fn count_conflicts_at(
    idx: usize, layout: &Layout, ctx: Option<&ObstacleContext>,
) -> i32 {
    let self_segs = &layout.segs[idx];
    let mut n: i32 = 0;

    for j in 0..layout.segs.len() {
        if j == idx { continue; }
        let other_segs = &layout.segs[j];

        for sa in self_segs {
            for sb in other_segs {
                if sa.max_x + SEG_MIN_SEP < sb.min_x { continue; }
                if sb.max_x + SEG_MIN_SEP < sa.min_x { continue; }
                if sa.max_y + SEG_MIN_SEP < sb.min_y { continue; }
                if sb.max_y + SEG_MIN_SEP < sa.min_y { continue; }
                let d = seg_seg_dist(sa.ax, sa.ay, sa.bx, sa.by,
                                     sb.ax, sb.ay, sb.bx, sb.by);
                if d < SEG_MIN_SEP { n += 1; }
            }
        }

        let expanded = expand_box(&layout.boxes[j], PILL_PROX);
        for s in self_segs {
            if s.max_x < expanded.ql || s.min_x > expanded.qr { continue; }
            if s.max_y < expanded.qt || s.min_y > expanded.qb { continue; }
            if seg_box_overlap(s, &expanded) > 0.0 { n += 1; }
        }

        let xc = count_segment_crossings(self_segs, other_segs);
        if xc >= 2 { n += xc - 1; }
    }

    if let Some(c) = ctx {
        if !c.boundaries.is_empty() {
            n += boundary_overlap_penalty(self_segs, c.boundaries, c.strip_h).count;
        }
        if !c.wall_edges.is_empty() {
            n += wall_edge_overlap_penalty(self_segs, c.wall_edges).count;
        }
        if !c.wire_segments.is_empty() {
            let w = wire_overlap_penalty(self_segs, c.placed, c.wire_segments);
            n += w.par_count * 2 + (w.count - w.par_count);
        }
    }
    n
}

pub fn leader_has_conflict_at(
    idx: usize, layout: &Layout, ctx: Option<&ObstacleContext>,
) -> bool {
    let self_segs = &layout.segs[idx];
    for j in 0..layout.segs.len() {
        if j == idx { continue; }
        let other_segs = &layout.segs[j];
        for sa in self_segs {
            for sb in other_segs {
                if sa.max_x + SEG_MIN_SEP < sb.min_x { continue; }
                if sb.max_x + SEG_MIN_SEP < sa.min_x { continue; }
                if sa.max_y + SEG_MIN_SEP < sb.min_y { continue; }
                if sb.max_y + SEG_MIN_SEP < sa.min_y { continue; }
                let d = seg_seg_dist(sa.ax, sa.ay, sa.bx, sa.by,
                                     sb.ax, sb.ay, sb.bx, sb.by);
                if d < SEG_MIN_SEP { return true; }
            }
        }
        let expanded = expand_box(&layout.boxes[j], PILL_PROX);
        for s in self_segs {
            if s.max_x < expanded.ql || s.min_x > expanded.qr { continue; }
            if s.max_y < expanded.qt || s.min_y > expanded.qb { continue; }
            if seg_box_overlap(s, &expanded) > 0.0 { return true; }
        }
        let xc = count_segment_crossings(self_segs, other_segs);
        if xc >= 2 { return true; }
    }
    if let Some(c) = ctx {
        if !c.boundaries.is_empty() &&
            boundary_overlap_penalty(self_segs, c.boundaries, c.strip_h).count > 0
        { return true; }
        if !c.wall_edges.is_empty() &&
            wall_edge_overlap_penalty(self_segs, c.wall_edges).count > 0
        { return true; }
        if !c.wire_segments.is_empty() {
            let w = wire_overlap_penalty(self_segs, c.placed, c.wire_segments);
            if w.par_count > 0 { return true; }
        }
    }
    false
}

pub fn conflicted_leader_indices(
    layout: &Layout, ctx: Option<&ObstacleContext>,
) -> Vec<bool> {
    (0..layout.segs.len())
        .map(|i| leader_has_conflict_at(i, layout, ctx))
        .collect()
}
