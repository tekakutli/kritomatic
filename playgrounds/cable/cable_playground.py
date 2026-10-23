"""
cable_playground.py — plan cable / pipe routes in a room.

    1. run room.py                → produces room.step (+ room_walls.json)
    2. run cable_playground.py    → writes cable_playground.html and serves it.
    3. open the URL that this script prints.

room.py and room_walls.json live at playgrounds/shared/, next to the
shared playground_fonts.  `_SHARED` puts that directory on sys.path
for `playground_fonts`, and cable_geometry resolves the JSON path
relative to its own location.

This module is the entry point.  The heavy lifting lives in:

    cable_geometry     — room_walls.json → a geometry dict
    cable_html         — the HTML/JS page and module manifest
    pgkit.server       — the HTTP + /modules/ server, plus /remote

The old `cable_server.py` is deleted.  `kit_serve` (from
pgkit.server) handles the HTTP layer, the module serving at
/modules/, and the /remote WebSocket for the remote panel.  Two
routes that used to live in cable_server.py are preserved by handing
them to `kit_serve`'s `routes` and `raw_routes`:

    POST /save               → persists room_layout.json (JSON body)
    POST /diag               → prints the arrow diagnostic (text body)
    POST /optimize-leaders   → spawns the Rust leader-optimizer binary
                               (raw body, JSON response)

`/save` and `/diag` are straightforward callables.  `/optimize-leaders`
is a subprocess wrapper, ported from cable_server.py's handler with
identical semantics: reads the request body, pipes it through the
Rust binary, writes its stdout to the response; 500 with a JSON error
body if the binary is missing, 504 on timeout.  The JS side
(pg_export_rust.js) already handles a failed endpoint gracefully by
falling back to its in-page JS leader optimiser, so a missing binary
is not a hard failure — it is just slower.

Data model (used by the browser-side JS)
----------------------------------------
Anchor: a point in the room.  Two spaces:
    floor     : { x, y }  (+ optional gridId/gridAxis)
    wall-edge : { segIdx, t, v }

Cable: an ordered list of anchor ids.  Sharing an anchor between
cables is what "connecting" means.

Save format v3 — unchanged from the previous revision.

The `/save` and `/diag` routes are still wired to preserve the same
endpoints the browser has always used; a fork that removes the
arrow-diagnostic feature can drop the `/diag` route.
"""

import json
import os
import shutil
import subprocess
import sys

_HERE   = os.path.dirname(os.path.abspath(__file__))
_PARENT = os.path.dirname(_HERE)
_SHARED = os.path.join(_PARENT, "shared")
for _p in (_HERE, _PARENT, _SHARED):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import cable_geometry
import cable_html

from pgkit.server import serve as kit_serve


HTML_FILE    = "cable_playground.html"
STATE_FILE   = "room_layout.json"
PORT         = 8765
SERVE        = True
OPEN_BROWSER = True

# Rust leader-optimizer binary.  Same resolution order as the old
# cable_server.py: env var wins, then PATH, then a convention path
# next to this file.  If none resolves, /optimize-leaders returns 500
# and the JS falls back to its own (slower) optimiser.
RUST_BIN = (
    os.environ.get("LEADER_OPTIMIZER_BIN")
    or shutil.which("leader_optimizer")
    or os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "leader_optimizer", "target", "release", "leader_optimizer",
    )
)
RUST_TIMEOUT_SEC = 60


def _save_route(payload):
    """Persist room_layout.json from the browser's /save POST."""
    try:
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)
        n = (len(payload.get("floorGrids", [])) +
             len(payload.get("wallGrids", [])) +
             len(payload.get("floorCables", [])) +
             len(payload.get("wallCables", [])))
        print(f"  saved {n} item(s) to {STATE_FILE}", flush=True)
        return {"success": True}
    except Exception as e:
        print(f"  save FAILED: {e}", flush=True)
        return {"success": False, "message": str(e)}


