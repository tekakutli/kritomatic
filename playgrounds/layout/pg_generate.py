"""Generate button: submit, poll, and reflect status in the panel.

Uses the playground's `/generate` and `/generate_status` routes.

Inputs read at click time:
  · `#promptText`             — global prompt for CLIP.
  · `#seedInput`              — optional fixed seed; blank → server
                                picks a random one.
  · `board.textBboxes[*].text` — per-bubble strings.
  · `board.viewer.ratio`      — current viewer aspect ratio key.

Seed display
============
`#seedInput` is the ONLY writable seed field.  It is never populated
by code — the user types a value or leaves it blank.  A blank value
means "random next time", which is what makes iteration fast.

`#lastSeedDisplay` is a separate read-only `<code>` element in the
State section that shows the seed the most recent successful
generation used, for copying.  It is never written into
`#seedInput`.  Click to copy.

Multiple concurrent generations
===============================
Each click starts a new server-side job and a new client-side poll
loop.  The button is never disabled due to a generation in flight;
it updates its text to show the number of active jobs.
"""
GENERATE_JS = r"""
import { board } from "./pg_state.js";
import { flashStatus } from "./pgkit/panel_core.js";

const POLL_INTERVAL_MS = 1000;
const MAX_POLL_S       = 200;

let _activeJobs = new Set();
let _lastSeedInstalled = false;

function _statusEl() {
  return document.getElementById("generateStatus");
}

function _setStatus(text, cls) {
  const el = _statusEl();
  if (!el) return;
  el.textContent = text;
  el.style.color = cls === "bad" ? "#c5221f"
                 : cls === "ok"  ? "#0d652d"
                 : cls === "warn"? "#a05a00"
                 : "#5a6774";
}

function _readPrompt() {
  const el = document.getElementById("promptText");
  return el ? String(el.value || "") : "";
}

function _readSeed() {
  const el = document.getElementById("seedInput");
  if (!el) return null;
  const v = String(el.value || "").trim();
  if (!v) return null;
  const n = parseInt(v, 10);
  return Number.isFinite(n) ? n : null;
}

function _readBubbleTexts() {
  return board.textBboxes
    .map(b => String(b.text || "").trim())
    .filter(t => t.length > 0);
}

function _writeLastSeed(seed) {
  const el = document.getElementById("lastSeedDisplay");
  if (!el) return;
  el.textContent = (seed === undefined || seed === null)
    ? "\u2014"
    : String(seed);
}

function _installLastSeedCopy() {
  if (_lastSeedInstalled) return;
  const el = document.getElementById("lastSeedDisplay");
  if (!el) return;
  el.style.cursor = "pointer";
  el.title = "Click to copy";
  el.addEventListener("click", async () => {
    const txt = el.textContent || "";
    if (!txt || txt === "\u2014") return;
    try {
      await navigator.clipboard.writeText(txt);
      flashStatus("seed copied", "ok");
    } catch (_) {
      flashStatus("copy failed", "bad");
    }
  });
  _lastSeedInstalled = true;
}

export function isGenerating() {
  return _activeJobs.size > 0;
}

export async function triggerGeneration() {
  if (!board.imageEl) {
    flashStatus("load an image first", "warn");
    _setStatus("load an image first", "warn");
    return;
  }
  const v = board.viewer;
  const payload = {
    viewer_x_norm: v.x,
    viewer_y_norm: v.y,
    viewer_w_norm: v.w,
    viewer_h_norm: v.h,
    image_width:   board.imageWidth,
    image_height:  board.imageHeight,
    ratio:         v.ratio,
  };

  const prompt = _readPrompt();
  if (prompt) payload.prompt = prompt;
  const seed = _readSeed();
  if (seed !== null) payload.seed = seed;
  const bubbles = _readBubbleTexts();
  if (bubbles.length) payload.bubble_texts = bubbles;

  flashStatus("submitting generation…", "warn");
  _setStatus("submitting…", "warn");

  let jobId;
  try {
    const resp = await fetch("/generate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const data = await resp.json();
    if (!data.success) {
      _setStatus("submit failed: " + (data.message || "unknown"), "bad");
      flashStatus("submit failed", "bad");
      return;
    }
    jobId = data.job_id;
  } catch (e) {
    _setStatus("submit error: " + e.message, "bad");
    flashStatus("submit error", "bad");
    return;
  }

  _activeJobs.add(jobId);
  _updateButtonState();
  _poll(jobId, 0);
}

function _updateButtonState() {
  const btn = document.getElementById("generateBtn");
  if (!btn) return;
  btn.disabled = !board.imageEl;
  if (_activeJobs.size > 0) {
    btn.textContent = `Generate (${_activeJobs.size})`;
  } else {
    btn.textContent = "Generate";
  }
}

async function _poll(jobId, elapsedMs) {
  if (elapsedMs > MAX_POLL_S * 1000) {
    _finish(jobId, "error", "timed out");
    return;
  }

  let data;
  try {
    const resp = await fetch("/generate_status", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ job_id: jobId }),
    });
    data = await resp.json();
  } catch (e) {
    setTimeout(() => _poll(jobId, elapsedMs + POLL_INTERVAL_MS), POLL_INTERVAL_MS);
    return;
  }

  if (!data.success) {
    _finish(jobId, "error", data.message || "unknown error");
    return;
  }

  const status  = data.status || "unknown";
  const message = data.message || "";

  if (status === "done") {
    _finish(jobId, "done", message, data.seed);
    return;
  }
  if (status === "error") {
    _finish(jobId, "error", message);
    return;
  }

  _setStatus(`${_activeJobs.size} running: ${status} - ${message}`, "warn");

  setTimeout(() => _poll(jobId, elapsedMs + POLL_INTERVAL_MS), POLL_INTERVAL_MS);
}

function _finish(jobId, kind, message, seed) {
  _activeJobs.delete(jobId);
  _updateButtonState();

  if (kind === "done") {
    _setStatus("done: " + message, "ok");
    flashStatus("generation done", "ok");

    // Show the used seed in the read-only display.  #seedInput is
    // NEVER written by code — a blank input means "random next
    // time", and a typed input is the user's choice to repeat.
    if (seed !== undefined && seed !== null) {
      _writeLastSeed(seed);
    }

    // Auto-refresh @thelayout
    import("./pg_autosync.js")
      .then(m => {
        if (m && typeof m.refreshLayoutCanvas === "function") {
          m.refreshLayoutCanvas();
        }
      })
      .catch(() => {});
  } else {
    _setStatus("error: " + message, "bad");
    flashStatus("generation error: " + message, "bad");
  }
}

export function refreshGenerateButton() {
  _updateButtonState();
}

export function installGenerateButton() {
  _installLastSeedCopy();
  const btn = document.getElementById("generateBtn");
  if (!btn) return;
  btn.addEventListener("click", triggerGeneration);
  refreshGenerateButton();
}
"""