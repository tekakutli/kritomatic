"""Panel field wiring: the context-rectangle checkbox, the inpaint
prompt textarea, the view-rotation scrub input, the interceptor
routing checkbox and URL field, and the field registrations that
expose these as pgkit fields.

Very little code here compared to a full-featured playground: this
playground drives exactly one operation (inpaint), and its inputs are
the mask stack (from other sources) and the prompt.

VIEW ROTATION — SCRUB INPUT
===========================
The rotation control is one text field that behaves as both a
numeric input (click → focus → type an exact angle) and a scrub
control (press and drag sideways to change the value continuously).
This is the standard `_installScrubInput` pattern used by cable,
cone, boxes, and text_wall; the input carries the `scrubInput` CSS
class from pgkit's SHELL_CSS.

  · rate 0.5 deg/px    100 px drag = 50°.
  · snapStep 15°       Shift-drag lands on 15° increments, the same
                       snap convention as boxes' card rotation and
                       cable's mirror-angle scrub.
  · wraparound         values outside [0, 360) wrap (`-30` ≡ `330`);
                       `_applyRotation` does the wrapping.
  · click-to-type      `_installScrubInput`'s mouseup calls
                       `el.focus(); el.select()` when the drag never
                       crossed its 3-px movement threshold, so a
                       plain click selects the current value and the
                       user can type over it directly.  Enter and
                       blur both commit; Escape reverts.

DRAG ROUNDS TO WHOLE DEGREES; TYPED INPUT KEEPS DECIMALS
========================================================
The user explicitly asked for no decimals during a scrub drag, but
full decimal precision when typing.  `_installScrubInput` passes
the active drag state as the second argument to `write()` during a
drag (mousemove) and passes nothing on Enter/blur commits.  The
`write` handler below branches on that:

    drag argument present     isDrag = true   → round to integer
                              (both in the model and on the display)
    drag argument absent      isDrag = false  → keep the exact value
                              (decimals allowed; the only path that
                              produces a fractional rotation)

Because the model is rounded too during drag, the widget and the
stored value never diverge: a drag that ends at "53" leaves the
model at 53, not 52.6.  A subsequent drag starting from a typed
fractional value (e.g. 47.5) has its first tick round that value
into the integer grid — a drag always produces integers.

`_syncRotationWidget()` mirrors the current `view.rotation` into the
input; it is called by the `view.rotation` field's `set` handler so
a remote patch keeps the local widget in sync.  It rounds to one
decimal place (so a remote client can still patch a fractional
value and see it displayed), and it skips the write when
`document.activeElement === rotationEl`, so it never stomps a value
the user is currently typing.

`status.running` is a read-only boolean registered at the bottom,
same convention as sam3_segment: scripting clients (CLI,
`remote_panel`) can wait on this name to sequence async actions.

INTERCEPTOR ROUTING
===================
A checkbox ("Route via interceptor") and a URL text input live in the
Inpainting section.  When the checkbox is checked and the user clicks
Inpaint, the payload is forwarded to the URL (default: the companion
interceptor playground at port 8775) instead of running locally.  See
`pg_comfy_client.py`'s `runInpaintViaInterceptor` for the flow.

The URL field is hidden while the checkbox is off, via a `display`
toggle driven from `_syncInterceptUrlVisibility`.  The field values
themselves (`intercept.enabled`, `intercept.url`) are always
registered, so a remote client can flip the checkbox whether or not
the local DOM input happens to be visible.
"""

