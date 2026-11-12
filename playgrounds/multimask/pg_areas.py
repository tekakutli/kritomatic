"""The area list: creation, mutation, selection."""

AREAS_JS = r"""
import { board, _scheduleDraw } from "./pg_state.js";

function _makeId() {
  return Math.random().toString(36).slice(2, 10);
}

function _makeMaskEntry(b64) {
  const img = new Image();
  const entry = {
    b64, img, w: 0, h: 0, loaded: false,
    _pixels: null, _tinted: null, _tintedKey: null,
  };
  img.onload = () => {
    entry.w = img.width;
    entry.h = img.height;
    entry.loaded = true;
    _scheduleDraw();
  };
  img.onerror = () => _scheduleDraw();
  img.src = "data:image/png;base64," + b64;
  return entry;
}

function _nextAreaName() {
  const used = new Set(board.areas.map(a => a.name));
  while (used.has("area_" + board.nextAreaNum)) board.nextAreaNum++;
  const name = "area_" + board.nextAreaNum;
  board.nextAreaNum++;
  return name;
}

export function areaById(id) {
  for (const a of board.areas) if (a.id === id) return a;
  return null;
}

export function addAreaFromB64(b64, opts) {
  opts = opts || {};
  const id = opts.id || _makeId();
  const name = opts.name || _nextAreaName();
  const area = {
    id, name,
    prompt:    opts.prompt || "",
    useTagger: !!opts.useTagger,
    mask:      _makeMaskEntry(b64),
    // null = inherit from board.globals
    strength:              opts.strength              ?? null,
    set_cond_area:         opts.set_cond_area         ?? null,
    mask_channel:          opts.mask_channel          ?? null,
    bbox:                  opts.bbox                  ?? null,
    resize:                opts.resize                ?? null,
    tagger_mode:           opts.tagger_mode           ?? null,
    tagger_model:          opts.tagger_model          ?? null,
    tagger_threshold:      opts.tagger_threshold      ?? null,
    tagger_char_threshold: opts.tagger_char_threshold ?? null,
  };
  board.areas.push(area);
  _scheduleDraw();
  return id;
}

export function removeArea(id) {
  const idx = board.areas.findIndex(a => a.id === id);
  if (idx < 0) return false;
  board.areas.splice(idx, 1);
  if (board.selectedAreaId === id) board.selectedAreaId = null;
  if (board.hoveredAreaId  === id) board.hoveredAreaId  = null;
  _scheduleDraw();
  return true;
}

export function updateArea(id, patch) {
  const a = areaById(id);
  if (!a) return false;
  for (const k in patch) {
    if (k in a) a[k] = patch[k];
  }
  _scheduleDraw();
  return true;
}

export function selectArea(id) {
  board.selectedAreaId = id;
  _scheduleDraw();
}

export function hoverArea(id) {
  if (board.hoveredAreaId !== id) {
    board.hoveredAreaId = id;
    _scheduleDraw();
  }
}
"""