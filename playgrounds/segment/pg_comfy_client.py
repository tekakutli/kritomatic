"""HTTP bridge to the ComfyUI backend.  Every action is a fetch to the
Python server; the server does the ComfyUI heavy lifting.

IMPORT SPLIT
============
`board` comes from `pg_state.js` (leaf).  `computeEffectiveBBox` comes
from `pg_pixels.js`.  Neither import changes the body; only the header.
The old import line was `import { board, computeEffectiveBBox } from
"./pg_core.js"`."""

COMFY_CLIENT_JS = r"""
import { board } from "./pg_state.js";
import { computeEffectiveBBox } from "./pg_pixels.js";
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
   Segment All — uses staging points + global prompt
   ============================================================ */

export async function runSegmentAll() {
  if (!board.imageHash) {
    flashStatus("Load an image first", "warn");
    return null;
  }
  if (_inFlightCount > 0) await interruptSegmentation();

  _inFlightCount += 1;
  board.running = true;
  pgkitNotifyAll();

  const body = {
    hash:              board.imageHash,
    positive:          board.stagingPoints
                         .filter(p =>  p.isPositive)
                         .map(p => ({ x: p.x, y: p.y })),
    negative:          board.stagingPoints
                         .filter(p => !p.isPositive)
                         .map(p => ({ x: p.x, y: p.y })),
    text:              board.textPrompt,
    threshold:         board.threshold,
    refine_iterations: board.refineIterations,
    individual_masks:  !!board.individualMasks,
    bbox:              null,
  };

  try {
    const res = await fetch("/comfy/segment", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const payload = await res.json();
    if (!payload.success) {
      flashStatus("Failed: " + (payload.message || "unknown"), "bad");
      return null;
    }
    board.lastTiming = payload.timing;
    return payload;
  } catch (e) {
    flashStatus("Request failed: " + e.message, "bad");
    return null;
  } finally {
    _inFlightCount -= 1;
    if (_inFlightCount === 0) board.running = false;
    pgkitNotifyAll();
  }
}

/* ============================================================
   Refine — per-mask points + per-mask prompt (SAM3) + mask_b64 (SAM2)
   ============================================================ */

export async function runRefine(maskIdx) {
  if (!board.imageHash) {
    flashStatus("Load an image first", "warn");
    return null;
  }
  if (_inFlightCount > 0) await interruptSegmentation();

  const mask = board.masks[maskIdx];
  if (!mask || !mask.loaded) {
    flashStatus("Mask not loaded yet", "warn");
    return null;
  }

  _inFlightCount += 1;
  board.running = true;
  pgkitNotifyAll();

  const positive = mask.points.filter(p =>  p.isPositive)
                              .map(p => ({ x: p.x, y: p.y }));
  const negative = mask.points.filter(p => !p.isPositive)
                              .map(p => ({ x: p.x, y: p.y }));

  // Unified payload. Server picks what it needs based on `model`.
  const body = {
    model:             board.refineModel,
    hash:              board.imageHash,
    mask_b64:          mask.b64,             // SAM2 only
    positive,
    negative,
    text:              mask.textPrompt || "",// SAM3 only
    threshold:         board.threshold,       // SAM3 only
    refine_iterations: board.refineIterations,// SAM3 only
    individual_masks:  true,                  // SAM3 refine: always
    bbox:              computeEffectiveBBox(maskIdx), // SAM3 only
  };

  try {
    const res = await fetch("/comfy/refine", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const payload = await res.json();
    if (!payload.success) {
      flashStatus("Failed: " + (payload.message || "unknown"), "bad");
      return null;
    }
    board.lastTiming = payload.timing;
    return payload;
  } catch (e) {
    flashStatus("Request failed: " + e.message, "bad");
    return null;
  } finally {
    _inFlightCount -= 1;
    if (_inFlightCount === 0) board.running = false;
    pgkitNotifyAll();
  }
}

export async function interruptSegmentation() {
  try { await fetch("/comfy/interrupt", { method: "POST" }); }
  catch (e) {}
}
"""
