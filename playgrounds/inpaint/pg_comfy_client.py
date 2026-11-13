"""HTTP bridge to the inpaint backend.  Every action is a fetch to the
Python server; the server does the ComfyUI + daemon heavy lifting.

This is the inpaint-only variant: `runInpaint` replaces the SAM3
`runSegmentAll` / `runRefine`.  No bbox computation, no per-mask
points, no SAM2/SAM3 model selection — the inpaint workflow does not
use any of that.

INTERCEPTOR BRANCH
==================
`runInpaintViaInterceptor` is the alternative path used when the
panel's "Route via interceptor" checkbox is on.  It:

    1. POSTs the payload to the local server's `/intercept_start`,
       which forwards it to the configured interceptor playground.
    2. Polls the local server's `/intercept_poll` for the result.
    3. Returns the same shape as `runInpaint` — `{success, image_b64,
       timing}` on success, `null` on error.

`interruptInpaint` (the function `cancel` binds to) is updated to
route through `/intercept_cancel` when a task is in flight on the
interceptor, falling back to `/comfy/interrupt` otherwise.

The interceptor side posts the result back to the source's
`/intercept_callback` route; that callback writes to a server-side
dict, which `/intercept_poll` reads.  See
`inpaint_server.handle_intercept_*`.

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

/* POST, not GET.  /comfy/status is registered in inpaint_server.py's
   ROUTES and dispatched via pgkit/server.py::do_POST.  A GET falls
   through to SimpleHTTPRequestHandler.do_GET which serves files and
   returns 404 HTML — res.json() then throws "Unexpected token '<'",
   which _checkStatusAction flashes as "ComfyUI unreachable:
   Unexpected token <".  Same fix segment's fetchStatus needed. */
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

/* Read the daemon's shared-state prompt (i.e. `_active_config
   ["params"]["prompt"]`).  Used on boot to populate the playground
   prompt field when it is empty — see `_hydrateSharedPrompt` in
   `pg_panel_bindings.js`. */
export async function fetchCurrentPrompt() {
  try {
    const res = await fetch("/comfy/current_prompt", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: "{}",
    });
    if (!res.ok) {
      return { success: false, prompt: "", message: "HTTP " + res.status };
    }
    return await res.json();
  } catch (e) {
    return { success: false, prompt: "", message: e.message };
  }
}

/* ============================================================
   Inpaint — the single operation this playground performs
   ============================================================ */

export async function runInpaint({ canvasB64, selectionB64, contextB64,
                                   prompt, outputRotation }) {
  if (_inFlightCount > 0) await interruptInpaint();

  _inFlightCount += 1;
  board.running = true;
  pgkitNotifyAll();

  const t0 = performance.now();
  try {
    const res = await fetch("/comfy/inpaint", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        canvas_b64:    canvasB64,
        selection_b64: selectionB64,
        context_b64:   contextB64,
        prompt:        prompt,
        output_rotation: outputRotation || 0,
      }),
    });
    const t1 = performance.now();
    console.log("[inpaint] fetch resolved after",
                (t1 - t0).toFixed(0), "ms; status", res.status,
                "ok", res.ok);

    const payload = await res.json();
    const t2 = performance.now();
    console.log("[inpaint] json parsed after",
                (t2 - t1).toFixed(0), "ms; success:", payload.success,
                "b64 length:", (payload.image_b64 || "").length);

    if (!payload.success) {
      flashStatus("inpaint failed: " + (payload.message || "unknown"), "bad");
      return null;
    }
    board.lastTiming = payload.timing;
    return payload;
  } catch (e) {
    const tEnd = performance.now();
    console.error("[inpaint] fetch threw after",
                  (tEnd - t0).toFixed(0), "ms:", e);
    flashStatus("inpaint failed: " + e.message, "bad");
    return null;
  } finally {
    _inFlightCount -= 1;
    if (_inFlightCount === 0) board.running = false;
    pgkitNotifyAll();
  }
}

/* ============================================================
   Inpaint via interceptor
   ============================================================
   Same external contract as `runInpaint` — takes the same
   arguments plus `interceptorUrl` and `callbackUrl`, returns
   `{success, image_b64, timing}` or `null`.  The difference is
   *where the work happens*: our Python server forwards the payload
   to the interceptor playground, the interceptor runs
   `handle_inpaint` (the same function the direct path uses), and
   posts the result back to our server's `/intercept_callback`.
   We poll for it.

   `board.currentInterceptTaskId` is set for the duration so
   `interruptInpaint` (which the Cancel button binds to) knows to
   POST to `/intercept_cancel` rather than `/comfy/interrupt`, and
   so `syncPanelFields` shows "waiting for interceptor…" instead of
   "inpainting…". */

export async function runInpaintViaInterceptor({
  canvasB64, selectionB64, contextB64, prompt, outputRotation,
  interceptorUrl, callbackUrl,
}) {
  if (_inFlightCount > 0) await interruptInpaint();

  _inFlightCount += 1;
  board.running = true;
  pgkitNotifyAll();

  try {
    // 1. Ask our own server to forward the payload to the
    //    interceptor.  Synchronous: a few hundred ms to localhost,
    //    or an immediate error if the interceptor is unreachable.
    let startResp;
    try {
      const res = await fetch("/intercept_start", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          interceptor_url: interceptorUrl,
          callback_url:    callbackUrl,
          canvas_b64:      canvasB64,
          selection_b64:   selectionB64,
          context_b64:     contextB64,
          prompt:          prompt,
          output_rotation: outputRotation || 0,
        }),
      });
      startResp = await res.json();
    } catch (e) {
      flashStatus("interceptor unreachable: " + e.message, "bad");
      return null;
    }
    if (!startResp || !startResp.success) {
      flashStatus("interceptor: "
                  + ((startResp && startResp.message) || "unknown"),
                  "bad");
      return null;
    }

    const taskId = startResp.task_id;
    board.currentInterceptTaskId = taskId;
    pgkitNotifyAll();

    // 2. Poll our own server for the result.
    const deadline = performance.now() + 10 * 60 * 1000;
    while (performance.now() < deadline) {
      await new Promise(r => setTimeout(r, 750));
      let poll;
      try {
        const res = await fetch("/intercept_poll", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ task_id: taskId }),
        });
        poll = await res.json();
      } catch (e) {
        continue;
      }
      if (!poll || !poll.status) continue;
      if (poll.status === "pending") continue;
      if (poll.status === "complete") {
        board.lastTiming = poll.timing;
        return {
          success:   true,
          image_b64: poll.image_b64,
          timing:    poll.timing,
        };
      }
      if (poll.status === "error") {
        flashStatus("interceptor: " + (poll.message || "unknown"), "bad");
        return null;
      }
    }
    flashStatus("interceptor timed out", "bad");
    return null;
  } catch (e) {
    console.error("[intercept] threw:", e);
    flashStatus("interceptor: " + e.message, "bad");
    return null;
  } finally {
    board.currentInterceptTaskId = null;
    _inFlightCount -= 1;
    if (_inFlightCount === 0) board.running = false;
    pgkitNotifyAll();
  }
}

export async function interruptInpaint() {
  if (board.currentInterceptTaskId) {
    try {
      await fetch("/intercept_cancel", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          task_id: board.currentInterceptTaskId,
        }),
      });
    } catch (e) {}
    return;
  }
  try { await fetch("/comfy/interrupt", { method: "POST" }); }
  catch (e) {}
}
"""