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

PLAYGROUND REGISTRATION
=======================
Like controlnets, inpaint registers a set of layer slots with the
daemon so the docker can render them in the Playgrounds section and
the daemon can filter the corresponding resources out of its own
Resources section.  The three slots:

  · `@thecanvas` — user-editable.  One row in the docker's
    Playgrounds section (source dropdown + layer picker).  The
    selected spec drives inpaint's autosync poll — which document,
    which layer, or the composite — and therefore what ends up in
    the `thecanvas` scratch layer on the next sync.

  · `@thecontext` — `managed_by_playground: True`.  Claimed from the
    Resources section (no manual editing) but does not get a
    Playgrounds row; the playground derives context from its scalar
    context rect / crop frame and never reads the layer.

  · `@theselection` — `managed_by_playground: True`.  Claimed from
    the Resources section but does not get a Playgrounds row; the
    playground writes it via `comfy_set_layer_pixels` on every sync.

`managed_by_playground` slots only contribute to the daemon's
resource-filtering; the docker's `_update_playgrounds` skips them
when building rows.  See `kritomatic_daemon/handlers/panel.py`'s
`ResourcesProvider` and `kritomatic_daemon/panel/docker.py`'s
`_update_playgrounds`.
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

from inpaint import inpaint_html, inpaint_server, playground_registry

from pgkit.server import serve as kit_serve


HTML_FILE    = "inpaint.html"
PORT         = 8774
OPEN_BROWSER = True

PLAYGROUND_ID   = "inpaint"
PLAYGROUND_NAME = "Inpaint"

LAYER_SLOTS = [
    # User-editable via the docker's Playgrounds section.
    {"id": "@thecanvas"},
    # Managed internally by the playground.  Claimed from Resources
    # so the user cannot edit them out of sync with what the
    # playground derives; not shown in the Playgrounds section.
    {"id": "@thecontext", "managed_by_playground": True},
    {"id": "@theselection", "managed_by_playground": True},
]


def descriptor():
    """Return the run_all.py descriptor for this playground."""
    return {
        "name":           PLAYGROUND_ID,
        "port":           PORT,
        "html_file":      HTML_FILE,
        "html_render":    inpaint_html.render,
        "routes":         inpaint_server.ROUTES,
        "raw_routes":     inpaint_server.RAW_ROUTES,
        "module_sources": inpaint_html.module_sources(),
    }


def main():
    html = inpaint_html.render()
    with open(HTML_FILE, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"Wrote {HTML_FILE}")
    print(f"  comfyui  : http://{inpaint_server.COMFY_HOST}:{inpaint_server.COMFY_PORT}")
    print(f"  workflow : dynamic \u2014 uses whatever the daemon has loaded")
    print(f"  routes   : {', '.join(sorted(list(inpaint_server.ROUTES) + list(inpaint_server.RAW_ROUTES)))}")

    # Register with the daemon BEFORE starting to serve, so the panel
    # reflects inpaint's existence on the very first render.  A
    # failure here is non-fatal: inpaint still works, just without a
    # Playgrounds row and without the resource-filtering.
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
        routes         = inpaint_server.ROUTES,
        raw_routes     = inpaint_server.RAW_ROUTES,
        module_sources = inpaint_html.module_sources(),
        hub_name       = "inpaint",
    )


if __name__ == "__main__":
    main()