def _diag_route(payload):
    """Print the arrow diagnostic to the terminal.  The browser sends
    it as a plain-text POST; pgkit.server hands us a dict, so the
    bridge is to send the text back through one of the payload values.
    See `_handle_diag` below for the wire-format note."""
    text = payload.get("text", "") if isinstance(payload, dict) else str(payload)
    rule = "=" * 72
    print("", flush=True)
    print(rule, flush=True)
    print(" ARROW DIAGNOSTIC", flush=True)
    print(rule, flush=True)
    print(text, flush=True)
    print(rule, flush=True)
    print("", flush=True)
    return {"success": True}


def _optimize_route(payload):
    """Spawn the Rust leader optimiser.  See the module docstring for
    the wire format and error handling.  Because pgkit.server's `routes`
    dict receives a parsed JSON body, we re-serialize it before piping
    to the Rust binary, which expects JSON on stdin."""
    if not os.path.exists(RUST_BIN):
        return {
            "success": False,
            "message": "leader_optimizer binary not found",
            "path":    RUST_BIN,
            "hint":    "cargo build --release in leader_optimizer/, "
                       "or set LEADER_OPTIMIZER_BIN",
        }
    try:
        body = json.dumps(payload).encode("utf-8")
        proc = subprocess.run(
            [RUST_BIN],
            input=body,
            capture_output=True,
            timeout=RUST_TIMEOUT_SEC,
        )
        if proc.returncode != 0:
            err = proc.stderr.decode("utf-8", errors="replace")[:1000]
            return {
                "success": False,
                "message": f"leader_optimizer exited {proc.returncode}: "
                           f"{err or '(no stderr)'}",
            }
        return json.loads(proc.stdout.decode("utf-8"))
    except subprocess.TimeoutExpired:
        return {
            "success": False,
            "message": "leader optimizer timed out",
            "timeout_sec": RUST_TIMEOUT_SEC,
        }
    except Exception as e:
        return {"success": False, "message": str(e)}


def main():
    geom = cable_geometry.extract_geometry()

    html = cable_html.render(geom)
    with open(HTML_FILE, "w", encoding="utf-8") as f:
        f.write(html)

    b  = geom["bounds"]
    uw = geom["unfoldedWall"]
    n_step = sum(1 for s in uw["segments"] if s.get("kind") == "step")
    n_col  = sum(1 for s in uw["segments"] if s.get("kind") == "column")

    print(f"Wrote {HTML_FILE}")
    print(f"  wall faces extracted  : {len(geom['faces'])}")
    print(f"  step areas extracted  : {len(geom['stepFaces'])}")
    print(f"  bounds (mm)           : "
          f"x [{b['minX']:.1f}, {b['maxX']:.1f}]  "
          f"y [{b['minY']:.1f}, {b['maxY']:.1f}]")
    print(f"  wall cut              : z = {geom['cutZ']:.0f} mm")
    print(f"  unfolded wall         : {len(uw['segments'])} segment(s) "
          f"({n_col} column, {n_step} step), "
          f"perimeter {uw['totalU']:.0f} mm, height {uw['height']:.0f} mm, "
          f"{'CCW' if uw['ccw'] else 'CW'}")
    print(f"  columns               : {len(geom.get('columns', []))}")
    print(f"  openings              : {len(geom.get('openings', []))}")
    print(f"  state file            : {STATE_FILE}")
    print(f"  leader optimizer      : "
          f"{'found at ' + RUST_BIN if os.path.exists(RUST_BIN) else 'NOT FOUND (JS fallback)'}")

    if SERVE:
        print()
        kit_serve(
            port           = PORT,
            open_browser   = OPEN_BROWSER,
            html_file      = HTML_FILE,
            routes         = {
                "/save":              _save_route,
                "/diag":              _diag_route,
                "/optimize-leaders":  _optimize_route,
            },
            module_sources = cable_html.module_sources(geom),
        )
    else:
        print(f"  open                  : file://{os.path.abspath(HTML_FILE)}")


if __name__ == "__main__":
    main()
