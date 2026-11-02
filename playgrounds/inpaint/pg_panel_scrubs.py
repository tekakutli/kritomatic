"""Panel field wiring: the context-rectangle checkbox, the inpaint
prompt textarea, and the field registrations that expose both as
pgkit fields.

Very little code here compared to a full-featured playground: this
playground drives exactly one operation (inpaint), and its inputs are
the mask stack (from other sources) and the prompt.

`status.running` is a read-only boolean registered at the bottom,
same convention as sam3_segment: scripting clients (CLI,
`remote_panel`) can wait on this name to sequence async actions."""

PANEL_SCRUBS_JS = r"""
import { board, view, _applyRotation } from "./pg_state.js";
import { ensureDefaultContext } from "./pg_inpaint.js";
import {
  pgkitRegisterField, pgkitNotifyLocal,
} from "./pgkit/remote_client.js";

/* ============================================================
   Inpainting section
   ============================================================ */

const inpaintEnabledEl = document.getElementById("inpaintEnabled");
if (inpaintEnabledEl) {
  inpaintEnabledEl.checked = !!board.inpaintEnabled;
  inpaintEnabledEl.addEventListener("change", () => {
    board.inpaintEnabled = !!inpaintEnabledEl.checked;
    if (board.inpaintEnabled) ensureDefaultContext();
    pgkitNotifyLocal("inpaint.enabled");
    import("./pg_dispatch.js").then(m => m.draw()).catch(() => {});
  });
}

const inpaintPromptEl = document.getElementById("inpaintPrompt");
if (inpaintPromptEl) {
  inpaintPromptEl.value = board.inpaintPrompt || "";
  inpaintPromptEl.addEventListener("input", () => {
    board.inpaintPrompt = inpaintPromptEl.value;
    pgkitNotifyLocal("inpaint.prompt");
    import("./pg_panel_fields.js").then(m => m.syncPanelFields()).catch(() => {});
  });
}

/* ============================================================
   Field registrations
   ============================================================ */

pgkitRegisterField("inpaint.enabled", {
  get: () => !!board.inpaintEnabled,
  set: (v) => {
    board.inpaintEnabled = !!v;
    const el = document.getElementById("inpaintEnabled");
    if (el && document.activeElement !== el) el.checked = board.inpaintEnabled;
    if (board.inpaintEnabled) ensureDefaultContext();
    import("./pg_dispatch.js").then(m => m.draw()).catch(() => {});
  },
  meta: { kind: "bool", label: "Context rectangle" },
});

pgkitRegisterField("inpaint.prompt", {
  get: () => board.inpaintPrompt || "",
  set: (v) => {
    board.inpaintPrompt = String(v || "");
    const el = document.getElementById("inpaintPrompt");
    if (el && document.activeElement !== el) el.value = board.inpaintPrompt;
    import("./pg_panel_fields.js").then(m => m.syncPanelFields()).catch(() => {});
  },
  meta: { kind: "text", label: "Inpaint prompt" },
});

pgkitRegisterField("masks.selectedIndex", {
  get: () => board.selectedMaskIndices.length > 0
    ? board.selectedMaskIndices[0] : -1,
  set: (v) => {
    const n = parseInt(v, 10);
    if (!isFinite(n)) return;
    if (n < 0 || n >= board.masks.length) {
      import("./pg_masks.js").then(m => m.clearMaskSelection()).catch(() => {});
    } else {
      import("./pg_masks.js").then(m => m.replaceMaskSelection(n)).catch(() => {});
    }
    import("./pg_dispatch.js").then(m => m.draw()).catch(() => {});
  },
  meta: { kind: "number", label: "First selected mask" },
});

/* ============================================================
   Status: running
   ============================================================
   Read-only boolean; `board.running` is already toggled by
   `pg_comfy_client.py` around the HTTP round-trip and
   `pgkitNotifyAll()` there already includes this field.

   `visible: false` marks the field API-only: it appears in `schema`
   and `state` so scripting clients can wait on it, but not as a
   user-clickable widget in the panel. */

/* ============================================================
   View rotation
   ============================================================
   Two controls for the same value: a range slider for quick
   dragging and a number input for typing an exact angle.  Both
   stay in sync via `_syncRotationWidget()`; whichever control
   the user is currently focused on is left alone so a value
   that hasn't been re-formatted (e.g. "045" while typing) isn't
   stomped by a redraw. */

const rotationEl    = document.getElementById("viewRotation");
const rotationNumEl = document.getElementById("viewRotationNum");

function _syncRotationWidget() {
  const deg = Math.round(view.rotation || 0);
  if (rotationEl && document.activeElement !== rotationEl) {
    rotationEl.value = String(deg);
  }
  if (rotationNumEl && document.activeElement !== rotationNumEl) {
    rotationNumEl.value = String(deg);
  }
}

function _commitRotation(raw, source) {
  const v = parseFloat(raw);
  if (!isFinite(v)) return;
  // Clamp to [0, 360]; _applyRotation wraps 360 to 0 internally.
  const clamped = Math.max(0, Math.min(360, v));
  _applyRotation(clamped);
  // Update the *other* control immediately so the user sees the
  // clamped value reflected, but leave the source control as-is
  // while it still has focus (the browser manages its contents).
  if (source !== "slider" && rotationEl) {
    rotationEl.value = String(Math.round(clamped));
  }
  if (source !== "number" && rotationNumEl) {
    rotationNumEl.value = String(Math.round(clamped));
  }
  pgkitNotifyLocal("view.rotation");
  import("./pg_dispatch.js").then(m => m.draw()).catch(() => {});
}

if (rotationEl) {
  rotationEl.value = String(Math.round(view.rotation || 0));
  rotationEl.addEventListener("input", () => {
    _commitRotation(rotationEl.value, "slider");
  });
}

if (rotationNumEl) {
  rotationNumEl.value = String(Math.round(view.rotation || 0));
  // `change` fires on blur and on Enter, which is when we want to
  // apply the typed value — firing on every keystroke would apply
  // "4" then "45" as separate rotations.
  rotationNumEl.addEventListener("change", () => {
    _commitRotation(rotationNumEl.value, "number");
    // Normalise the displayed value after commit (e.g. "045" -> "45").
    rotationNumEl.value = String(Math.round(view.rotation || 0));
  });
  // Enter should also blur so `change` fires.
  rotationNumEl.addEventListener("keydown", (e) => {
    if (e.key === "Enter") {
      e.preventDefault();
      rotationNumEl.blur();
    }
  });
}

_syncRotationWidget();

pgkitRegisterField("view.rotation", {
  get: () => view.rotation || 0,
  set: (v) => {
    const n = parseFloat(v);
    if (!isFinite(n)) return;
    _applyRotation(n);
    _syncRotationWidget();
    import("./pg_dispatch.js").then(m => m.draw()).catch(() => {});
  },
  meta: { kind: "number", label: "Rotation (deg)", min: 0, max: 360 },
});
pgkitRegisterField("status.running", {
  get: () => !!board.running,
  set: () => {},
  meta: {
    kind: "bool",
    label: "Running",
    visible: false,
  },
});
"""