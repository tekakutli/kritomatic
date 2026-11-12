"""Inpaint playground — entry point.

    python inpaint_playground.py

Writes inpaint.html, serves it at http://127.0.0.1:8774/, and serves
/modules/* from the module manifest.

The inpaint workflow is whatever the daemon currently has loaded — the
one the docker's Workflow picker shows, set via `comfy_load`.  The
playground does NOT hardcode a workflow name; see `inpaint_server.py`'s
module docstring for the resolution rules.  Krita must be running with
the Kritomatic daemon enabled, with a workflow already loaded, and the
daemon must be linked to a running ComfyUI (the playground sends a
`comfy_connect` as part of every inpaint batch, so an already-connected
daemon is fine but a disconnected one gets auto-connected on first run).
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

from inpaint import inpaint_html, inpaint_server

from pgkit.server import serve as kit_serve


HTML_FILE    = "inpaint.html"
PORT         = 8774
OPEN_BROWSER = True


def main():
    html = inpaint_html.render()
    with open(HTML_FILE, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"Wrote {HTML_FILE}")
    print(f"  comfyui  : http://{inpaint_server.COMFY_HOST}:{inpaint_server.COMFY_PORT}")
    print(f"  workflow : dynamic \u2014 uses whatever the daemon has loaded")
    print(f"  routes   : {', '.join(sorted(list(inpaint_server.ROUTES) + list(inpaint_server.RAW_ROUTES)))}")

    kit_serve(
        port           = PORT,
        open_browser   = OPEN_BROWSER,
        html_file      = HTML_FILE,
        routes         = inpaint_server.ROUTES,
        raw_routes     = inpaint_server.RAW_ROUTES,
        module_sources = inpaint_html.module_sources(),
        hub_name       = "inpaint",
    )

if __name__ == "__main__":
    main()
