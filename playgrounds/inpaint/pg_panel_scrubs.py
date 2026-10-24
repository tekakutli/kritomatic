"""Panel field wiring: the inpaint enable checkbox, the inpaint prompt
textarea, and the field registrations that expose both as pgkit
fields.

Very little code here compared to a full-featured playground: this
playground drives exactly one operation (inpaint), and its inputs are
the mask stack (from other sources) and the prompt.

`status.running` is a read-only boolean registered at the bottom,
same convention as sam3_segment: scripting clients (CLI,
`remote_panel`) can wait on this name to sequence async actions."""

PANEL_SCRUBS_JS = r"""
import { board } from "./pg_state.js";
import { ensureDefaultContext } from "./pg_inpaint.js";
import {
  pgkitRegisterField, pgkitNotifyLocal,
} from "./pgkit/remote_client.js";

/* ============================================================
   Inpainting section
   ============================================================ */


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
  meta: { kind: "bool", label: "Inpainting enabled" },
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
