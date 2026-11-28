"""HTTP bridge to the rmbg backend for ComfyUI preflight.

One action remains: `fetchStatus()`, used by the status-line click
handler to confirm ComfyUI is reachable.  No image upload, no
generation submission — this playground pushes state only."""

COMFY_CLIENT_JS = r"""
export function isBusy() { return false; }

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