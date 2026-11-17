"""HTTP bridge to the inpaint backend.

One action remains:

  - `fetchStatus()` — ComfyUI preflight, used by the status-line click
    handler to confirm the server is reachable.

The image-upload path (`uploadImageFile`) is gone along with the
pick-image / drop-overlay buttons.  The canvas is pulled exclusively
from the daemon via `pg_krita_client.js` (autosync + manual refresh),
so there is no client-side upload flow anymore.

The actual state push into Krita scratch layers is in `pg_sync.js`;
that module talks to `/sync` directly.  Generation is triggered by
the docker, not by this playground."""

COMFY_CLIENT_JS = r"""
let _inFlightCount = 0;

export function isBusy() { return _inFlightCount > 0; }

/* POST, not GET.  /comfy/status is registered in inpaint_server.py's
   ROUTES and dispatched via pgkit/server.py::do_POST. */
export async function fetchStatus() {
  try {
    const res = await fetch("/comfy/status", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: "{}",
    });
    if (!res.ok) return { success: false, reachable: false, message: "HTTP " + res.status };
    return await res.json();
  } catch (e) {
    return { success: false, reachable: false, message: e.message };
  }
}
"""