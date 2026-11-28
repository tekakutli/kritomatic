"""HTTP bridge to the outpaint server.

The three daemon-facing functions are re-exported from pgruntime's
shared `makeKritaClient` factory — identical to magi's, layout's,
multimask's, controlnets', and segment's.  `generate()` and
`sendToKrita()` are outpaint-specific: `/generate` triggers
`comfy_generate --no_paste` via the daemon (used by external callers
and debugging; the browser UI triggers generation via the docker's
global Run button, and the result arrives through the event bus, not
through `/generate`).  `/send_to_krita` chains
`create_new_with_dimensions` + `comfy_paste_image_b64` to materialise
a previewed result as a new Krita document.

URL defaults (`/krita_status`, `/active_state`, `/resource_image`)
match outpaint's flat routes — no override object needed.
"""

CLIENT_JS = r"""
import { makeKritaClient } from "./pgruntime/js/daemon_client.js";

const _krita = makeKritaClient();

export const checkKritaStatus   = _krita.checkKritaStatus;
export const fetchActiveState   = _krita.fetchActiveState;
export const fetchResourceImage = _krita.fetchResourceImage;

// Trigger generation through the daemon with no_paste.  Returns
// {success, b64, count} on success — b64 is the base64-encoded PNG
// payload (no data: prefix).  See outpaint_server.py's /generate.
export async function generate() {
  try {
    const res = await fetch("/generate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: "{}",
    });
    if (!res.ok) return { success: false, message: "HTTP " + res.status };
    return await res.json();
  } catch (e) {
    return { success: false, message: e.message };
  }
}

// Materialize the current PNG as a brand-new Krita document.  See
// outpaint_server.py's /send_to_krita.
export async function sendToKrita(b64, width, height, name) {
  try {
    const res = await fetch("/send_to_krita", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ b64, width, height, name: name || "" }),
    });
    if (!res.ok) return { success: false, message: "HTTP " + res.status };
    return await res.json();
  } catch (e) {
    return { success: false, message: e.message };
  }
}
"""
