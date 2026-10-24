"""HTTP bridge to the inpaint backend.  Every action is a fetch to the
Python server; the server does the ComfyUI + daemon heavy lifting.

This is the inpaint-only variant: `runInpaint` replaces the SAM3
`runSegmentAll` / `runRefine`.  No bbox computation, no per-mask
points, no SAM2/SAM3 model selection — the inpaint workflow does not
use any of that.

IMPORT SPLIT
============
`board` comes from `pg_state.js` (leaf)."""

COMFY_CLIENT_JS = r"""
import { board } from "./pg_state.js";
import { pgkitNotifyAll } from "./pgkit/remote_client.js";
import { flashStatus } from "./pgkit/panel_core.js";

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

export async function fetchStatus() {
  try {
    const res = await fetch("/comfy/status");
    return await res.json();
  } catch (e) {
    return { success: false, reachable: false, message: e.message };
  }
}

/* ============================================================
   Inpaint — the single operation this playground performs
   ============================================================ */

export async function runInpaint({ canvasB64, selectionB64, contextB64, prompt }) {
  if (_inFlightCount > 0) await interruptInpaint();

  _inFlightCount += 1;
  board.running = true;
  pgkitNotifyAll();

  try {
    const res = await fetch("/comfy/inpaint", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        canvas_b64:    canvasB64,
        selection_b64: selectionB64,
        context_b64:   contextB64,
        prompt:        prompt,
      }),
    });
    const payload = await res.json();
    if (!payload.success) {
      flashStatus("inpaint failed: " + (payload.message || "unknown"), "bad");
      return null;
    }
    board.lastTiming = payload.timing;
    return payload;
  } catch (e) {
    flashStatus("inpaint failed: " + e.message, "bad");
    return null;
  } finally {
    _inFlightCount -= 1;
    if (_inFlightCount === 0) board.running = false;
    pgkitNotifyAll();
  }
}

export async function interruptInpaint() {
  try { await fetch("/comfy/interrupt", { method: "POST" }); }
  catch (e) {}
}
"""
