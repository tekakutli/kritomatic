"""Inpaint playground — entry point.

    python inpaint_playground.py

Writes inpaint.html, and serves
/modules/* from the module manifest.

The inpaint workflow is whatever the daemon currently has loaded — the
one the docker's Workflow picker shows, set via `comfy_load`.  The
playground does NOT hardcode a workflow name; see `inpaint_server.py`'s
module docstring for the resolution rules.  Krita must be running with
the Kritomatic daemon enabled, with a workflow already loaded, and the
daemon must be linked to a running ComfyUI (the playground sends a
`comfy_connect` as part of every inpaint batch, so an already-connected
daemon is fine but a disconnected one gets auto-connected on first run).

PLAYGROUND IDENTITY
===================
`PLAYGROUND_ID`, `PLAYGROUND_NAME`, `PLAYGROUND_PORT`, and
`LAYER_SLOTS` live in `inpaint_server.py`, not here.  They are needed
by both this module (registration on boot, teardown on exit) and by
the server's own routes (`/version`, `/playground_reregister`).
Putting them in `inpaint_server.py` gives the server a single source
of truth without creating an import cycle back to this module.

This module re-imports them under the same local names for
backward compatibility with anything that reads them off
`inpaint_playground`:

    PORT            = inpaint_server.PLAYGROUND_PORT
    PLAYGROUND_ID   = inpaint_server.PLAYGROUND_ID
    PLAYGROUND_NAME = inpaint_server.PLAYGROUND_NAME
    LAYER_SLOTS     = inpaint_server.LAYER_SLOTS

`descriptor()`, `main()`, and `_cleanup()` all use the imported
names.  Everything else (`HTML_FILE`, `OPEN_BROWSER`, the pgkit
`Hub` wiring) stays in this module because those are launch-time
concerns, not server-side state.

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

The initial `playground_registry.register(...)` call below is
retried for ~30 s on boot.  If it fails (daemon down at process
start), the playground still serves HTTP and the daemon can pick it
up later via the browser autosync's `/playground_reregister` path
(see `pg_autosync.js`'s REGISTRY PRESENCE CHECK) — no restart
required.

CANVAS-PUSH WIRING (Slice D)
============================
`main()` creates a `pgkit.remote_hub.Hub` instance and passes it to
both:

  · `kit_serve(hub=_shared_hub)` — so the `/remote` WebSocket
    endpoint uses this hub instead of creating its own.

  · `inpaint_server.set_browser_hub(_shared_hub)` — so the canvas
    subscriber thread (spawned by `start_canvas_subscriber()`) has
    something to broadcast through.

Then `inpaint_server.start_canvas_subscriber()` spawns the thread
that connects to the daemon's event stream (port 12347) and forwards
`canvas_update` and `paste_applied` events to every connected
browser over their `/remote` WebSocket.  See `inpaint_server.py`'s
module docstring, CANVAS_PUSH SUBSCRIBER, and
`kritomatic_daemon/handlers/comfyui_generation.py`'s
`_publish_canvas_update` / `_publish_paste_applied` for the daemon
side.

The net effect: after a batch pastes into Krita, the playground's
canvas updates within ~20-90 ms (Slice E's per-paste optimistic
display), instead of waiting for the next autosync poll tick + a
fresh `doc.projection()` composite read."""

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

from pgkit.remote_hub import Hub
from pgkit.server import serve as kit_serve


HTML_FILE    = "inpaint.html"
OPEN_BROWSER = True

# Source of truth is `inpaint_server.py` — see the module docstring,
# PLAYGROUND IDENTITY.  Re-imported here under the same names so
# anything that historically imported them off this module keeps
# working.
PORT            = inpaint_server.PLAYGROUND_PORT
PLAYGROUND_ID   = inpaint_server.PLAYGROUND_ID
PLAYGROUND_NAME = inpaint_server.PLAYGROUND_NAME
LAYER_SLOTS     = inpaint_server.LAYER_SLOTS


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

    # Canvas-push wiring (Slice D).  Create the hub, register it with
    # the inpaint server so the subscriber thread can broadcast
    # through it, spawn the subscriber, then pass the same hub to
    # `kit_serve()` so the `/remote` WebSocket endpoint uses it.  See
    # the module docstring, CANVAS-PUSH WIRING.
    shared_hub = Hub()
    inpaint_server.set_browser_hub(shared_hub)
    inpaint_server.start_canvas_subscriber()

    kit_serve(
        port           = PORT,
        open_browser   = OPEN_BROWSER,
        html_file      = HTML_FILE,
        routes         = inpaint_server.ROUTES,
        raw_routes     = inpaint_server.RAW_ROUTES,
        module_sources = inpaint_html.module_sources(),
        hub_name       = "inpaint",
        hub            = shared_hub,
    )


if __name__ == "__main__":
    main()
