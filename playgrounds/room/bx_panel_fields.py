"""
bx_panel_fields.py — field syncs and the per-frame panel push.

This module owns three functions called from `draw()` once per frame:

    _syncSelectionInputs   writes the selected box's W / H / Rot
                           into the BOX inspector fields
    _boxSyncInspector      shows / hides the BOX section based on
                           whether a box is selected
    updateTitleBlock       writes the title block values (project,
                           sheet, boxes, scale, selection)

Every one of these writes is equality-guarded so a stable board
costs zero DOM writes.  This is the Stage 1 fix — in the old code
these functions unconditionally reassigned `textContent` and
`style.display` on every frame.

`selectedItem()` (in bx_core) is the shared read: the panel
inspector and the draw functions both consult it.  No inspection
here reaches past that accessor; if the definition of "selected"
ever changes, it changes in bx_core, not in five places.

DOM-write guards
================
The pattern is uniform: read the current DOM value, compare with the
target, only assign when different.  `document.activeElement` is
checked before writing to an input so the caret stays with the user
when they are mid-edit.  The caching values on the module — none of
which are typed — are avoided in favour of re-reading the DOM,
because the panel owns no state the DOM does not also hold; two
sources of truth is the failure mode to avoid.
"""

PANEL_FIELDS_JS = r"""
import { GEOMETRY } from "./bx_geometry.js";
import {
  board, view, selectedItem, boxIsInvalid,
} from "./bx_core.js";
import { T } from "./bx_i18n.js";

/* ==========================================================================
   SELECTION INPUTS
   ==========================================================================
   Drives the three W / H / Rot fields in the BOX inspector and the
   read-only x/y info line.  Called from draw() once per frame.
   Every write is equality-guarded. */

export function _syncSelectionInputs() {
  const nameInput   = document.getElementById("boxName");
  const selWInput   = document.getElementById("selW");
  const selHInput   = document.getElementById("selH");
  const selRotInput = document.getElementById("selRot");
  const selInfo     = document.getElementById("selInfo");
  if (!nameInput) return;

  const b = selectedItem();
  if (b) {
    nameInput.disabled = false;
    if (document.activeElement !== nameInput &&
        nameInput.value !== (b.name || "")) {
      nameInput.value = b.name || "";
    }
    selWInput.disabled = false;
    selHInput.disabled = false;
    selRotInput.disabled = false;

    const wTarget = String(Math.round(b.w));
    const hTarget = String(Math.round(b.h));
    const rTarget = String(Math.round(b.rot * 100) / 100);

    if (document.activeElement !== selWInput && selWInput.value !== wTarget) {
      selWInput.value = wTarget;
    }
    if (document.activeElement !== selHInput && selHInput.value !== hTarget) {
      selHInput.value = hTarget;
    }
    if (document.activeElement !== selRotInput && selRotInput.value !== rTarget) {
      selRotInput.value = rTarget;
    }

    const infoTarget = T("boxInfo")(b.x.toFixed(0), b.y.toFixed(0));
    if (selInfo && selInfo.textContent !== infoTarget) {
      selInfo.textContent = infoTarget;
    }
  } else {
    if (!nameInput.disabled) nameInput.disabled = true;
    if (nameInput.value !== "") nameInput.value = "";
    selWInput.disabled = true;
    selHInput.disabled = true;
    selRotInput.disabled = true;
    if (selWInput.value !== "")   selWInput.value   = "";
    if (selHInput.value !== "")   selHInput.value   = "";
    if (selRotInput.value !== "") selRotInput.value = "";

    const none = T("boxSelNone");
    if (selInfo && selInfo.textContent !== none) selInfo.textContent = none;
  }
}

/* ==========================================================================
   CONDITIONAL BOX INSPECTOR
   ==========================================================================
   `#boxSection` starts display:none in the CSS; this function sets
   it to display:block while a box is selected.  Called from draw()
   once per frame.  Only writes when the current value differs. */

export function _boxSyncInspector() {
  const section = document.getElementById("boxSection");
  const idxEl   = document.getElementById("boxIndex");
  if (!section || !idxEl) return;

  const b = selectedItem();
  if (!b) {
    if (section.style.display !== "none") section.style.display = "none";
    return;
  }
  if (section.style.display !== "block") section.style.display = "block";

  const idx = board.selectedIdx + 1;
  const total = board.items.length;
  const inv = boxIsInvalid(b);
  const status = inv ? T("boxInvalid") : T("boxValid");
  const target = T("boxIdx")(idx, total, status);
  if (idxEl.textContent !== target) idxEl.textContent = target;
}

/* ==========================================================================
   TITLE BLOCK
   ========================================================================== */

const _TB_REFS = {
  project: null, sheet: null, boxes: null, scale: null, selection: null,
};

export function updateTitleBlock() {
  if (!_TB_REFS.project) {
    _TB_REFS.project   = document.getElementById("tb_project");
    _TB_REFS.sheet     = document.getElementById("tb_sheet");
    _TB_REFS.boxes     = document.getElementById("tb_boxes");
    _TB_REFS.scale     = document.getElementById("tb_scale");
    _TB_REFS.selection = document.getElementById("tb_selection");
    if (!_TB_REFS.project) return;
  }

  const sheetTarget = T("tbSheetVal")(GEOMETRY.cutZ);
  if (_TB_REFS.sheet.textContent !== sheetTarget) {
    _TB_REFS.sheet.textContent = sheetTarget;
  }

  const validN = board.items.filter(b => !boxIsInvalid(b)).length;
  const totalN = board.items.length;
  const boxesTarget = T("tbBoxesVal")(validN, totalN);
  if (_TB_REFS.boxes.textContent !== boxesTarget) {
    _TB_REFS.boxes.textContent = boxesTarget;
  }

  const scaleN = Math.max(1, Math.round(1 / Math.max(view.scale, 1e-6)));
  const scaleTarget = T("tbScaleVal")(scaleN);
  if (_TB_REFS.scale.textContent !== scaleTarget) {
    _TB_REFS.scale.textContent = scaleTarget;
  }

  let selTarget = T("tbSelNone");
  const b = selectedItem();
  if (b) {
    const inv = boxIsInvalid(b);
    const status = inv ? T("boxInvalid") : T("boxValid");
    const name = b.name || T("boxUnnamed");
    selTarget = T("tbSelBox")(name, status);
  }
  if (_TB_REFS.selection.textContent !== selTarget) {
    _TB_REFS.selection.textContent = selTarget;
  }
}
"""
