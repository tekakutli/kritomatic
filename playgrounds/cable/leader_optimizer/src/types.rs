use serde::{Deserialize, Serialize};

#[derive(Clone, Copy, Debug, Default, Serialize, Deserialize)]
pub struct Point { pub x: f64, pub y: f64 }

#[derive(Clone, Copy, Debug)]
pub struct Segment {
    pub ax: f64, pub ay: f64, pub bx: f64, pub by: f64,
    pub min_x: f64, pub max_x: f64,
    pub min_y: f64, pub max_y: f64,
    pub angle: f64,
    pub len: f64,
    pub path_idx: i32,
    pub kind: u8, // 0 = vert, 1 = horiz, 2 = diag
}

#[derive(Clone, Copy, Debug)]
pub struct Aabb { pub ql: f64, pub qr: f64, pub qt: f64, pub qb: f64 }

#[derive(Clone, Copy, Debug, Deserialize)]
pub struct WireSegmentIn {
    pub ax: f64, pub ay: f64, pub bx: f64, pub by: f64,
}

#[derive(Clone, Copy, Debug)]
pub struct WireSegment {
    pub ax: f64, pub ay: f64, pub bx: f64, pub by: f64,
    pub angle: f64,
}

impl From<WireSegmentIn> for WireSegment {
    fn from(w: WireSegmentIn) -> Self {
        Self {
            ax: w.ax, ay: w.ay, bx: w.bx, by: w.by,
            angle: (w.by - w.ay).atan2(w.bx - w.ax),
        }
    }
}

#[derive(Clone, Copy, Debug, Deserialize)]
pub struct WallEdgeIn { pub x0: f64, pub x1: f64, pub y: f64 }

#[derive(Clone, Copy, Debug)]
pub struct WallEdge { pub x0: f64, pub x1: f64, pub y: f64 }

#[derive(Clone, Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct PlacedIn {
    pub anchor_cx: f64,
    pub anchor_cy_rel: f64,
    pub w: f64,
    pub h: f64,
    pub track: usize,
    pub pill_center_x: f64,
    pub channel_y_rel: f64,
    #[serde(default)] pub offset_a: f64,
    /// Horizontal offset of the tail's vertical descent column from
    /// the pill's centre.  Breaks cross-track descents that would
    /// otherwise sit at the same x and read as one thick line.
    /// See `separate_cross_track_descents` in pillpush.rs.
    #[serde(default)] pub offset_p: f64,
    #[serde(default)] pub bevel0: f64,
    #[serde(default)] pub bevel1: f64,
    #[serde(default)] pub jog0: f64,
    #[serde(default)] pub jog1: f64,
    #[serde(default)] pub dive_mode: i32,
    #[serde(default)] pub detour_bias: f64,
}

#[derive(Clone, Debug)]
pub struct PlacedItem {
    pub anchor_cx: f64,
    pub anchor_cy_rel: f64,
    pub w: f64,
    pub h: f64,
    pub track: usize,
    pub pill_center_x: f64,
    pub channel_y_rel: f64,
    pub offset_a: f64,
    pub offset_p: f64,
    pub bevel0: f64,
    pub bevel1: f64,
    pub jog0: f64,
    pub jog1: f64,
    pub dive_mode: i32,
    pub detour_bias: f64,
    pub final_path: Option<Vec<Point>>,
}

impl From<PlacedIn> for PlacedItem {
    fn from(p: PlacedIn) -> Self {
        Self {
            anchor_cx: p.anchor_cx,
            anchor_cy_rel: p.anchor_cy_rel,
            w: p.w, h: p.h, track: p.track,
            pill_center_x: p.pill_center_x,
            channel_y_rel: p.channel_y_rel,
            offset_a: 0.0,
            offset_p: 0.0,
            bevel0: 0.0, bevel1: 0.0,
            jog0: 0.0, jog1: 0.0,
            dive_mode: 0, detour_bias: 0.0,
            final_path: None,
        }
    }
}

#[derive(Clone, Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct PlacedOut {
    pub anchor_cx: f64,
    pub anchor_cy_rel: f64,
    pub w: f64,
    pub h: f64,
    pub track: usize,
    pub pill_center_x: f64,
    pub channel_y_rel: f64,
    pub offset_a: f64,
    pub offset_p: f64,
    pub bevel0: f64,
    pub bevel1: f64,
    pub jog0: f64,
    pub jog1: f64,
    pub dive_mode: i32,
    pub detour_bias: f64,
    /// Serialized as `[[x, y], ...]` — the shape the JS renderer
    /// (drawVertexLabels in pg_export_labels.py) expects.
    pub final_path: Option<Vec<[f64; 2]>>,
}

impl From<PlacedItem> for PlacedOut {
    fn from(p: PlacedItem) -> Self {
        Self {
            anchor_cx: p.anchor_cx,
            anchor_cy_rel: p.anchor_cy_rel,
            w: p.w, h: p.h, track: p.track,
            pill_center_x: p.pill_center_x,
            channel_y_rel: p.channel_y_rel,
            offset_a: p.offset_a,
            offset_p: p.offset_p,
            bevel0: p.bevel0, bevel1: p.bevel1,
            jog0: p.jog0, jog1: p.jog1,
            dive_mode: p.dive_mode,
            detour_bias: p.detour_bias,
            final_path: p.final_path.map(|v| {
                v.into_iter().map(|pt| [pt.x, pt.y]).collect()
            }),
        }
    }
}

#[derive(Clone, Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct OptimizeRequest {
    #[serde(default)] pub cable_id: i64,
    pub placed: Vec<PlacedIn>,
    pub strip_h: f64,
    pub top_pad: f64,
    pub track_offsets: Vec<f64>,
    pub boundaries: Vec<f64>,
    pub wire_segments: Vec<WireSegmentIn>,
    pub wall_edges: Vec<WallEdgeIn>,
    pub strip_area_x0: f64,
    pub strip_area_w: f64,
}

#[derive(Clone, Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct OptimizeResponse {
    pub cable_id: i64,
    pub placed: Vec<PlacedOut>,
}

#[derive(Clone, Debug, Deserialize)]
pub struct BatchRequest { pub cables: Vec<OptimizeRequest> }

#[derive(Clone, Debug, Serialize)]
pub struct BatchResponse { pub cables: Vec<OptimizeResponse> }
