"""rmbg playground — entry point.

    python rmbg_playground.py

Writes rmbg.html, serves /modules/*, registers with the daemon,
and wires up the canvas-push subscriber — same boot shape as
inpaint's `inpaint_playground.py`.

PLAYGROUND IDENTITY
===================
`PLAYGROUND_ID`, `PLAYGROUND_NAME`, `PLAYGROUND_PORT`, and
`LAYER_SLOTS` live in `rmbg_server.py` so the server can answer
`/version` and `/playground_reregister` without importing this
module (avoids a cycle).  Re-imported here under the same
names for backward-compat with anything reading them off
`rmbg_playground`.

PUSH-CHANNEL WIRING
===================
Creates a `pgkit.remote_hub.Hub`, passes it to both
`kit_serve(hub=...)` (so `/remote` WS uses it) and
`inpaint_server.set_browser_hub` (so the daemon-event subscriber
can broadcast through it), then spawns the subscriber via
`start_canvas_subscriber()`."""

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

from rmbg import rmbg_html, rmbg_server
from pgruntime import daemon_registry as playground_registry

from pgkit.remote_hub import Hub
from pgkit.server import serve as kit_serve


HTML_FILE    = "rmbg.html"
OPEN_BROWSER = True

PORT            = rmbg_server.PLAYGROUND_PORT
PLAYGROUND_ID   = rmbg_server.PLAYGROUND_ID
PLAYGROUND_NAME = rmbg_server.PLAYGROUND_NAME
LAYER_SLOTS     = rmbg_server.LAYER_SLOTS


def descriptor():
    return {
        "name":           PLAYGROUND_ID,
        "port":           PORT,
        "html_file":      HTML_FILE,
        "html_render":    rmbg_html.render,
        "routes":         rmbg_server.ROUTES,
        "raw_routes":     rmbg_server.RAW_ROUTES,
        "module_sources": rmbg_html.module_sources(),
    }


def main():
    html = rmbg_html.render()
    with open(HTML_FILE, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"Wrote {HTML_FILE}")
    print(f"  comfyui  : http://{rmbg_server.COMFY_HOST}:{rmbg_server.COMFY_PORT}")
    print(f"  workflow : dynamic \u2014 uses whatever the daemon has loaded")
    print(f"  routes   : {', '.join(sorted(list(rmbg_server.ROUTES) + list(rmbg_server.RAW_ROUTES)))}")

    state_url = f"http://127.0.0.1:{PORT}"
    registered = playground_registry.register(
        PLAYGROUND_ID, PLAYGROUND_NAME, state_url, PORT,
        layer_slots=LAYER_SLOTS,
        retry_s=30.0,
        log_prefix="rmbg",
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
            f"show its Playgrounds section row.",
            file=sys.stderr, flush=True,
        )

    def _cleanup():
        try:
            playground_registry.unregister(PLAYGROUND_ID, log_prefix="rmbg")
        except Exception:
            pass

    atexit.register(_cleanup)

    shared_hub = Hub()
    rmbg_server.set_browser_hub(shared_hub)
    rmbg_server.start_canvas_subscriber()

    kit_serve(
        port           = PORT,
        open_browser   = OPEN_BROWSER,
        html_file      = HTML_FILE,
        routes         = rmbg_server.ROUTES,
        raw_routes     = rmbg_server.RAW_ROUTES,
        module_sources = rmbg_html.module_sources(),
        hub_name       = "rmbg",
        hub            = shared_hub,
    )


if __name__ == "__main__":
    main()