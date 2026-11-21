"""Segment playground — entry point.

    python segment_playground.py

Registers with the kritomatic daemon on boot under the id `"segment"`
with a single user-editable `@thesegment` layer slot, so the docker's
Playgrounds section can pick which Krita layer (or the composite)
drives the playground canvas.
"""

import atexit
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_PLAYGROUNDS = os.path.dirname(_HERE)

try:
    sys.path.remove(_HERE)
except ValueError:
    pass
if _PLAYGROUNDS not in sys.path:
    sys.path.insert(0, _PLAYGROUNDS)

from segment import segment_html, segment_server, playground_registry

from pgkit.server import serve as kit_serve


HTML_FILE    = "segment.html"
OPEN_BROWSER = True

PORT            = segment_server.PLAYGROUND_PORT
PLAYGROUND_ID   = segment_server.PLAYGROUND_ID
PLAYGROUND_NAME = segment_server.PLAYGROUND_NAME
LAYER_SLOTS     = segment_server.LAYER_SLOTS


def descriptor():
    return {
        "name":           PLAYGROUND_ID,
        "port":           PORT,
        "html_file":      HTML_FILE,
        "html_render":    segment_html.render,
        "routes":         segment_server.ROUTES,
        "raw_routes":     segment_server.RAW_ROUTES,
        "module_sources": segment_html.module_sources(),
    }


def main():
    html = segment_html.render()
    with open(HTML_FILE, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"Wrote {HTML_FILE}")
    print(f"  comfyui  : http://{segment_server.COMFY_HOST}:{segment_server.COMFY_PORT}")
    print(f"  segment  : {segment_server.WORKFLOW_SEGMENT_PATH}")
    print(f"  refine   : {segment_server.WORKFLOW_REFINE_PATH}")
    print(f"  routes   : {', '.join(sorted(list(segment_server.ROUTES) + list(segment_server.RAW_ROUTES)))}")

    state_url = f"http://127.0.0.1:{PORT}"
    registered = playground_registry.register(
        PLAYGROUND_ID, PLAYGROUND_NAME, state_url, PORT,
        layer_slots=LAYER_SLOTS,
        retry_s=30.0,
    )
    if registered:
        print(
            f"  registered with daemon as {PLAYGROUND_ID!r} "
            f"(slot(s): {[s['id'] for s in LAYER_SLOTS]})",
            flush=True,
        )
    else:
        print(
            f"  WARNING: could not register with daemon; "
            f"playground works independently but the docker will not "
            f"show its Playgrounds section row.  Restart after daemon "
            f"is up to register.",
            file=sys.stderr, flush=True,
        )

    def _cleanup():
        try:
            playground_registry.unregister(PLAYGROUND_ID)
        except Exception:
            pass

    atexit.register(_cleanup)

    kit_serve(
        port           = PORT,
        open_browser   = OPEN_BROWSER,
        html_file      = HTML_FILE,
        routes         = segment_server.ROUTES,
        raw_routes     = segment_server.RAW_ROUTES,
        module_sources = segment_html.module_sources(),
        hub_name       = "segment",
    )


if __name__ == "__main__":
    main()