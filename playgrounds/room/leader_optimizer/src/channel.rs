use crate::types::*;

/// Faithful port of `_buildChannelToPill` (pg_export_optimizer.py).
///
/// The descent column is `descent_x`, which may differ from the
/// pill's centre `pill_x` by an offset the caller has chosen to
/// separate this leader's vertical from a foreign leader's.  The
/// blockers check, the probe direction, and the detour columns all
/// key off `descent_x`; the final `[pill_x, pill_top_y]` point is
/// only emitted when the descent column and the pill's centre
/// actually differ, so a leader with `offset_p = 0` produces exactly
/// the path it did before the offset was introduced.
pub fn build_channel_to_pill(
    descent_x: f64, pill_x: f64, chan_y: f64, pill_top_y: f64,
    self_idx: usize,
    placed: &[PlacedItem],
    strip_bottom: f64, top_pad: f64, track_offsets: &[f64],
) -> Vec<Point> {
    let m = 4.0;
    let eps = 2.0;
    let bias = placed[self_idx].detour_bias;

    let mut boxes: Vec<Aabb> = Vec::new();
    for (i, q) in placed.iter().enumerate() {
        if i == self_idx { continue; }
        let qt = strip_bottom + top_pad + track_offsets[q.track];
        let qb = qt + q.h;
        if qb <= chan_y { continue; }
        if qt >= pill_top_y { continue; }
        boxes.push(Aabb {
            ql: q.pill_center_x - q.w / 2.0,
            qr: q.pill_center_x + q.w / 2.0,
            qt, qb,
        });
    }

    let mut blockers: Vec<Aabb> = Vec::new();
    for b in &boxes {
        if descent_x >= b.ql - eps && descent_x <= b.qr + eps {
            blockers.push(*b);
        }
    }

    // No blockers on the descent column: straight down from the
    // channel to the pill's top, plus a short horizontal to the
    // pill's actual centre if the descent column is offset.
    if blockers.is_empty() {
        let mut out = vec![Point { x: descent_x, y: pill_top_y }];
        if (descent_x - pill_x).abs() > 0.5 {
            out.push(Point { x: pill_x, y: pill_top_y });
        }
        return out;
    }

    blockers.sort_by(|a, b| a.qt.partial_cmp(&b.qt).unwrap());
    let mut bands: Vec<Aabb> = Vec::new();
    let mut cur = blockers[0];
    for b in &blockers[1..] {
        if b.qt <= cur.qb + 2.0 * m {
            cur.qb = cur.qb.max(b.qb);
            cur.ql = cur.ql.min(b.ql);
            cur.qr = cur.qr.max(b.qr);
        } else {
            bands.push(cur);
            cur = *b;
        }
    }
    bands.push(cur);

    let h_clear = |y: f64, xa: f64, xb: f64| -> bool {
        let lo = xa.min(xb);
        let hi = xa.max(xb);
        for b in &boxes {
            if b.qt - eps > y || b.qb + eps < y { continue; }
            if b.qr < lo || b.ql > hi { continue; }
            return false;
        }
        true
    };
    let v_clear = |x: f64, ya: f64, yb: f64| -> bool {
        let lo = ya.min(yb);
        let hi = ya.max(yb);
        for b in &boxes {
            if b.ql - eps > x || b.qr + eps < x { continue; }
            if b.qb <= lo || b.qt >= hi { continue; }
            return false;
        }
        true
    };

    let mut out = Vec::new();
    let mut cur_y = chan_y;

    for band in &bands {
        let entry_y = cur_y.max(band.qt - m);
        let exit_y = pill_top_y.min(band.qb + m);
        if entry_y >= exit_y - 0.5 { continue; }

        if entry_y > cur_y + 0.5 {
            out.push(Point { x: descent_x, y: entry_y });
        }

        let left_x  = band.ql - m;
        let right_x = band.qr + m;
        let prefer_right = (right_x - descent_x) < (descent_x - left_x);
        let order = if prefer_right {
            [right_x, left_x]
        } else {
            [left_x, right_x]
        };

        let mut detour_x: Option<f64> = None;
        for &base in &order {
            let dir = if base > descent_x { 1.0 } else { -1.0 };
            let mut probe = base;
            for _ in 0..60 {
                if v_clear(probe, entry_y, exit_y)
                    && h_clear(entry_y, descent_x, probe)
                    && h_clear(exit_y,  descent_x, probe)
                {
                    detour_x = Some(probe);
                    break;
                }
                probe += dir * 6.0;
            }
            if detour_x.is_some() { break; }
        }
        let mut dxv = detour_x.unwrap_or(if prefer_right { right_x } else { left_x });

        if bias != 0.0 {
            let biased = dxv + bias;
            if v_clear(biased, entry_y, exit_y)
                && h_clear(entry_y, descent_x, biased)
                && h_clear(exit_y,  descent_x, biased)
            {
                dxv = biased;
            }
        }

        out.push(Point { x: dxv,       y: entry_y });
        out.push(Point { x: dxv,       y: exit_y  });
        out.push(Point { x: descent_x, y: exit_y  });
        cur_y = exit_y;
    }

    if pill_top_y > cur_y + 0.5 {
        out.push(Point { x: descent_x, y: pill_top_y });
    }
    if (descent_x - pill_x).abs() > 0.5 {
        out.push(Point { x: pill_x, y: pill_top_y });
    }
    out
}
