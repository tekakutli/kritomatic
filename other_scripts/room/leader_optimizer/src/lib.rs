pub mod constants;
pub mod geometry;
pub mod channel;
pub mod path;
pub mod snapshot;
pub mod penalties;
pub mod refine;
pub mod optimize;
pub mod pillpush;
pub mod types;

use types::*;

pub fn optimize_one(req: OptimizeRequest) -> OptimizeResponse {
    let cable_id = req.cable_id;
    let mut placed: Vec<PlacedItem> = req.placed.into_iter().map(PlacedItem::from).collect();

    let wire_segments: Vec<WireSegment> =
        req.wire_segments.into_iter().map(WireSegment::from).collect();
    let wall_edges: Vec<WallEdge> = req.wall_edges.into_iter()
        .map(|w| WallEdge { x0: w.x0, x1: w.x1, y: w.y })
        .collect();

    refine::refine_leader_offsets(&mut placed, req.strip_h, req.top_pad, &req.track_offsets);
    pillpush::polish_leader_layout(
        &mut placed, req.strip_h, req.top_pad, &req.track_offsets,
        &req.boundaries, &wire_segments, &wall_edges,
    );
    pillpush::hug_overhanging_leaders(
        &mut placed, req.strip_area_x0, req.strip_area_x0 + req.strip_area_w,
        req.strip_h, req.top_pad, &req.track_offsets,
    );

    OptimizeResponse {
        cable_id,
        placed: placed.into_iter().map(PlacedOut::from).collect(),
    }
}

pub fn optimize_batch(req: BatchRequest) -> BatchResponse {
    BatchResponse {
        cables: req.cables.into_iter().map(optimize_one).collect(),
    }
}
