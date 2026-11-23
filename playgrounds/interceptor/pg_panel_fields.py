"""Panel sync: task list, selected-task details, prompt textarea,
multimask area cards, controlnets list, button enable/disable,
result display.

`syncPanelFields` is called every frame from `draw()`.  Every DOM
write is guarded against the current value so a steady-state frame
touches nothing.

The prompt textarea and the multimask prompt textareas are NOT
written by `syncPanelFields` unless the current DOM value differs
from the model AND the element is not focused.  `_selectTask` in
`pg_panel_bindings.js` sets the initial values when the selection
changes; user typing is preserved across frames.

MULTIMASK AREA CARDS
====================
Cards are rebuilt only when the set of area ids changes.  Prompt
values are updated in place per-frame (skip-if-focused) so typing is
never interrupted.  Each card carries:

    · swatch       — area colour (mirrors multimask)
    · name         — from the snapshot's area.name
    · dims         — from area.mask.width × area.mask.height
    · prompt       — textarea, value from board.areaPromptOverrides
                     if present, else area.prompt

The section itself is hidden when `board.multimask` is null (no
snapshot → the workflow doesn't use multimask).

CONTROLNETS LIST
================
Display-only.  Rebuilt when the entry-hash set or the enabled-count
changes.  Each row carries:

    · thumbnail    — 64×64 letterboxed `<img>` decoded from the
                     snapshot's `images[hash]` b64, or a "?" placeholder
                     if the transform was missing for that entry
    · name         — from the snapshot's entry name (or id)
    · dims         — image hash entry's width × height

The section itself is hidden when `item.controlnets` is null (no
snapshot → the workflow doesn't use `ControlNetsConditioning`).

Per-entry "disabled" state is NOT rendered.  `state.apply_enabled`
is keyed by `ControlNetApplyAdvanced` titles; `state.controlnets`
entries are keyed by preprocessor titles; a preprocessor may feed
multiple apply nodes.  From the state alone we cannot determine
whether a given preprocessor is "disabled" (it is, only if every
downstream apply node is off), so the info line just reports the
overall "N/M apply node(s) enabled" count and every entry renders
identically.  The `.cnDisabled` CSS rule exists in `pg_base.py` for a
future feature that can express the per-entry correlation.

CONTROLNETS THUMBNAIL STALENESS — FIXED
=======================================
The interceptor's controlnets thumbnails are DOM `<img>` elements
whose `src` holds the transformed b64 captured at intake time.
Prior to this fix, `_syncControlnetsSection`'s rebuild cache keyed
on the raw preprocessed hashes plus the apply-enabled count, which
are invariant across inpaint crop-frame rotations — rotating
inpaint and starting a new intercepted run produces a new snapshot
with the *same* raw hashes but *different* transformed b64 bytes,
so the panel's cache would hit and the thumbnails would keep
showing the previous task's transformed images.

The fix prepends `board.selectedTaskId` to the rebuild signature,
forcing a rebuild on every task switch.  A new intercepted run
creates a new `batch_id`, so the signature always changes when the
user clicks a new queued task; re-selecting the currently-selected
task does not (its snapshot is frozen at intake).

The same treatment was applied to `_syncMultimaskAreas` as a
defensive consistency measure.  Its masks are canvas-drawn per frame
from `board.multimask.masks`, so they were not affected by the same
bug; its prompt textareas are synced in the else branch, which
correctly overwrites on task switch.  The task id is still included
in its signature so both sections in this file follow the same
"task switch always rebuilds" invariant.

PENDING LOCAL RUNS
==================
Every status render and every status gate in this module reads
through `effectiveStatus(task)` (exported from `pg_state.js`), not
through `task.status` directly:

  · `_syncTaskList` builds its diff signature from
    `effectiveStatus(t)`, so a task entering or leaving
    `board.pendingLocalRuns` re-renders the list, and the row's
    status badge does not flip back to `"queued"` before the
    server has actually flipped the status.

  · `_syncTaskInfo` reads through `effectiveStatus(item)` so the
    detail pane's "status:" line reports `"running"` during the
    local-pending window.

  · `_syncResult` gates its "inpainting…" line on
    `effectiveStatus(item) === "running"` so the result line
    matches the button state.

  · `syncPanelFields` gates the Run / Discard buttons on
    `effectiveStatus(summary) === "queued"` so they disable
    immediately on click and stay disabled across the brief window
    before the server has processed the request.

This is what keeps the panel consistent during the click → server-
flip window.  See `pg_state.py`'s "PENDING LOCAL RUNS" for the full
rationale.

EXPIRY AND PROMPT-OVERRIDE FALLTHROUGH
======================================
Two additional states surfaced in `_syncResult`:

  · **`expired`** — the daemon's 15-minute intercept timeout swept
    the batch.  Surfaced with a `"re-intercept from the docker"` 
    action so the user knows there is a recovery path (there is no
    in-place recovery: the prepared graph has been dropped).

  · **`prompt_override_applied === false`** — the daemon's
    `comfy_resume_intercepted` reports this when the workflow has no
    `Primitive` node titled `"prompt"`.  In that case the user's
    typed override is silently dropped by the daemon; without the
    suffix below, a successful run with a dropped override is
    visually identical to a successful run with it applied.

OUTPAINT
========
A successful resume whose `result.outpaint` is `true` indicates the
daemon published the raw images to the outpaint playground instead of
pasting into Krita.  The user's original document was not modified.
The result line reads "outpaint result published" instead of "pasted
N layer(s)"; the outpaint playground's browser tab is the place to
view / Send to Krita."""

