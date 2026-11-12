"""The area list: one card per area, with inline prompt textarea and
use_tagger checkbox.  Rebuilds only when the set of area ids changes;
otherwise updates existing cards in place, skipping the focused
input so the user's typing is never stomped.

State-change callbacks are routed through a small hook the bindings
module installs, to avoid a circular import."""

PANEL_LISTS_JS = r"""
import { board, areaColorById } from "./pg_state.js";
import {
  areaById, updateArea, removeArea, selectArea,
} from "./pg_areas.js";
import { pgkitNotifyEntities } from "./pgkit/remote_client.js";

// Focus tracking
let _focusedAreaId = null;
let _focusedField  = null;

// Debounced state-sync hook, installed by pg_panel_bindings
let _stateChangeHook = null;
export function setStateChangeHook(fn) { _stateChangeHook = fn; }
function _onChange() {
  if (_stateChangeHook) _stateChangeHook();
  pgkitNotifyEntities();
}

export function syncAreaList() {
  const list = document.getElementById("areaList");
  if (!list) return;

  if (_focusedAreaId) _updateInPlace(list);
  else                _rebuild(list);

  pgkitNotifyEntities();
}

function _rebuild(list) {
  list.innerHTML = "";

  if (board.areas.length === 0) {
    const e = document.createElement("div");
    e.className = "empty";
    e.textContent = board.imageHash
      ? "no areas — click + From Krita"
      : "load an image first, then + From Krita";
    list.appendChild(e);
    return;
  }

  for (const area of board.areas) {
    list.appendChild(_makeCard(area));
  }
}

function _updateInPlace(list) {
  const idsNow = board.areas.map(a => a.id);

  // Remove cards for areas that no longer exist.
  const existing = {};
  for (const el of list.children) {
    if (el.classList && el.classList.contains("areaCard")) {
      existing[el.dataset.id] = el;
    }
  }
  for (const id in existing) {
    if (!idsNow.includes(id)) existing[id].remove();
  }

  // Update in place, append new at the end.  No reordering: the array
  // only ever appends or removes, so DOM order matches array order
  // without touching existing nodes.
  for (const area of board.areas) {
    const card = existing[area.id];
    if (card) {
      _updateCard(card, area);
    } else {
      list.appendChild(_makeCard(area));
    }
  }
}

function _updateCard(card, area) {
  const nameEl   = card.querySelector(".areaName");
  const promptEl = card.querySelector(".areaPrompt");
  const taggerEl = card.querySelector(".areaTagger");
  const dimsEl   = card.querySelector(".areaDims");
  const sw       = card.querySelector(".areaSwatch");

  if (nameEl && document.activeElement !== nameEl) nameEl.value = area.name;
  if (promptEl && document.activeElement !== promptEl) promptEl.value = area.prompt;
  if (taggerEl && document.activeElement !== taggerEl) taggerEl.checked = !!area.useTagger;
  if (dimsEl) dimsEl.textContent = (area.mask.w || 0) + "\u00d7" + (area.mask.h || 0);
  if (sw) {
    const hue = areaColorById(area.id);
    sw.style.background = "rgb(" + hue.r + "," + hue.g + "," + hue.b + ")";
  }
}

function _makeCard(area) {
  const card = document.createElement("div");
  card.className = "areaCard";
  card.dataset.id = area.id;

  // Header row
  const header = document.createElement("div");
  header.className = "areaHeader";

  const swatch = document.createElement("span");
  swatch.className = "areaSwatch";
  const hue = areaColorById(area.id);
  swatch.style.background = "rgb(" + hue.r + "," + hue.g + "," + hue.b + ")";
  header.appendChild(swatch);

  const nameInput = document.createElement("input");
  nameInput.className = "areaName";
  nameInput.type = "text";
  nameInput.value = area.name;
  nameInput.title = "Area name. Used as the label and by the node's area_filter.";
  nameInput.addEventListener("focus", () => {
    _focusedAreaId = area.id; _focusedField = "name";
  });
  nameInput.addEventListener("blur", () => {
    _focusedAreaId = null; _focusedField = null;
  });
  nameInput.addEventListener("input", () => {
    updateArea(area.id, { name: nameInput.value });
    _onChange();
  });
  header.appendChild(nameInput);

  const dims = document.createElement("span");
  dims.className = "areaDims";
  dims.textContent = (area.mask.w || 0) + "\u00d7" + (area.mask.h || 0);
  header.appendChild(dims);

  const del = document.createElement("button");
  del.className = "areaDel";
  del.textContent = "\u00d7";
  del.title = "Remove this area";
  del.addEventListener("click", (e) => {
    e.stopPropagation();
    removeArea(area.id);
    _onChange();
    import("./pg_dispatch.js").then(m => m.draw()).catch(() => {});
  });
  header.appendChild(del);

  card.appendChild(header);

  // Prompt textarea
  const prompt = document.createElement("textarea");
  prompt.className = "areaPrompt";
  prompt.rows = 2;
  prompt.value = area.prompt;
  prompt.placeholder = "prompt for this area";
  prompt.spellcheck = false;
  prompt.addEventListener("focus", () => {
    _focusedAreaId = area.id; _focusedField = "prompt";
  });
  prompt.addEventListener("blur", () => {
    _focusedAreaId = null; _focusedField = null;
  });
  prompt.addEventListener("input", () => {
    updateArea(area.id, { prompt: prompt.value });
    _onChange();
  });
  card.appendChild(prompt);

  // use_tagger row
  const flags = document.createElement("label");
  flags.className = "areaFlags";
  const tagger = document.createElement("input");
  tagger.className = "areaTagger";
  tagger.type = "checkbox";
  tagger.checked = !!area.useTagger;
  tagger.addEventListener("change", () => {
    updateArea(area.id, { useTagger: tagger.checked });
    _onChange();
  });
  flags.appendChild(tagger);
  const taggerLabel = document.createElement("span");
  taggerLabel.textContent = "use_tagger";
  flags.appendChild(taggerLabel);
  card.appendChild(flags);

  // Card click → select the area (unless click was on an input)
  card.addEventListener("click", (e) => {
    if (e.target === nameInput || e.target === prompt
        || e.target === tagger || e.target === del) return;
    selectArea(area.id);
    import("./pg_dispatch.js").then(m => m.draw()).catch(() => {});
  });

  return card;
}
"""
