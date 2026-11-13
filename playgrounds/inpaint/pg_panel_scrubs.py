"""Panel field wiring: the context-rectangle checkbox, the view-rotation
scrub input, and field registrations.

VIEW ROTATION — SCRUB INPUT
===========================
Same as before: one text field that behaves as both numeric input
(click to focus, type an angle) and scrub control (press and drag).
Drag rounds to whole degrees; typed input keeps decimals.

NEW: every rotation change schedules an *immediate* sync (bypassing the
debounce).  Rotation is a scalar field on the sync payload — the daemon
queries it back via `/rotation` on demand, so keeping it fresh is
cheap.  Immediate means a rotate-then-Run sequence has no measurable
stale window.

CONTEXT RECTANGLE CHECKBOX
==========================
Turning it on schedules a debounced sync (the pushed `thecontext`
changes from "untouched" to "white rect at user rect").  Turning it off
schedules a debounced sync that pushes all-white once — the ON→OFF
transition.  Further syncs while off do not touch `thecontext`; the
daemon reads that layer's own bbox or falls back to the whole canvas."""

PANEL_SCRUBS_JS = r"""
import { board, view, _applyRotation } from "./pg_state.js";
import { ensureDefaultContext } from "./pg_inpaint.js";
import { _installScrubInput } from "./pgkit/panel_core.js";
import { scheduleSync, syncNow } from "./pg_sync.js";
import {
  pgkitRegisterField, pgkitNotifyLocal,
} from "./pgkit/remote_client.js";

/* ============================================================
   Context rectangle checkbox
   ============================================================ */

const inpaintEnabledEl = document.getElementById("inpaintEnabled");
if (inpaintEnabledEl) {
  inpaintEnabledEl.checked = !!board.inpaintEnabled;
  inpaintEnabledEl.addEventListener("change", () => {
    board.inpaintEnabled = !!inpaintEnabledEl.checked;
    if (board.inpaintEnabled) ensureDefaultContext();
    pgkitNotifyLocal("inpaint.enabled");
    scheduleSync();
    import("./pg_dispatch.js").then(m => m.draw()).catch(() => {});
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
    scheduleSync();
    import("./pg_dispatch.js").then(m => m.draw()).catch(() => {});
  },
  meta: { kind: "bool", label: "Context rectangle" },
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
    scheduleSync();
    import("./pg_dispatch.js").then(m => m.draw()).catch(() => {});
  },
  meta: { kind: "number", label: "First selected mask" },
});

/* ============================================================
   View rotation
   ============================================================ */

const rotationEl = document.getElementById("viewRotation");

function _syncRotationWidget() {
  if (!rotationEl) return;
  if (document.activeElement === rotationEl) return;
  const rounded = Math.round((view.rotation || 0) * 10) / 10;
  const target = String(rounded);
  if (rotationEl.value !== target) rotationEl.value = target;
}

if (rotationEl) {
  rotationEl.value = String(Math.round((view.rotation || 0) * 10) / 10);
  _installScrubInput(rotationEl, {
    rate: 0.5,
    snapStep: 15,
    read: () => view.rotation || 0,
    write: (raw, drag) => {
      const v = parseFloat(raw);
      if (!isFinite(v)) return;

      const isDrag = !!drag;
      const value = isDrag ? Math.round(v) : v;

      let wrapped = value % 360;
      if (wrapped < 0) wrapped += 360;
      view.rotation = wrapped;

      if (document.activeElement !== rotationEl) {
        const displayed = isDrag
          ? Math.round(wrapped)
          : Math.round(wrapped * 10) / 10;
        rotationEl.value = String(displayed);
      }

      pgkitNotifyLocal("view.rotation");
      // Rotation → immediate sync.  Debounce would be visible here:
      // rotate-then-Run within 250 ms would race the daemon's
      // /rotation query.
      syncNow();
    },
  });
}

pgkitRegisterField("view.rotation", {
  get: () => view.rotation || 0,
  set: (v) => {
    const n = parseFloat(v);
    if (!isFinite(n)) return;
    _applyRotation(n);
    _syncRotationWidget();
    syncNow();
    import("./pg_dispatch.js").then(m => m.draw()).catch(() => {});
  },
  meta: { kind: "number", label: "Rotation (deg)", min: 0, max: 360 },
});

pgkitRegisterField("status.syncing", {
  get: () => !!board.syncing,
  set: () => {},
  meta: {
    kind: "bool",
    label: "Syncing",
    visible: false,
  },
});
"""