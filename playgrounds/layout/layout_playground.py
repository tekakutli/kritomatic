"""Layout playground — entry point.

    python layout_playground.py

Writes layout.html, serves it at http://127.0.0.1:8777/.  State is
served at /state for the `DrawatoonSetIP` ComfyUI node.

Registers with the kritomatic daemon on startup (retry ~30s).  The
docker renders a "Playground Input" section with one row per
(playground, layer slot) — for this playground, one row for
`@thelayout` with a `[source▼][layer]` picker.  The playground polls
that selector and re-fetches the displayed layer whenever it
changes.  When the daemon dies, the playground exits (background
watcher, ~10s of consecutive failures); the user restarts both
manually.

State shape (POST /state):

    {
      "op": "set",
      "state": {
        "text_bboxes":          [[x1,y1,x2,y2], ...],
        "character_bboxes":     [[x1,y1,x2,y2], ...],
        "reference_embeddings": [null | [768 floats], ...],
        "image_dims":           [W, H]
      }
    }

All bboxes are normalized [0,1] in (x1,y1,x2,y2) order.

drawatoon is a **512-px model**. In the sampler workflow use
`PixArtResolutionSelect` with `model = "PixArtMS_Sigma_XL_2_512"`.
1024x1024 will produce blocky output.
"""

import atexit
import os
import signal
import sys
import threading
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
_PLAYGROUNDS = os.path.dirname(_HERE)

try:
    sys.path.remove(_HERE)
except ValueError:
    pass
if _PLAYGROUNDS not in sys.path:
    sys.path.insert(0, _PLAYGROUNDS)

from layout import layout_html, layout_server, playground_registry

from pgkit.server import serve as kit_serve


HTML_FILE    = "layout.html"
PORT         = 8777
OPEN_BROWSER = True

PLAYGROUND_ID   = layout_server.PLAYGROUND_ID
PLAYGROUND_NAME = layout_server.PLAYGROUND_NAME
LAYER_SLOTS     = layout_server.LAYER_SLOTS

_DAEMON_WATCH_INTERVAL_S = 2.0
_DAEMON_WATCH_MAX_FAILS  = 5


def descriptor():
    return {
        "name":           PLAYGROUND_ID,
        "port":           PORT,
        "html_file":      HTML_FILE,
        "html_render":    layout_html.render,
        "routes":         layout_server.ROUTES,
        "raw_routes":     layout_server.RAW_ROUTES,
        "module_sources": layout_html.module_sources(),
    }


def _watch_daemon_and_exit():
    """Exit the playground when the daemon dies."""
    from layout import krita_client

    consecutive = 0
    while True:
        time.sleep(_DAEMON_WATCH_INTERVAL_S)
        try:
            resp = krita_client.send(
                {"type": "comfy_status"}, timeout=3.0,
            )
            ok = (isinstance(resp, dict)
                  and resp.get("status") == "success")
        except Exception:
            ok = False

        if ok:
            consecutive = 0
            continue

        consecutive += 1
        if consecutive >= _DAEMON_WATCH_MAX_FAILS:
            print(
                f"[layout] daemon unreachable for "
                f"{consecutive} consecutive checks; exiting",
                file=sys.stderr, flush=True,
            )
            os._exit(0)


def _unregister():
    playground_registry.unregister(PLAYGROUND_ID)


def _sigterm(signum, frame):
    _unregister()
    sys.exit(0)


def main():
    html = layout_html.render()
    with open(HTML_FILE, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"Wrote {HTML_FILE}")
    print(f"  routes   : {', '.join(sorted(list(layout_server.ROUTES) + list(layout_server.RAW_ROUTES)))}")
    print(f"  state    : http://127.0.0.1:{PORT}/state")

    url = f"http://127.0.0.1:{PORT}"
    registered = playground_registry.register(
        PLAYGROUND_ID, PLAYGROUND_NAME, url, PORT,
        layer_slots=LAYER_SLOTS,
        retry_s=30.0,
    )
    if registered:
        print(
            f"  registered with daemon as {PLAYGROUND_ID!r} "
            f"(slot(s): {[s['id'] for s in LAYER_SLOTS]})"
        )
    else:
        print(
            f"  WARNING: could not register with daemon; "
            f"docker will not show this playground. "
            f"Playground works independently; restart after daemon "
            f"is up to register.",
            file=sys.stderr,
        )

    atexit.register(_unregister)
    signal.signal(signal.SIGTERM, _sigterm)

    threading.Thread(
        target=_watch_daemon_and_exit,
        name="layout-daemon-watch",
        daemon=True,
    ).start()

    kit_serve(
        port           = PORT,
        open_browser   = OPEN_BROWSER,
        html_file      = HTML_FILE,
        routes         = layout_server.ROUTES,
        raw_routes     = layout_server.RAW_ROUTES,
        module_sources = layout_html.module_sources(),
        hub_name       = "layout",
    )


if __name__ == "__main__":
    main()