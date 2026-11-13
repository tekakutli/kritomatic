"""HTTP bridge to the inpaint backend.

Two actions remain:

  - `uploadImageFile(file)` — used by the file-drop / pick-image path.
    Uploads the raw file to `/comfy/upload_image` and returns its
    content hash, dimensions, and the data URL the browser will
    display.  (The server never sees ComfyUI; this endpoint is a
    local decoder + hash.)

  - `fetchStatus()` — ComfyUI preflight, used by the status-line click
    handler to confirm the server is reachable.

The actual state push into Krita scratch layers is in `pg_sync.js`;
that module talks to `/sync` directly.  Generation is triggered by
the docker, not by this playground."""

COMFY_CLIENT_JS = r"""
let _inFlightCount = 0;

export function isBusy() { return _inFlightCount > 0; }

export async function uploadImageFile(file) {
  const res = await fetch("/comfy/upload_image", {
    method: "POST",
    headers: {
      "Content-Type": "application/octet-stream",
      "X-Filename": encodeURIComponent(file.name || "image"),
    },
    body: file,
  });
  if (!res.ok) throw new Error("HTTP " + res.status);
  const payload = await res.json();
  if (!payload.success) throw new Error(payload.message || "upload failed");
  return payload;
}

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