PANEL_FIELDS_JS = r"""
import { board, areaColorById, effectiveStatus } from "./pg_state.js";

let _els = null;
function _get() {
  if (_els) return _els;
  _els = {
    taskInfo:         document.getElementById("taskInfo"),
    taskList:         document.getElementById("taskList"),
    taskPayload:      document.getElementById("taskPayload"),
    promptInput:      document.getElementById("promptInput"),
    runBtn:           document.getElementById("runBtn"),
    discardBtn:       document.getElementById("discardBtn"),
    resultInfo:       document.getElementById("resultInfo"),
    multimaskSection: document.getElementById("multimaskSection"),
    multimaskInfo:    document.getElementById("multimaskInfo"),
    multimaskList:    document.getElementById("multimaskList"),
    showMultimaskLabel: document.getElementById("showMultimaskLabel"),
    showMultimaskCb:  document.getElementById("showMultimask"),
    controlnetsSection: document.getElementById("controlnetsSection"),
    controlnetsInfo:    document.getElementById("controlnetsInfo"),
    controlnetsList:    document.getElementById("controlnetsList"),
  };
  return _els;
}

function _fmtAge(sec) {
  if (sec < 60)   return Math.round(sec) + "s";
  if (sec < 3600) return Math.round(sec / 60) + "m";
  return Math.round(sec / 3600) + "h";
}

function _fmtParams(params) {
  if (!params || typeof params !== "object") return "\u2014";
  const keys = Object.keys(params).sort();
  if (keys.length === 0) return "\u2014";
  return keys.map(k => k + "=" + JSON.stringify(params[k])).join(", ");
}

let _taskListSig = null;
function _syncTaskList() {
  const e = _get();
  if (!e.taskList) return;

  // The signature reads through effectiveStatus so a task entering or
  // leaving board.pendingLocalRuns re-renders the list (the row's
  // status badge is what the user sees, and it should stay "running"
  // until the server confirms).  See this module's "PENDING LOCAL
  // RUNS" docstring section.
  const sig = board.order.map(tid => {
    const t = board.tasks[tid];
    return tid + ":" + (t ? effectiveStatus(t) : "");
  }).join("|") + "|" + (board.selectedTaskId || "");
  if (sig === _taskListSig) return;
  _taskListSig = sig;

  e.taskList.innerHTML = "";

  if (board.order.length === 0) {
    const div = document.createElement("div");
    div.className = "empty";
    div.textContent = "no tasks yet";
    e.taskList.appendChild(div);
    return;
  }

  const now = Date.now() / 1000;
  for (const tid of board.order) {
    const t = board.tasks[tid];
    if (!t) continue;
    const row = document.createElement("div");
    row.className = "taskRow"
                  + (tid === board.selectedTaskId ? " selected" : "");

    const label = document.createElement("span");
    label.className = "taskLabel";
    label.textContent = (t.workflow_name || t.source_label || "task")
                       + " \u00b7 " + _fmtAge(now - t.received_at);
    label.title = tid;
    row.appendChild(label);

    // Effective status, not t.status: a task this client has fired
    // Run / Discard for reports "running" even if the last poll still
    // shows "queued".  See pg_state.py's "PENDING LOCAL RUNS".
    const eff = effectiveStatus(t);
    const st = document.createElement("span");
    st.className = "taskStatus " + eff;
    st.textContent = eff;
    row.appendChild(st);

    row.addEventListener("click", () => {
      import("./pg_panel_bindings.js")
        .then(m => m._selectTask(tid))
        .catch(() => {});
    });

    e.taskList.appendChild(row);
  }
}

let _taskInfoSig = null;
function _syncTaskInfo(item) {
  const e = _get();
  if (!e.taskInfo) return;

  let text;
  if (!item) {
    text = "no task selected";
  } else {
    const p = item.payload || {};
    // Effective status, not item.status: reports "running" during the
    // local-pending window so the detail pane stays consistent with
    // the button state.
    const eff = effectiveStatus(item) || "?";
    const lines = [
      "workflow: " + (p.workflow_name || "?"),
      "status:   " + eff,
      "id:       " + item.task_id,
    ];
    if (item.message) lines.push("message:  " + item.message);
    text = lines.join("\n");
  }
  if (text === _taskInfoSig) return;
  _taskInfoSig = text;
  e.taskInfo.textContent = text;
}

let _taskPayloadSig = null;
function _syncTaskPayload(item) {
  const e = _get();
  if (!e.taskPayload) return;
  let text = "";
  if (item) {
    const p = item.payload || {};
    text = "params: " + _fmtParams(p.params);
    if (p.crop_bounds) {
      const cb = p.crop_bounds;
      text += "\ncrop:   " + cb[2] + "\u00d7" + cb[3]
            + " at (" + cb[0] + "," + cb[1] + ")"
            + "  rotation: " + (p.output_rotation || 0) + "\u00b0";
    }
    const w = p.warnings || [];
    if (w.length) {
      text += "\nwarnings: " + w.join("; ");
    }
    if (item.multimask && item.multimask.state) {
      const n = (item.multimask.state.areas || []).length;
      text += "\nmultimask: " + n + " area(s)";
    }
    if (item.controlnets && item.controlnets.state) {
      const n = (item.controlnets.state.controlnets || []).length;
      text += "\ncontrolnets: " + n + " preprocessor image(s)";
    }
  }
  if (text === _taskPayloadSig) return;
  _taskPayloadSig = text;
  e.taskPayload.textContent = text;
}

let _resultSig = null;
function _syncResult(item) {
  const e = _get();
  if (!e.resultInfo) return;
  let text = "", cls = "";
  // Effective status, not item.status: `effectiveStatus(item)` reports
  // "running" during the local-pending window, so the "inpainting…"
  // line appears as soon as Run is clicked rather than after the next
  // poll.  See pg_state.py's "PENDING LOCAL RUNS".
  const eff = item ? effectiveStatus(item) : null;
  if (item && item.result) {
    const r = item.result;
    const timing = r.timing != null
      ? (r.timing.toFixed ? r.timing.toFixed(2) : r.timing) + "s"
      : "?";
    if (r.outpaint) {
      // Outpaint workflow: the daemon published the raw result to
      // the outpaint playground instead of pasting into Krita.  The
      // user's original document was not modified.  They can view
      // the result and Send to Krita from the outpaint playground's
      // browser tab.
      text = "outpaint result published in " + timing
           + "\ncheck the outpaint playground tab";
    } else {
      const layers = (r.layer_names || []).join(", ") || "(none)";
      text = "pasted " + (r.output_count || 0) + " layer(s) in " + timing
           + "\n" + layers;
    }
    // If the daemon reports the override wasn't applied (no
    // workflow 'prompt' Primitive slot exists), tell the user.
    // Without this, a successful run with a dropped override looks
    // identical to a successful run with it applied.  The daemon
    // sends prompt_override_applied in the resume response data —
    // see comfy_resume_intercepted in
    // kritomatic_daemon/handlers/comfyui_intercept.py.  See this
    // module's docstring, EXPIRY AND PROMPT-OVERRIDE FALLTHROUGH.
    if (r.prompt_override_applied === false) {
      text += "\n(prompt not applied \u2014 workflow has no "
            + "'prompt' Primitive slot)";
    }
    cls = "ok";
  } else if (item && eff === "error") {
    text = "failed: " + (item.message || "unknown");
    cls = "err";
  } else if (item && eff === "running") {
    text = "inpainting\u2026";
    cls = "";
  } else if (item && eff === "cancelled") {
    text = "cancelled";
    cls = "";
  } else if (item && eff === "expired") {
    // The daemon's 15-minute intercept timeout swept this task.  The
    // prepared graph has been dropped — there is no in-place
    // recovery, only a re-intercept from the docker.  See this
    // module's docstring, EXPIRY AND PROMPT-OVERRIDE FALLTHROUGH,
    // and interceptor_server.py's "Task expiry".
    text = "expired \u2014 re-intercept from the docker";
    cls = "err";
  }
  const sig = text + "|" + cls;
  if (sig === _resultSig) return;
  _resultSig = sig;
  e.resultInfo.textContent = text;
  e.resultInfo.className = cls;
}

/* ============================================================
   Multimask area cards
   ============================================================ */

let _mmAreasSig = null;

function _makeMmAreaCard(area, overridePrompt) {
  const card = document.createElement("div");
  card.className = "mmAreaCard";
  card.dataset.mmAreaId = area.id;

  const header = document.createElement("div");
  header.className = "mmAreaHeader";

  const swatch = document.createElement("span");
  swatch.className = "mmAreaSwatch";
  const hue = areaColorById(area.id);
  swatch.style.background = "rgb(" + hue.r + "," + hue.g + "," + hue.b + ")";
  header.appendChild(swatch);

  const name = document.createElement("span");
  name.className = "mmAreaName";
  name.textContent = area.name || area.id;
  header.appendChild(name);

  const dims = document.createElement("span");
  dims.className = "mmAreaDims";
  const m = area.mask || {};
  dims.textContent = (m.width || 0) + "\u00d7" + (m.height || 0);
  header.appendChild(dims);

  card.appendChild(header);

  const ta = document.createElement("textarea");
  ta.className = "mmAreaPrompt";
  ta.rows = 2;
  ta.spellcheck = false;
  ta.placeholder = "prompt for this area";
  ta.value = overridePrompt !== undefined
    ? overridePrompt
    : (area.prompt || "");
  ta.addEventListener("input", () => {
    const tid = board.selectedTaskId;
    if (!tid) return;
    const map = board.areaPromptOverrides[tid] || {};
    map[area.id] = ta.value;
    board.areaPromptOverrides[tid] = map;
  });
  card.appendChild(ta);

  return card;
}

function _syncMultimaskAreas(item) {
  const e = _get();
  if (!e.multimaskSection) return;

  const mm = item && item.multimask;
  if (!mm || !mm.state || !Array.isArray(mm.state.areas)) {
    e.multimaskSection.style.display = "none";
    _mmAreasSig = null;
    return;
  }
  e.multimaskSection.style.display = "";

  const areas = mm.state.areas;
  const tid = board.selectedTaskId;
  const overrides = (tid && board.areaPromptOverrides[tid]) || {};

  // Info line
  const url = mm.url || "?";
  const nMasks = Object.keys(mm.masks || {}).length;
  const infoText =
    "source: " + url + "\n"
    + areas.length + " area(s), " + nMasks + " mask(s) drawn";
  if (e.multimaskInfo && e.multimaskInfo.textContent !== infoText) {
    e.multimaskInfo.textContent = infoText;
    e.multimaskInfo.className = "";
  }

  // Task id in the signature: the mask PNGs are drawn fresh every
  // frame (board.selectedItem.multimask.masks), so the mask visuals
  // are not cached in DOM — but the prompt textareas are, and the
  // "what to display per card" defaults come from per-task state
  // (board.areaPromptOverrides[tid] and this snapshot's
  // area.prompt).  The else branch below *does* correctly overwrite
  // the textarea on a task switch with matching ids, so this is
  // defensive rather than a fix for a live bug — included so that a
  // task switch always rebuilds, matching _syncControlnetsSection's
  // behavior and preventing any future DOM state that would survive
  // a task switch unnoticed.  See the module docstring,
  // CONTROLNETS THUMBNAIL STALENESS — FIXED.
  const idsSig = (board.selectedTaskId || "") + "||"
               + areas.map(a => a.id).join("|");
  if (idsSig !== _mmAreasSig) {
    _mmAreasSig = idsSig;
    e.multimaskList.innerHTML = "";
    if (areas.length === 0) {
      const empty = document.createElement("div");
      empty.className = "empty";
      empty.textContent = "no areas in multimask";
      e.multimaskList.appendChild(empty);
    } else {
      for (const area of areas) {
        e.multimaskList.appendChild(
          _makeMmAreaCard(area, overrides[area.id]),
        );
      }
    }
  } else {
    // Same area set — just sync prompt values in place, skipping
    // focused textareas so user typing is never stomped.
    for (const area of areas) {
      const card = e.multimaskList.querySelector(
        '[data-mm-area-id="' + area.id + '"]'
      );
      if (!card) continue;
      const ta = card.querySelector(".mmAreaPrompt");
      if (!ta || document.activeElement === ta) continue;
      const want = overrides[area.id] !== undefined
        ? overrides[area.id]
        : (area.prompt || "");
      if (ta.value !== want) ta.value = want;
    }
  }

  // Toggle visibility
  if (e.showMultimaskLabel) {
    e.showMultimaskLabel.style.display = "flex";
  }
  if (e.showMultimaskCb) {
    e.showMultimaskCb.checked = !!board.showMultimask;
  }
}

/* ============================================================
   Controlnets list (display-only)
   ============================================================ */

let _cnSig = null;

function _makeCnRow(entry, images) {
  const row = document.createElement("div");
  row.className = "cnRow";

  const imgMeta = entry.image || {};
  const hash = imgMeta.hash;
  const b64 = hash ? images[hash] : null;

  if (b64) {
    const thumb = document.createElement("img");
    thumb.className = "cnThumb";
    thumb.src = "data:image/png;base64," + b64;
    thumb.alt = entry.name || entry.id || "controlnet";
    row.appendChild(thumb);
  } else {
    // The transform was missing for this entry (controlnets
    // unreachable at snapshot time, or that entry had no image when
    // the daemon fetched).  Show a placeholder so the row is still
    // legible.
    const placeholder = document.createElement("div");
    placeholder.className = "cnThumb cnThumbEmpty";
    placeholder.textContent = "?";
    placeholder.title = "image not captured at intercept";
    row.appendChild(placeholder);
  }

  const info = document.createElement("div");
  info.className = "cnInfo";

  const name = document.createElement("span");
  name.className = "cnName";
  name.textContent = entry.name || entry.id || "(unnamed)";
  name.title = name.textContent;
  info.appendChild(name);

  const dims = document.createElement("span");
  dims.className = "cnDims";
  dims.textContent = (imgMeta.width || 0) + "\u00d7" + (imgMeta.height || 0);
  info.appendChild(dims);

  row.appendChild(info);
  return row;
}

function _syncControlnetsSection(item) {
  const e = _get();
  if (!e.controlnetsSection) return;

  const cn = item && item.controlnets;
  if (!cn || !cn.state || !Array.isArray(cn.state.controlnets)) {
    e.controlnetsSection.style.display = "none";
    _cnSig = null;
    return;
  }
  e.controlnetsSection.style.display = "";

  const entries       = cn.state.controlnets;
  const images        = cn.images || {};
  const applyEnabled  = cn.state.apply_enabled || {};
  const nApplyOn      = Object.values(applyEnabled)
                          .filter(v => v !== false).length;
  const nApplyTotal   = Object.keys(applyEnabled).length;
  const nImgs         = Object.keys(images).length;

  // Info line.  "N/M apply node(s) enabled" is honest about overall
  // state without per-entry correlation (which the snapshot cannot
  // provide — apply_enabled is keyed by ControlNetApplyAdvanced
  // titles, controlnets is keyed by preprocessor titles, and the
  // mapping between them lives in the workflow file only).  See
  // the module docstring, CONTROLNETS LIST.
  let infoText = "source: " + (cn.url || "?") + "\n"
               + entries.length + " preprocessor image(s), "
               + nImgs + " drawn";
  if (nApplyTotal > 0) {
    infoText += "\n" + nApplyOn + "/" + nApplyTotal
              + " apply node(s) enabled";
  }
  if (e.controlnetsInfo && e.controlnetsInfo.textContent !== infoText) {
    e.controlnetsInfo.textContent = infoText;
  }

  // Task id in the signature: raw image hashes do NOT change when
  // inpaint's crop frame rotates — only the transformed b64 behind
  // /controlnet_image does (see controlnets_server.py's CROP-FRAME
  // TRANSFORM).  Two intercepted tasks that share raw hashes but
  // were captured at different crop frames therefore produce the
  // same `entrySig` without the task id, and the panel would keep
  // showing the previous task's thumbnails (their `<img>` `src`
  // holds the OLD transformed b64; nothing forces a rebuild).  The
  // task id forces a rebuild on every task switch — a new
  // intercepted run creates a new batch_id, so the signature always
  // changes when the user clicks a new queued task; re-selecting
  // the currently-selected task does not (its snapshot is frozen at
  // intake).  See the module docstring,
  // CONTROLNETS THUMBNAIL STALENESS — FIXED.
  const entrySig = (board.selectedTaskId || "") + "||"
                 + entries.map(c => (c.image || {}).hash || "").join("|")
                 + "||" + nApplyOn + "/" + nApplyTotal
                 + "||" + nImgs;
  if (entrySig === _cnSig) return;
  _cnSig = entrySig;

  e.controlnetsList.innerHTML = "";
  if (entries.length === 0) {
    const empty = document.createElement("div");
    empty.className = "empty";
    empty.textContent =
      "no preprocessed images yet \u2014 click Preprocess in the "
      + "controlnets playground";
    e.controlnetsList.appendChild(empty);
    return;
  }

  for (const entry of entries) {
    e.controlnetsList.appendChild(_makeCnRow(entry, images));
  }
}

export function syncPanelFields() {
  const e = _get();
  const summary = board.tasks[board.selectedTaskId];
  const item    = board.selectedItem;

  _syncTaskList();
  _syncTaskInfo(item);
  _syncTaskPayload(item);
  _syncResult(item);
  _syncMultimaskAreas(item);
  _syncControlnetsSection(item);

  // Hide the multimask overlay toggle when there is no snapshot.
  if (!(item && item.multimask)) {
    if (e.showMultimaskLabel) e.showMultimaskLabel.style.display = "none";
  }

  // The prompt textarea is NOT written here — see the module
  // docstring.  It is set on task selection (in _selectTask) and
  // updated by the textarea's own input handler.

  // Button gating reads through effectiveStatus: a task this client
  // has fired Run / Discard for reports "running" even if the last
  // poll still shows "queued", so the buttons disable on click and
  // stay disabled across the click → server-flip window.  See
  // pg_state.py's "PENDING LOCAL RUNS".
  //
  // `expired` tasks (daemon swept them) land here as non-"queued",
  // so the buttons disable — the correct state: there is nothing to
  // run or discard; the user must re-intercept from the docker.
  const eff = effectiveStatus(summary);
  const canRun     = eff === "queued";
  const canDiscard = eff === "queued";
  if (e.runBtn)     e.runBtn.disabled     = !canRun;
  if (e.discardBtn) e.discardBtn.disabled = !canDiscard;
}
"""