PANEL_SCRUBS_JS = r"""
import { board, view, _applyRotation } from "./pg_state.js";
import { ensureDefaultContext } from "./pg_inpaint.js";
import { _installScrubInput } from "./pgkit/panel_core.js";
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
   Interceptor routing
   ============================================================ */

const interceptEnabledEl = document.getElementById("interceptEnabled");
const interceptUrlEl     = document.getElementById("interceptUrl");
const interceptUrlRow    = document.getElementById("interceptUrlRow");

function _syncInterceptUrlVisibility() {
  if (!interceptUrlRow) return;
  interceptUrlRow.style.display =
    board.interceptEnabled ? "flex" : "none";
}

if (interceptEnabledEl) {
  interceptEnabledEl.checked = !!board.interceptEnabled;
  interceptEnabledEl.addEventListener("change", () => {
    board.interceptEnabled = !!interceptEnabledEl.checked;
    _syncInterceptUrlVisibility();
    pgkitNotifyLocal("intercept.enabled");
    import("./pg_panel_fields.js")
      .then(m => m.syncPanelFields())
      .catch(() => {});
  });
  _syncInterceptUrlVisibility();
}

if (interceptUrlEl) {
  interceptUrlEl.value = board.interceptUrl
    || "http://127.0.0.1:8775/intercept/intake";
  interceptUrlEl.addEventListener("input", () => {
    board.interceptUrl = interceptUrlEl.value;
    pgkitNotifyLocal("intercept.url");
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

pgkitRegisterField("intercept.enabled", {
  get: () => !!board.interceptEnabled,
  set: (v) => {
    board.interceptEnabled = !!v;
    const el = document.getElementById("interceptEnabled");
    if (el && document.activeElement !== el) {
      el.checked = board.interceptEnabled;
    }
    _syncInterceptUrlVisibility();
    import("./pg_panel_fields.js")
      .then(m => m.syncPanelFields())
      .catch(() => {});
  },
  meta: { kind: "bool", label: "Route via interceptor" },
});

pgkitRegisterField("intercept.url", {
  get: () => board.interceptUrl || "",
  set: (v) => {
    board.interceptUrl = String(v || "");
    const el = document.getElementById("interceptUrl");
    if (el && document.activeElement !== el) {
      el.value = board.interceptUrl;
    }
  },
  meta: { kind: "text", label: "Interceptor URL" },
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
   A single scrub input: click to focus and type an exact angle,
   drag horizontally to scrub.  Shift-drag snaps to 15°, matching
   the 15° snap used by boxes' card rotation and cable's
   mirror-angle scrub.

   The `_installScrubInput` installer's mousedown does
   `e.preventDefault()`, so a plain click does not focus the field
   directly; the installer's own mouseup handler calls
   `el.focus(); el.select()` when the pointer never moved beyond
   its 3-px threshold, which is what gives us the click-to-type
   behaviour.  Enter and blur commit; Escape reverts (all three
   handled by the installer).

   The installer passes the drag state as the second argument to
   `write()` during a scrub, and passes nothing on Enter/blur
   commits.  We use that to round to whole degrees while dragging
   while preserving typed decimals on commit — see the module
   docstring for the full reasoning.

   `_syncRotationWidget` is called from the field's `set` handler
   (below) so a remote patch refreshes the local widget.  It skips
   the write when `document.activeElement === rotationEl`, so it
   never stomps a value the user is typing. */

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

      // `drag` is the installer's own drag-state object, passed as
      // the second argument during a mousemove scrub.  Enter and
      // blur commits call `write(raw)` with no second argument.
      //
      // During drag: round to whole degrees — no decimals on the
      // display, and no decimals in the model either, so the two
      // never diverge.  A subsequent drag that starts from a typed
      // fractional value (e.g. 47.5) has its first tick round into
      // the integer grid, which is the intended behaviour: a drag
      // always produces integers.
      //
      // On commit: keep the value exactly as typed, including
      // decimals.  This is the only path that produces a fractional
      // rotation.
      const isDrag = !!drag;
      const value = isDrag ? Math.round(v) : v;

      let wrapped = value % 360;
      if (wrapped < 0) wrapped += 360;
      view.rotation = wrapped;

      // Refresh the visible value on every scrub tick.  During a
      // drag we show the integer; on a typed commit the field is
      // (or is about to be) unfocused, so we leave its value alone
      // — the user's typed text is what stays visible until the
      // next state-driven sync.
      if (document.activeElement !== rotationEl) {
        const displayed = isDrag
          ? Math.round(wrapped)
          : Math.round(wrapped * 10) / 10;
        rotationEl.value = String(displayed);
      }

      pgkitNotifyLocal("view.rotation");
      // The scrub installer calls `_drawFn()` right after this
      // `write()` returns; no explicit draw() needed.
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