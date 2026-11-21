"""Multimask playground — entry point.

    python multimask_playground.py

Writes multimask.html, serves it at http://127.0.0.1:8776/.
The playground's state is served at /state for the
`MultiAreaConditioning` ComfyUI node.

CANVAS-PUSH WIRING
==================
`main()` creates a `pgkit.remote_hub.Hub` instance and passes it to
both:

  · `kit_serve(hub=_shared_hub)` — so the `/remote` WebSocket
    endpoint uses this hub instead of creating its own.

  · `multimask_server.set_browser_hub(_shared_hub)` — so the canvas
    subscriber thread (spawned by `start_canvas_subscriber()`) has
    something to broadcast through.

Then `multimask_server.start_canvas_subscriber()` spawns the thread
that connects to the daemon's event stream (port 12347) and forwards
`canvas_update` and `paste_applied` events to every connected
browser over their `/remote` WebSocket.

The net effect: after inpaint pastes into Krita, multimask's canvas
updates within ~20-90 ms (via `paste.applied` composite) and then
swaps to the authoritative `canvas.pushed` composite ~200 ms later,
without the browser polling for either.  See
`multimask_server.py`'s module docstring, PUSH CHANNELS.

Multimask does NOT register itself with the daemon's playground
registry — inpaint is the sole owner of `@thecanvas` and multimask
is a read-only consumer of inpaint's selector.  Consequently the
daemon's registry sweep never touches multimask, and multimask does
not need a `/playground_reregister` route."""

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

from multimask import multimask_html, multimask_server

from pgkit.remote_hub import Hub
from pgkit.server import serve as kit_serve


HTML_FILE    = "multimask.html"
PORT         = 8776
OPEN_BROWSER = True


def descriptor():
    """Return the run_all.py descriptor for this playground."""
    return {
        "name":           "multimask",
        "port":           PORT,
        "html_file":      HTML_FILE,
        "html_render":    multimask_html.render,
        "routes":         multimask_server.ROUTES,
        "raw_routes":     multimask_server.RAW_ROUTES,
        "module_sources": multimask_html.module_sources(),
    }


def main():
    html = multimask_html.render()
    with open(HTML_FILE, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"Wrote {HTML_FILE}")
    print(f"  routes   : {', '.join(sorted(list(multimask_server.ROUTES) + list(multimask_server.RAW_ROUTES)))}")
    print(f"  state    : http://127.0.0.1:{PORT}/state")

    # Canvas-push wiring.  Create the hub, register it with the
    # multimask server so the subscriber thread can broadcast
    # through it, spawn the subscriber, then pass the same hub to
    # `kit_serve()` so the `/remote` WebSocket endpoint uses it.
    # See the module docstring, CANVAS-PUSH WIRING.
    shared_hub = Hub()
    multimask_server.set_browser_hub(shared_hub)
    multimask_server.start_canvas_subscriber()

    kit_serve(
        port           = PORT,
        open_browser   = OPEN_BROWSER,
        html_file      = HTML_FILE,
        routes         = multimask_server.ROUTES,
        raw_routes     = multimask_server.RAW_ROUTES,
        module_sources = multimask_html.module_sources(),
        hub_name       = "multimask",
        hub            = shared_hub,
    )


if __name__ == "__main__":
    main()