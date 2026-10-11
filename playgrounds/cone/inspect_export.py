#!/usr/bin/env python3
"""
inspect_export.py — dump every layer an export created, via the
Kritomatic daemon.

Runs one introspect call per node in the document tree, then prints
a compact report: for every vector layer, every shape's SVG, bounds,
and — if the daemon reports them — the shape's own QTransform and
absolute transformation.  For groups, prints the transform-mask
matrix and its pivots.  For texts under a mask, computes the
projected screen position so you can see where each text actually
lands versus where its polygon sits.

Usage:

    python inspect_export.py                 # everything
    python inspect_export.py --pattern "S5"  # only layers named *S5*
    python inspect_export.py --json          # raw data
"""

import argparse
import json
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path


def _find_repo_src() -> Path:
    p = Path(__file__).resolve().parent
    for _ in range(12):
        cand = p / "src"
        if (cand / "kritomatic").is_dir():
            return cand
        if p == p.parent:
            break
        p = p.parent
    raise RuntimeError(
        "Could not locate src/kritomatic from " + str(Path(__file__).resolve())
    )


_SRC = _find_repo_src()
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from kritomatic.client import KritaClient  # noqa: E402


def _call(client, cmd_type, **kwargs):
    resp = client.execute(cmd_type, **kwargs)
    if not resp or resp.get('status') != 'success':
        msg = resp.get('message') if resp else 'no response'
        print(f"  ! {cmd_type} failed: {msg}", file=sys.stderr)
        return None
    return resp.get('data', {})


def _list_layers(client):
    data = _call(client, 'list_layers')
    return data.get('layers', []) if data else []


def _describe(client, name):
    return _call(client, 'describe_vector_layer', layer_name=name)


def _parse_svg_attrs(svg):
    out = {'x': None, 'y': None, 'font_size': None, 'rotation_deg': None,
           'points': None}
    for key, pattern in (
        ('x',            r'\bx="([-0-9.eE]+)"'),
        ('y',            r'\by="([-0-9.eE]+)"'),
        ('font_size',    r'font-size:\s*([-0-9.eE]+)'),
        ('rotation_deg', r'rotate\(\s*([-0-9.eE]+)'),
    ):
        m = re.search(pattern, svg)
        if m:
            try:
                out[key] = float(m.group(1))
            except ValueError:
                pass
    m = re.search(r'points="([^"]+)"', svg)
    if m:
        pts = []
        for pair in m.group(1).split():
            try:
                xs, ys = pair.split(',')
                pts.append([float(xs), float(ys)])
            except ValueError:
                pass
        out['points'] = pts
    return out


def _parse_mask(xml_text):
    """Return {matrix, originalCenter, transformedCenter} from a mask
    XML string, or None.  Matrix keys are m11..m33 in Qt convention."""
    clean = re.sub(r'^\s*<!DOCTYPE[^>]*>\s*', '', xml_text, count=1)
    try:
        root = ET.fromstring(clean)
    except ET.ParseError:
        return None

    def _find(tag):
        for elem in root.iter(tag):
            return elem
        return None

    persp = _find('flattenedPerspectiveTransform')
    if persp is None:
        return None

    def _num(v):
        try:
            return float(v)
        except (TypeError, ValueError):
            return 0.0

    matrix = {k: _num(persp.attrib.get(k, '0'))
              for k in ('m11','m12','m13','m21','m22','m23','m31','m32','m33')}

    def _center(tag):
        e = _find(tag)
        if e is None:
            return (0.0, 0.0)
        return (_num(e.attrib.get('x', '0')), _num(e.attrib.get('y', '0')))

    return {
        'matrix':            matrix,
        'originalCenter':    _center('originalCenter'),
        'transformedCenter': _center('transformedCenter'),
    }


