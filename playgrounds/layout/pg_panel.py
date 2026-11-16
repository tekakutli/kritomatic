"""Panel: bbox list, buttons, ratio dropdown, state sync, Generate wiring.

Text bboxes carry a per-bbox `text` field — the string the user wants
rendered inside that bubble.  This is browser-side only: the
DrawatoonSetIP node ignores it (the ip branch encodes only position),
and it is not sent to `/state`.  At Generate time, `pg_generate.js`
sends it to the server along with the global prompt, and the server
folds it into the CLIP text.

Character bboxes carry a `magiLabel` field — the name of the magi
character whose embedding should be paired with this position box.
Layout's server resolves labels to 768-d vectors at read time.  Empty
label = no pairing (falls back to the learned generic character
embedding, same as if magi were unreachable).

Global prompt and seed:
   · `#promptText` — the base description fed into CLIP.
   · `#seedInput`  — an optional fixed seed; blank = random.

Both are read at click time only; not part of `/state`.

The Generate button is wired via `installGenerateButton()` from
`pg_generate.js`.  Multiple clicks queue multiple concurrent
generations; the button is never disabled due to a generation in
flight.
"""
PANEL_JS = r"""
 import {
   board, setImage, applyCanvasImage, clearImage,
   addBbox, deleteBbox, clearAllBboxes, seedDefault, selectBbox,
   setBboxText, setBboxMagiLabel, resetViewerToFit,
 } from "./pg_state.js";
 import {
   uploadImageFile, importFromKrita, checkKritaStatus,
   fetchActiveState, fetchResourceImage,
 } from "./pg_client.js";
 import { fetchSelectedLayer } from "./pg_autosync.js";
 import { flashStatus } from "./pgkit/panel_core.js";
 import { pgkitNotifyAll } from "./pgkit/remote_client.js";
 import {
   installGenerateButton, refreshGenerateButton,
 } from "./pg_generate.js";

 const PLAYGROUND_ID = "layout";
 const SLOT_ID       = "@thelayout";

 const RATIO_KEYS = [
   "0.25", "0.26", "0.27", "0.28",
   "0.32", "0.33", "0.35", "0.40",
   "0.42", "0.48", "0.50", "0.52",
   "0.57", "0.60", "0.68", "0.72",
   "0.78", "0.82", "0.88", "0.94",
   "1.00", "1.07", "1.13", "1.21",
   "1.29", "1.38", "1.46", "1.67",
   "1.75", "2.00", "2.09", "2.40",
   "2.50", "2.89", "3.00", "3.11",
   "3.62", "3.75", "3.88", "4.00",
 ];

 /* ============================================================
    State sync
    ============================================================ */

 let _syncTimer = null;
 let _lastStateSnapshot = "";

 function _stateSnapshot() {
   // Strip `text`/`magiLabel`-adjacent irrelevant fields for the
   // snapshot used only to decide "did anything change worth POSTing".
   // `magiLabel` IS included — pairing changes need a sync.
   const stripText = (list) => list.map(b => ({
     x: b.x, y: b.y, w: b.w, h: b.h,
   }));
   return JSON.stringify({
     t: stripText(board.textBboxes),
     c: stripText(board.characterBboxes),
     cl: board.characterBboxes.map(b => b.magiLabel || ""),
     d: [board.imageWidth, board.imageHeight],
     v: board.viewer,
   });
 }

 function _buildPayload() {
   const toWire = (list) => list.map(b => [
     b.x, b.y, b.x + b.w, b.y + b.h,
   ]);
   return {
     op: "set",
     state: {
       text_bboxes:      toWire(board.textBboxes),
       character_bboxes: toWire(board.characterBboxes),
       character_labels: board.characterBboxes.map(b => b.magiLabel || ""),
       image_dims:       [board.imageWidth, board.imageHeight],
       viewer: {
         x: board.viewer.x,
         y: board.viewer.y,
         w: board.viewer.w,
         h: board.viewer.h,
         ratio: board.viewer.ratio,
       },
     },
   };
 }

 function _scheduleStateSync() {
   if (_syncTimer) clearTimeout(_syncTimer);
   _syncTimer = setTimeout(_doStateSync, 250);
 }

 function _applyMagiFields(data) {
   if (data && Array.isArray(data.magi_character_labels)) {
     const labels = data.magi_character_labels;
     const reachable = !!data.magi_reachable;
     const changed =
       JSON.stringify(labels) !== JSON.stringify(board.magiLabels)
       || reachable !== board.magiReachable;
     board.magiLabels = labels;
     board.magiReachable = reachable;
     if (Array.isArray(data.reference_embeddings)) {
       board.referenceEmbeddings = data.reference_embeddings;
     }
     return changed;
   }
   return false;
 }

 async function _doStateSync() {
   _syncTimer = null;
   const snap = _stateSnapshot();
   if (snap === _lastStateSnapshot) return;
   try {
     const res = await fetch("/state", {
       method: "POST",
       headers: { "Content-Type": "application/json" },
       body: JSON.stringify(_buildPayload()),
     });
     const data = await res.json();
     if (data.success) {
       _lastStateSnapshot = snap;
       const v = (data.version || "").slice(0, 8);
       const el = document.getElementById("stateVersion");
       if (el) el.textContent = v || "—";
       const sl = document.getElementById("statusLine");
       if (sl) {
         sl.textContent = "synced \u00b7 v" + v
           + (board.magiReachable ? "" : " \u00b7 magi down");
         sl.className = "ok";
       }
       if (_applyMagiFields(data)) _renderBboxList();
     } else {
       const sl = document.getElementById("statusLine");
       if (sl) {
         sl.textContent = "sync failed: " + (data.message || "unknown");
         sl.className = "bad";
       }
     }
   } catch (e) {
     const sl = document.getElementById("statusLine");
     if (sl) {
       sl.textContent = "sync failed: " + e.message;
       sl.className = "bad";
     }
   }
   pgkitNotifyAll();
 }

 window._layoutOnDragEnd = _scheduleStateSync;

 /* ============================================================
    Bbox list
    ============================================================ */

 function _renderBboxList() {
   const list = document.getElementById("bboxList");
   if (!list) return;

   const ae = document.activeElement;
   if (ae && list.contains(ae) && ae.tagName === "INPUT") {
     return;
   }

   list.innerHTML = "";

   if (board.textBboxes.length === 0 && board.characterBboxes.length === 0) {
     const e = document.createElement("div");
     e.className = "empty";
     e.textContent = board.imageEl
       ? "no bboxes yet \u2014 click + Text region or + Character"
       : "load an image first";
     list.appendChild(e);
     return;
   }

   const _row = (kind, idx, bbox) => {
     const isSel = board.selectedKind === kind && board.selectedIdx === idx;
     const row = document.createElement("div");
     row.className = "bboxRow" + (isSel ? " selected" : "");
     if (isSel) {
       row.style.borderColor = kind === 'text'
         ? "rgba(120,220,255,0.85)"
         : "rgba(255,200,90,0.85)";
     }

     const sw = document.createElement("span");
     sw.className = "bboxSwatch";
     sw.style.background = kind === 'text'
       ? "rgb(120,220,255)"
       : "rgb(255,200,90)";
     row.appendChild(sw);

     const lbl = document.createElement("span");
     lbl.className = "bboxLabel";
     lbl.textContent = (kind === 'text' ? "Text " : "Char ") + (idx + 1);
     row.appendChild(lbl);

     if (kind === 'text') {
       const ti = document.createElement("input");
       ti.type = "text";
       ti.className = "bboxTextInput";
       ti.placeholder = "bubble text";
       ti.value = bbox.text || "";
       ti.addEventListener("input", () => {
         setBboxText(idx, ti.value);
       });
       ti.addEventListener("mousedown", (e) => e.stopPropagation());
       ti.addEventListener("click", (e) => e.stopPropagation());
       row.appendChild(ti);
     }

     if (kind === 'character') {
       const sel = document.createElement("select");
       sel.className = "bboxMagiSel";

       const blank = document.createElement("option");
       blank.value = "";
       blank.textContent = board.magiReachable
         ? "\u2014 pair with magi \u2014"
         : "\u2014 magi unreachable \u2014";
       sel.appendChild(blank);

       const known = board.magiLabels || [];
       for (const magiLbl of known) {
         const opt = document.createElement("option");
         opt.value = magiLbl;
         opt.textContent = magiLbl;
         sel.appendChild(opt);
       }
       // If the stored label is not in the current magi list, show
       // it as a disabled "(missing)" option so the user can see
       // what used to be paired before magi dropped it.
       const stored = bbox.magiLabel || "";
       if (stored && !known.includes(stored)) {
         const opt = document.createElement("option");
         opt.value = stored;
         opt.textContent = stored + " (missing)";
         opt.disabled = true;
         sel.appendChild(opt);
       }
       sel.value = stored;
       sel.disabled = !board.magiReachable || known.length === 0;
       sel.title = board.magiReachable
         ? "Pair this position with a magi character's embedding."
         : "magi playground is not reachable.";
       sel.addEventListener("change", () => {
         setBboxMagiLabel(idx, sel.value);
         _scheduleStateSync();
       });
       sel.addEventListener("click", (e) => e.stopPropagation());
       sel.addEventListener("mousedown", (e) => e.stopPropagation());
       row.appendChild(sel);
     }

     const dims = document.createElement("span");
     dims.className = "bboxDims";
     const pctW = Math.round(bbox.w * 100);
     const pctH = Math.round(bbox.h * 100);
     dims.textContent = pctW + "% \u00d7 " + pctH + "%";
     row.appendChild(dims);

     // Embedding indicator: for character rows, show whether the
     // server resolved a real embedding for this position.
     if (kind === 'character') {
       const hasEmb = (board.referenceEmbeddings[idx] !== null
                       && board.referenceEmbeddings[idx] !== undefined);
       const emb = document.createElement("span");
       emb.className = "bboxEmbed " + (hasEmb ? "yes" : "no");
       emb.textContent = hasEmb ? "768d" : "\u2014";
       row.appendChild(emb);
     }

     const del = document.createElement("button");
     del.className = "bboxDel";
     del.textContent = "\u00d7";
     del.title = "Remove bbox";
     del.addEventListener("click", (ev) => {
       ev.stopPropagation();
       deleteBbox(kind, idx);
       _renderBboxList();
       _scheduleStateSync();
     });
     row.appendChild(del);

     row.addEventListener("click", () => {
       selectBbox(kind, idx);
       _renderBboxList();
     });

     list.appendChild(row);
   };

   for (let i = 0; i < board.textBboxes.length; i++) {
     _row('text', i, board.textBboxes[i]);
   }
   for (let i = 0; i < board.characterBboxes.length; i++) {
     _row('character', i, board.characterBboxes[i]);
   }
 }

 /* ============================================================
    Panel sync
    ============================================================ */

 export function syncPanelFields() {
   const e = {
     imageInfo:      document.getElementById("imageInfo"),
     addText:        document.getElementById("addTextBboxBtn"),
     addChar:        document.getElementById("addCharacterBboxBtn"),
     seed:           document.getElementById("seedDefaultBtn"),
     clear:          document.getElementById("clearBboxesBtn"),
     viewerRatio:    document.getElementById("viewerRatio"),
   };

   if (e.imageInfo) {
     const text = board.imageEl
       ? board.imageWidth + " \u00d7 " + board.imageHeight
         + "   [" + String(board.imageHash).slice(0, 8) + "\u2026]"
       : "No image loaded";
     if (e.imageInfo.textContent !== text) e.imageInfo.textContent = text;
   }

   if (e.viewerRatio && document.activeElement !== e.viewerRatio) {
     if (e.viewerRatio.value !== board.viewer.ratio) {
       e.viewerRatio.value = board.viewer.ratio;
     }
   }

   const hasImage = !!board.imageEl;
   if (e.addText) e.addText.disabled = !hasImage;
   if (e.addChar) e.addChar.disabled = !hasImage;
   if (e.seed)    e.seed.disabled    = !hasImage;
   if (e.clear)   e.clear.disabled   =
     !(board.textBboxes.length + board.characterBboxes.length);

   refreshGenerateButton();
 }

 /* ============================================================
    Buttons
    ============================================================ */

 function _pickImageAction() {
   const input = document.createElement("input");
   input.type = "file";
   input.accept = "image/*";
   input.addEventListener("change", async () => {
     const file = input.files && input.files[0];
     if (!file) return;
     flashStatus("uploading\u2026", "");
     try {
       const info = await uploadImageFile(file);
       const dataUrl = await _fileToDataUrl(file);
       setImage(info.hash, dataUrl, info.width, info.height);
       flashStatus("image loaded", "ok");
       _scheduleStateSync();
       _renderBboxList();
     } catch (e) {
       flashStatus("upload failed: " + e.message, "bad");
     }
   });
   input.click();
 }

 function _fileToDataUrl(file) {
   return new Promise((resolve, reject) => {
     const reader = new FileReader();
     reader.onload = () => resolve(String(reader.result || ""));
     reader.onerror = () => reject(new Error("could not read file"));
     reader.readAsDataURL(file);
   });
 }

 function _clearImageAction() {
   clearImage();
   _renderBboxList();
   syncPanelFields();
   _scheduleStateSync();
 }

 async function _importImageFromKritaAction() {
   flashStatus("importing from Krita\u2026", "");
   const res = await importFromKrita({});
   if (!res.success) {
     flashStatus("import failed: " + (res.message || "unknown"), "bad");
     return;
   }
   setImage(res.hash, res.data_url, res.width, res.height);
   flashStatus("imported " + res.width + "\u00d7" + res.height, "ok");
   _renderBboxList();
   _scheduleStateSync();
 }

 async function _refreshCanvasAction() {
   flashStatus("refreshing " + SLOT_ID + "\u2026", "");
   const state = await fetchActiveState();
   if (!state || state.daemon_reachable === false) {
     flashStatus(SLOT_ID + " refresh failed: daemon unreachable", "bad");
     return;
   }
   const playgrounds = (state.data && state.data.playgrounds) || [];
   const entry = playgrounds.find(p => p.id === PLAYGROUND_ID);
   const slots = (entry && entry.layer_slots) || [];
   const slot = slots.find(s => s.id === SLOT_ID);
   if (!slot) {
     flashStatus(SLOT_ID + " not registered", "warn");
     return;
   }
   const selector = slot.selector
     || { source: "active", layer_name: null };
   await fetchSelectedLayer(selector);
 }

 function _addTextBboxAction() {
   if (!board.imageEl) return;
   const n = board.textBboxes.length;
   addBbox('text', {
     x: 0.10,
     y: Math.min(0.85, 0.02 + n * 0.15),
     w: 0.80,
     h: 0.10,
     text: "",
   });
   selectBbox('text', board.textBboxes.length - 1);
   _renderBboxList();
   _scheduleStateSync();
 }

 function _addCharacterBboxAction() {
   if (!board.imageEl) return;
   const n = board.characterBboxes.length;
   addBbox('character', {
     x: Math.max(0.05, 0.30 - n * 0.05),
     y: 0.15,
     w: 0.40,
     h: 0.75,
     magiLabel: "",
   });
   selectBbox('character', board.characterBboxes.length - 1);
   _renderBboxList();
   _scheduleStateSync();
 }

 function _seedDefaultAction() {
   if (!board.imageEl) return;
   seedDefault();
   _renderBboxList();
   _scheduleStateSync();
 }

 function _clearAllAction() {
   clearAllBboxes();
   _renderBboxList();
   _scheduleStateSync();
 }

 async function _kritaStatusAction() {
   const res = await checkKritaStatus();
   const el = document.getElementById("kritaStatus");
   if (!el) return;
   if (res.reachable) {
     el.textContent = "\u2713 reachable";
     el.className = "ok";
   } else {
     el.textContent = "\u2717 not reachable";
     el.className = "bad";
   }
 }

 /* ============================================================
    Magi labels poll — piggyback on layout's /state
    ============================================================
    Every 3s, ask layout's server for the current state.  If the
    returned version differs from the last one we saw, refresh
    `board.magiLabels` + `board.referenceEmbeddings` from the
    response.  This picks up magi changes (label edits, adds,
    deletes) even when the user is not editing layout — so the
    dropdowns always reflect what magi currently offers. */

 let _magiPollTimer = null;
 let _lastSeenVersion = null;

 async function _pollMagiState() {
   try {
     const res = await fetch("/state", {
       method: "POST",
       headers: { "Content-Type": "application/json" },
       body: "{}",
     });
     const data = await res.json();
     if (data.success && data.version !== _lastSeenVersion) {
       _lastSeenVersion = data.version;
       const changed = _applyMagiFields(data);
       const el = document.getElementById("stateVersion");
       if (el) el.textContent = (data.version || "—").slice(0, 8);
       if (changed) _renderBboxList();
     }
   } catch (_) {}
   _scheduleMagiPoll();
 }

 function _scheduleMagiPoll() {
   if (_magiPollTimer) clearTimeout(_magiPollTimer);
   _magiPollTimer = setTimeout(_pollMagiState, 3000);
 }

 /* ============================================================
    Install
    ============================================================ */

 function _showDropOverlay(on) {
   const el = document.getElementById("dropOverlay");
   if (el) el.classList.toggle("visible", on);
 }

 function _installDropOverlay() {
   if (document.getElementById("dropOverlay")) return;
   const el = document.createElement("div");
   el.id = "dropOverlay";
   el.textContent = "Drop an image";
   document.body.appendChild(el);
 }

 function _installViewerDropdowns() {
   const ratioSel = document.getElementById("viewerRatio");
   if (!ratioSel) return;
   if (ratioSel.options.length === 0) {
     for (const k of RATIO_KEYS) {
       const opt = document.createElement("option");
       opt.value = k;
       opt.textContent = k;
       ratioSel.appendChild(opt);
     }
   }
   ratioSel.value = board.viewer.ratio;
   ratioSel.addEventListener("change", () => {
     board.viewer.ratio = ratioSel.value;
     resetViewerToFit();
     _scheduleStateSync();
     _renderBboxList();
   });
 }

 export function installPanelBindings() {
   const ids = {
     pickImage:  "pickImageBtn",
     clearImage: "clearImageBtn",
     importImg:  "importImageBtn",
     refresh:    "refreshCanvasBtn",
     addText:    "addTextBboxBtn",
     addChar:    "addCharacterBboxBtn",
     seed:       "seedDefaultBtn",
     clearAll:   "clearBboxesBtn",
     kritaCheck: "kritaCheckBtn",
   };
   const el = {};
   for (const k in ids) el[k] = document.getElementById(ids[k]);

   if (el.pickImage)   el.pickImage.addEventListener("click", _pickImageAction);
   if (el.clearImage)  el.clearImage.addEventListener("click", _clearImageAction);
   if (el.importImg)   el.importImg.addEventListener("click", _importImageFromKritaAction);
   if (el.refresh)     el.refresh.addEventListener("click", _refreshCanvasAction);
   if (el.addText)     el.addText.addEventListener("click", _addTextBboxAction);
   if (el.addChar)     el.addChar.addEventListener("click", _addCharacterBboxAction);
   if (el.seed)        el.seed.addEventListener("click", _seedDefaultAction);
   if (el.clearAll)    el.clearAll.addEventListener("click", _clearAllAction);
   if (el.kritaCheck)  el.kritaCheck.addEventListener("click", _kritaStatusAction);

   _installViewerDropdowns();
   installGenerateButton();

   _installDropOverlay();
   let _dragDepth = 0;
   window.addEventListener("dragenter", () => {
     _dragDepth++;
     _showDropOverlay(true);
   });
   window.addEventListener("dragleave", () => {
     _dragDepth--;
     if (_dragDepth <= 0) _showDropOverlay(false);
   });
   window.addEventListener("dragover", (e) => e.preventDefault());
   window.addEventListener("drop", async (e) => {
     e.preventDefault();
     _dragDepth = 0;
     _showDropOverlay(false);
     const file = e.dataTransfer && e.dataTransfer.files
                  && e.dataTransfer.files[0];
     if (file && file.type.startsWith("image/")) {
       flashStatus("uploading\u2026", "");
       try {
         const info = await uploadImageFile(file);
         const dataUrl = await _fileToDataUrl(file);
         setImage(info.hash, dataUrl, info.width, info.height);
         _renderBboxList();
         _scheduleStateSync();
       } catch (e) {
         flashStatus("upload failed: " + e.message, "bad");
       }
     }
   });

   _bootPull().catch(() => {});
 }

 async function _bootPull() {
   try {
     const res = await fetch("/state", {
       method: "POST",
       headers: { "Content-Type": "application/json" },
       body: "{}",
     });
     const data = await res.json();
     if (data.success) {
       _lastSeenVersion = data.version;
       _applyMagiFields(data);
       const el = document.getElementById("stateVersion");
       if (el) el.textContent = (data.version || "—").slice(0, 8);
       _lastStateSnapshot = _stateSnapshot();
     }
   } catch (_) {}
   _renderBboxList();
   syncPanelFields();
   _scheduleMagiPoll();
 }

 export function layoutBootSync() {
   _renderBboxList();
   syncPanelFields();
   _kritaStatusAction();
 }

 export function onDrawComplete() {
   _renderBboxList();
   syncPanelFields();
 }
 """