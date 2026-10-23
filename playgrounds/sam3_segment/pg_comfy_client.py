COMFY_CLIENT_JS = r"""
import { board } from "./pg_core.js";
import { pgkitNotifyAll } from "./pgkit/remote_client.js";
import { flashStatus } from "./pgkit/panel_core.js";

let _inFlight = false;

export function isBusy() { return _inFlight; }

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

export async function runSegment() {
  if (!board.imageHash) {
    flashStatus("Load an image first", "warn");
    return null;
  }
  if (_inFlight) {
    await interruptSegmentation();
  }
  _inFlight = true;
  board.running = true;
  pgkitNotifyAll();

  const body = {
    hash:              board.imageHash,
    positive:          board.positive,
    negative:          board.negative,
    text:              board.textPrompt,
    threshold:         board.threshold,
    refine_iterations: board.refineIterations,
    individual_masks:  board.individualMasks,
  };
  try {
    const res = await fetch("/comfy/segment", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const payload = await res.json();
    if (!payload.success) {
      flashStatus("Segment failed: " + (payload.message || "unknown"), "bad");
      return null;
    }
    board.lastTiming = payload.timing;
    return payload;
  } catch (e) {
    flashStatus("Request failed: " + e.message, "bad");
    return null;
  } finally {
    _inFlight = false;
    board.running = false;
    pgkitNotifyAll();
  }
}

export async function interruptSegmentation() {
  try {
    await fetch("/comfy/interrupt", { method: "POST" });
  } catch (e) {
    // ignore
  }
}
"""
