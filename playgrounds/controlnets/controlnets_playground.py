"""Controlnets playground — entry point.

    python controlnets_playground.py

Writes controlnets.html, serves it at http://127.0.0.1:<port>/.

The playground is a state host: it registers with the daemon with an
`@thecontrolnet` layer slot, pulls the current image from that slot
via the daemon's `/krita/resource_image` route (through autosync),
and exposes `{image_hash, image_dims, controlnets, apply_enabled}` at
`/state` for the `ControlNetsConditioning` ComfyUI node to consume at
execution time.

PORT RESOLUTION
===============
`PORT` is not hardcoded here.  The whole playground reads its port
from `pgruntime.ports.port_for("controlnets")`, wired through
`controlnets_server.PLAYGROUND_PORT`.  Every playground's
`_playground.py` does the same: the single source of truth for the
port map is `pgruntime/ports.py`, validated for uniqueness and
reserved-port collisions at import time.

Fixed port, no silent fallback
==============================
Downstream consumers derive the URL from
`pgruntime.ports.url_for("controlnets", "/state")`.  If this
playground silently bound to a different port, they'd see a stale /
empty state.  Bind first, verify, refuse to start otherwise — the
`port_verify=True` flag on `serve_playground` does this.
"""

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

from controlnets import controlnets_html, controlnets_server

from pgruntime.bootstrap import serve_playground


HTML_FILE    = "controlnets.html"
PORT         = controlnets_server.PLAYGROUND_PORT
OPEN_BROWSER = True

PLAYGROUND_ID   = controlnets_server.PLAYGROUND_ID
PLAYGROUND_NAME = controlnets_server.PLAYGROUND_NAME
LAYER_SLOTS     = controlnets_server.LAYER_SLOTS


def descriptor():
    return {
        "name":           PLAYGROUND_ID,
        "port":           PORT,
        "html_file":      HTML_FILE,
        "html_render":    controlnets_html.render,
        "routes":         controlnets_server.ROUTES,
        "raw_routes":     controlnets_server.RAW_ROUTES,
        "module_sources": controlnets_html.module_sources(),
    }


def main():
    serve_playground(
        playground_id    = PLAYGROUND_ID,
        playground_name  = PLAYGROUND_NAME,
        port             = PORT,
        html_render      = controlnets_html.render,
        module_sources   = controlnets_html.module_sources(),
        routes           = controlnets_server.ROUTES,
        raw_routes       = controlnets_server.RAW_ROUTES,
        layer_slots      = LAYER_SLOTS,
        port_verify      = True,
        watch_daemon     = True,
        open_browser     = OPEN_BROWSER,
        html_file        = HTML_FILE,
        port_error_extra = (
            f"       Downstream consumers derive the URL from "
            f"pgruntime.ports.url_for(\"controlnets\", \"/state\")."
        ),
    )


if __name__ == "__main__":
    main()