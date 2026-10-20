use crate::constants::*;
use crate::types::*;

/// Faithful port of `refineLeaderOffsets` (pg_export_primitives.py).
///
/// Resets `offset_a` and `offset_p` on every item before the fan-out
/// pass.  `offset_p` is not moved here — it is moved by
/// `separate_cross_track_descents` in pillpush.rs — but it must be
/// zeroed here so a stale value from a previous run cannot survive
/// into a fresh export.
pub fn refine_leader_offsets(
    placed: &mut [PlacedItem], strip_h: f64, _top_pad: f64, _track_offsets: &[f64],
) {
    let min_sep = REFINE_MIN_SEP;
    let fan_step = REFINE_FAN_STEP;
    let max_offset = REFINE_MAX_OFFSET;

    for it in placed.iter_mut() {
        it.offset_a = 0.0;
        it.offset_p = 0.0;
    }
    if placed.len() < 2 { return; }

    let n = placed.len();
    let anchor_y_rel: Vec<f64> = placed.iter().map(|it| it.anchor_cy_rel - strip_h).collect();
    let chan_y_rel:   Vec<f64> = placed.iter().map(|it| it.channel_y_rel).collect();

    let y_overlap = |a0: f64, a1: f64, b0: f64, b1: f64| -> bool {
        a0.max(b0) < a1.min(b1) - 0.5
    };

    let anchor_x_at = |i: usize, y: f64, placed: &[PlacedItem]| -> f64 {
        let denom = chan_y_rel[i] - anchor_y_rel[i];
        let t = if denom > 1e-6 { (y - anchor_y_rel[i]) / denom } else { 0.0 };
        placed[i].anchor_cx + placed[i].offset_a * t
    };

    for _ in 0..REFINE_MAX_PASSES {
        let mut any_change = false;
        for i in 0..n {
            for j in (i + 1)..n {
                let has = y_overlap(anchor_y_rel[i], chan_y_rel[i],
                                    anchor_y_rel[j], chan_y_rel[j]);
                if !has { continue; }
                let y0 = anchor_y_rel[i].max(anchor_y_rel[j]);
                let y1 = chan_y_rel[i].min(chan_y_rel[j]);
                let mut conflict = false;
                for s in 0..=5 {
                    let y = y0 + (y1 - y0) * (s as f64) / 5.0;
                    if (anchor_x_at(i, y, placed) - anchor_x_at(j, y, placed)).abs() < min_sep {
                        conflict = true;
                        break;
                    }
                }
                if !conflict { continue; }

                let dir = if placed[j].anchor_cx >= placed[i].anchor_cx { 1.0 } else { -1.0 };
                let na = (placed[i].offset_a - dir * fan_step * 0.5)
                    .clamp(-max_offset, max_offset);
                let nb = (placed[j].offset_a + dir * fan_step * 0.5)
                    .clamp(-max_offset, max_offset);
                if (na - placed[i].offset_a).abs() > 1e-6 {
                    placed[i].offset_a = na; any_change = true;
                }
                if (nb - placed[j].offset_a).abs() > 1e-6 {
                    placed[j].offset_a = nb; any_change = true;
                }
            }
        }
        if !any_change { break; }
    }

    for i in 0..n {
        let h = chan_y_rel[i] - anchor_y_rel[i];
        let cap = max_offset.min((h * 0.5).max(0.0));
        placed[i].offset_a = placed[i].offset_a.clamp(-cap, cap);
    }
}