def _apply_mask(mask, x, y):
    """Replicate Krita's transform-mask math: a point is shifted to
    the original center, transformed by the matrix in Qt's row-vector
    convention, then shifted to the transformed center.

    Qt row-vector form:
        x' = (m11*x + m21*y + m31) / (m13*x + m23*y + m33)
        y' = (m12*x + m22*y + m32) / (m13*x + m23*y + m33)
    """
    if not mask:
        return None
    m = mask['matrix']
    ox, oy = mask['originalCenter']
    tx, ty = mask['transformedCenter']

    px = x - ox
    py = y - oy

    w = m['m13'] * px + m['m23'] * py + m['m33']
    if abs(w) < 1e-15:
        return None

    sx = (m['m11'] * px + m['m21'] * py + m['m31']) / w
    sy = (m['m12'] * px + m['m22'] * py + m['m32']) / w
    return (tx + sx, ty + sy)


def _render_report(client, layers, as_json=False):
    report = {'patches': [], 'layers': []}

    vector_layers = [l for l in layers if l['type'] == 'vectorlayer']
    seen_masks = set()

    for vl in vector_layers:
        desc = _describe(client, vl['name'])
        if not desc:
            continue

        # Nearest ancestor group with a mask.
        parent_group = None
        parent_mask = None
        for anc in desc.get('ancestors', []):
            if anc['type'] == 'grouplayer' and anc.get('mask'):
                parent_group = anc['name']
                parsed = _parse_mask(anc['mask']['xml'])
                parent_mask = parsed
                if parent_group not in seen_masks:
                    seen_masks.add(parent_group)
                    report['patches'].append({
                        'name': parent_group,
                        'mask_name': anc['mask']['name'],
                        'matrix': parsed['matrix'] if parsed else None,
                        'originalCenter': parsed['originalCenter'] if parsed else None,
                        'transformedCenter': parsed['transformedCenter'] if parsed else None,
                    })
                break

        layer_entry = {
            'name': vl['name'],
            'bounds': desc['layer']['bounds'],
            'parent_group': parent_group,
            'shapes': [],
        }

        for sh in desc.get('shapes', []):
            attrs = _parse_svg_attrs(sh['svg'])
            entry = {
                'svg': sh['svg'],
                'bounds': sh.get('bounds'),
                'parsed': attrs,
                'transforms': sh.get('transforms'),
            }
            if '<text' in sh['svg'] and parent_mask and \
               attrs['x'] is not None and attrs['y'] is not None:
                proj = _apply_mask(parent_mask, attrs['x'], attrs['y'])
                entry['projected_screen'] = proj
            if '<polygon' in sh['svg'] and attrs['points'] and parent_mask:
                screen_pts = []
                for [px, py] in attrs['points']:
                    p = _apply_mask(parent_mask, px, py)
                    if p:
                        screen_pts.append(p)
                if screen_pts:
                    sx = sum(p[0] for p in screen_pts) / len(screen_pts)
                    sy = sum(p[1] for p in screen_pts) / len(screen_pts)
                    entry['projected_screen_centroid'] = (sx, sy)
                    entry['projected_screen_corners'] = screen_pts
            layer_entry['shapes'].append(entry)
        report['layers'].append(layer_entry)

    if as_json:
        print(json.dumps(report, indent=2))
        return

    print(f"{len(report['patches'])} masked group(s), "
          f"{len(report['layers'])} vector layer(s).")
    print()

    print("=" * 72)
    print("PATCH MASKS")
    print("=" * 72)
    for p in report['patches']:
        print(f"  {p['name']}  (mask: {p['mask_name']})")
        if p['matrix']:
            m = p['matrix']
            print(f"    matrix:  [{m['m11']:>10.4f} {m['m12']:>10.4f} {m['m13']:>10.4f}]")
            print(f"             [{m['m21']:>10.4f} {m['m22']:>10.4f} {m['m23']:>10.4f}]")
            print(f"             [{m['m31']:>10.4f} {m['m32']:>10.4f} {m['m33']:>10.4f}]")
        print(f"    originalCenter    : {p['originalCenter']}")
        print(f"    transformedCenter : {p['transformedCenter']}")
        print()

    print("=" * 72)
    print("VECTOR LAYERS")
    print("=" * 72)
    for layer in report['layers']:
        print(f"  {layer['name']}  (parent mask: {layer['parent_group']})")
        b = layer['bounds']
        print(f"    layer bounds: ({b['x']}, {b['y']}) "
              f"{b['width']}×{b['height']}")
        for i, sh in enumerate(layer['shapes']):
            kind = ('text' if '<text' in sh['svg'] else
                    'polygon' if '<polygon' in sh['svg'] else 'shape')
            print(f"    shape {i + 1}: {kind}")
            if sh.get('bounds'):
                sb = sh['bounds']
                print(f"      flat bounds: ({sb['x']:.2f}, {sb['y']:.2f}) "
                      f"{sb['width']:.2f}×{sb['height']:.2f}")
            p = sh['parsed']
            if p['x'] is not None:
                print(f"      flat anchor: ({p['x']:.3f}, {p['y']:.3f})")
            if p['font_size'] is not None:
                print(f"      flat font-size: {p['font_size']:.2f}")
            if p['rotation_deg'] is not None:
                print(f"      flat rotation (from svg): {p['rotation_deg']:.2f}°")
            if p['points'] is not None:
                import math
                pts = p['points']
                n = len(pts)
                best_len = 0
                best_dx = best_dy = 0
                for k in range(n):
                    ax, ay = pts[k]
                    bx, by = pts[(k + 1) % n]
                    dx, dy = bx - ax, by - ay
                    L = (dx * dx + dy * dy) ** 0.5
                    if L > best_len:
                        best_len = L
                        best_dx, best_dy = dx / L, dy / L
                raw_ang = math.degrees(math.atan2(best_dy, best_dx))
                if best_dx < 0:
                    flip_ang = math.degrees(math.atan2(-best_dy, -best_dx))
                else:
                    flip_ang = raw_ang
                print(f"      points: {pts}")
                print(f"      longest edge: len={best_len:.1f}, raw={raw_ang:.1f}°, flipped={flip_ang:.1f}°")

            # Shape-level transform, if the daemon reported it.
            # toSvg() does not include this; it is the transform Krita
            # actually applies to the shape.
            if sh.get('transforms'):
                for name, t in sh['transforms'].items():
                    print(f"      {name}:")
                    print(f"        [{t['m11']:>10.4f} {t['m12']:>10.4f} {t['m13']:>10.4f}]")
                    print(f"        [{t['m21']:>10.4f} {t['m22']:>10.4f} {t['m23']:>10.4f}]")
                    print(f"        [{t['m31']:>10.4f} {t['m32']:>10.4f} {t['m33']:>10.4f}]")

            if 'projected_screen' in sh:
                px, py = sh['projected_screen']
                print(f"      → screen: ({px:.2f}, {py:.2f})")
            if 'projected_screen_centroid' in sh:
                sx, sy = sh['projected_screen_centroid']
                print(f"      → screen centroid: ({sx:.2f}, {sy:.2f})")
                for k, (cx, cy) in enumerate(sh['projected_screen_corners']):
                    print(f"          corner {k}: ({cx:.2f}, {cy:.2f})")
        print()


def main():
    p = argparse.ArgumentParser(
        prog="inspect_export.py",
        description="Dump every layer an export created, using the "
                    "Kritomatic daemon.",
    )
    p.add_argument("--pattern", default=None,
                   help="Only layers whose name contains this string.")
    p.add_argument("--json", action="store_true",
                   help="Emit raw JSON instead of the human report.")
    args = p.parse_args()

    client = KritaClient()
    if not client.connect():
        print("Error: could not connect to the daemon. Make sure Krita "
              "is running and the plugin is enabled.")
        sys.exit(1)

    try:
        layers = _list_layers(client)
        if args.pattern:
            layers = [l for l in layers if args.pattern in l['name']]
        if not layers:
            print("No layers found.")
            return
        _render_report(client, layers, as_json=args.json)
    finally:
        client.close()


if __name__ == "__main__":
    main()
