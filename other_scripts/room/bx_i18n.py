"""
bx_i18n.py — the boxes playground's single translation table.

One JavaScript object and one function:

    const LANG = "en"                 ← the language switch
    const TRANSLATIONS = { en, es }   ← every string the playground shows
    function T(key)                   ← the accessor

Every user-visible string in the boxes playground — panel labels,
tooltips, section headers, title-block field names and values, the
instructions popover, the live status line, the flash-status messages,
the invalid chip, the ghost's collision pill — lives in TRANSLATIONS.
Nothing in any other module should contain a hardcoded English (or
Spanish, or anything else) literal that a user reads.

Adding a language
-----------------
    1.  Copy the `en` table to a new key, say `fr`.
    2.  Translate the values in place.  Keys stay in English — they are
        identifiers, not prose, and they must stay stable so a partial
        translation keeps working.
    3.  Any key the new table is missing falls back to `en`, so a
        partial translation renders — untranslated strings appear in
        English alongside the translated ones.

Switching the active language
-----------------------------
    Change LANG near the top of this file.  That is the only switch.
    There is no runtime picker; the design intent is that a fork
    pins its language and ships.

Keys with arguments
-------------------
A key whose value varies with a number or a state word is a FUNCTION
of its arguments, not a string with `${}` placeholders.  This keeps
pluralisation and number-formatting decisions inside the language
table where they belong, instead of scattering ternaries through the
renderer.  `T("tbSheetVal")(cutZ)` reads as "the sheet-value function,
called with cutZ".

The one exception is `popInstructionsHTML` — the popover's body.  It
is one long HTML string because the popover is prose-heavy and
interleaving twenty keys with twenty `+` operators would be worse
than one well-formed block.  Translators translate it as one piece.

Concatenation order
-------------------
This module's I18N_JS is prepended to LIVE_JS in boxes_live.py, so it
is the first thing the playground's <script> tag defines after the
GEOMETRY const.  Every other module can therefore call T(key) at
parse time (inside a const initialiser) as well as at call time.
"""


I18N_JS = r"""
/* ==========================================================================
   TRANSLATIONS
   ==========================================================================

   LANG is the single switch.  Set it to "en" or "es" (or any key you
   add).  Missing keys fall back to English, so a partially-translated
   table keeps rendering.

   Every key whose value varies with a number or a state is a FUNCTION
   of that argument.  Every key with no arguments is a plain string.
   See the module docstring for why. */

const LANG = "es";

const TRANSLATIONS = {
  en: {
    /* ---- app shell ---------------------------------------------- */
    pageTitle:     "Box placement playground",
    appTitleShort: "Box planner",

    /* ---- section labels ---------------------------------------- */
    secGuides: "Guides",
    secDraw:   "Draw",
    secBox:    "Box",
    secEdit:   "Edit",
    secOutput: "Output",
    secStatus: "Status",

    /* ---- buttons ----------------------------------------------- */
    btnRotL90:   "− 90°",
    btnRotR90:   "+ 90°",
    btnRotL15:   "− 15°",
    btnRotR15:   "+ 15°",

    btnDrawBox:    "Draw box",
    btnCancelDraw: "Cancel draw",

    btnSave:      "Save",
    btnUndo:      "Undo",
    btnClearAll:  "Clear all",

    btnPrintPng:  "Print PNG",
    btnListBoxes: "List boxes",

    btnDeleteBox: "Delete box",

    btnHelp:      "?",
    btnClose:     "Close",
    btnShowPanel: "☰ Show panel",
    btnHidePanel: "−",

    /* ---- tooltips ---------------------------------------------- */
    tipRotL90: "Rotate the selected (or hovered) box by −90°",
    tipRotR90: "Rotate the selected (or hovered) box by +90°",
    tipRotL15: "Rotate by −15°",
    tipRotR15: "Rotate by +15°",

    tipDrawBox:   "Arm the drawing tool — click to place a preset box, "
                + "drag to draw an axis-aligned box (Esc to disarm)",
    tipSave:      "Write room_boxes.json (Ctrl/Cmd+S)",
    tipUndo:      "Remove the last placed box",
    tipClearAll:  "Delete every box",
    tipPrintPng:  "Save a black-and-white room_boxes.png",
    tipListBoxes: "Print the box table to the terminal",

    tipHelp:          "Show instructions (?)",
    tipCollapsePanel: "Collapse panel (H)",
    tipShowPanel:     "Show panel (H)",

    /* ---- field labels (panel inspectors) ------------------------ */
    lblName: "Name",
    lblW:    "W",
    lblH:    "H",
    lblRot:  "Rot",

    /* ---- box inspector ----------------------------------------- */
    boxSelNone:   "no box selected",
    boxInfo:      (x, y) => "x=" + x + " y=" + y,
    boxIdx:       (idx, total, status) =>
      "Box " + idx + " / " + total + "  ·  " + status,
    boxValid:     "valid",
    boxInvalid:   "invalid",
    boxUnnamed:   "(unnamed)",

    /* ---- title block ------------------------------------------- */
    tbProject:   "Project",
    tbSheet:     "Sheet",
    tbBoxes:     "Boxes",
    tbScale:     "Scale",
    tbSelection: "Selection",

    tbProjectVal: "room_walls.json",
    tbSheetVal:   (z) => "plan @ " + Math.round(z) + " mm",
    tbBoxesVal:   (validN, totalN) =>
      totalN === 0 ? "0" : validN + " valid / " + totalN,
    tbScaleVal:   (n) => "1 : " + n,
    tbSelNone:    "—",
    tbSelBox:     (name, status) => name + " · " + status,

    /* ---- popover ----------------------------------------------- */
    popTitle: "Instructions",
    popInstructionsHTML:
      "<b>Draw box</b> arms the drawing tool (Esc disarms it).  While " +
      "armed: click → place a preset box; drag → draw an axis-aligned " +
      "box, one corner at the down point.  Clicking a handle on an " +
      "existing box exits the mode and starts the matching drag.  The " +
      "W / H / Rot fields and the rotation buttons appear only while " +
      "this mode is armed; they configure what a click places.<br>" +
      "<b>Left drag</b> a box body → move it (slides along walls).<br>" +
      "<b>Corner handle</b> (large square) → resize both width and " +
      "height at once; the diagonally opposite corner stays put.<br>" +
      "<b>Edge handle</b> (small square) → resize one side; the " +
      "opposite edge stays put.<br>" +
      "<b>Rotation handle</b> (↻ above the box) → rotate about the " +
      "box's own centre.  <b>Shift</b> snaps to 15°.<br>" +
      "<b>Double-click</b> a box → focus its name field.<br>" +
      "<b>Right-click</b> a box → delete.  <b>Del</b> → delete " +
      "selected.<br>" +
      "<b>Scroll</b> zoom · <b>middle-drag</b> pan · <b>Esc</b> " +
      "deselect · <b>0</b> fit.<br>" +
      "<b>Ctrl/Cmd+S</b> saves.  Nothing is written until you press " +
      "Save.<br>" +
      "A <b>grey dashed</b> box is invalid (inside a wall); drag it out " +
      "to rescue it.  Orange boxes overlap; that is allowed and only " +
      "flagged.<br>" +
      "All dimensions in <b>millimetres</b>.",

    /* ---- flash-status messages (transient) --------------------- */
    msgConfirmClear:    "Delete all boxes?",
    msgSavedOk:         (file) => "✓ Saved " + file,
    msgDownloadedOk:    (file) => "✓ Downloaded " + file,
    msgPrintTerminal:   "✓ Printed to terminal",
    msgPrintConsole:    "✓ Printed to browser console (F12)",
    msgLoaded:          (n, source, bad) =>
      "Loaded " + n + " box" + (n === 1 ? "" : "es") + " from " + source +
      (bad ? " — " + bad + " invalid (grey dashed)" : ""),
    msgWallBlocked:     "✗ Change blocked by wall",
    msgRotationBlocked: "✗ Rotation blocked by wall",
    msgPlacedOk:        "✓ Placed (click Save to keep)",
    msgPlaceCollision:  "✗ Collision with wall — cannot place here",
    msgPNGFailed:       "✗ PNG export failed",
    msgPNGWritten:      (w, h) => "✓ PNG written (" + w + "×" + h + ")",

    /* ---- live status line (persistent) ------------------------- */
    stPanningSpace:    "panning (Space)",
    stVerbRotating:    "rotating",
    stVerbResizingBoth:"resizing both",
    stVerbResizing:    "resizing",
    stHandleDrag: (verb, x, y, w, h, rot) =>
      verb + " · x=" + x + " y=" + y + " w=" + w + " h=" + h +
      " rot=" + rot + "°",
    stDragging: (inv, x, y, rot) =>
      (inv ? "dragging (invalid)" : "dragging") +
      " · x=" + x + " y=" + y + " rot=" + rot + "°",
    stDrawing: (inv, w, h) =>
      (inv ? "drawing (invalid)" : "drawing") + " · w=" + w + " h=" + h,
    stSpaceHeld:  "Space held · drag to pan",
    stDrawPreview: (x, y, w, h, rot, bad) =>
      "draw · x=" + x + " y=" + y + " w=" + w + " h=" + h +
      " rot=" + rot + "° · " + (bad ? "COLLISION" : "OK"),
    stHover: (inv, name, x, y, rot) =>
      (inv ? "hover (invalid)" : "hover") + " · " + name +
      " · x=" + x + " y=" + y + " rot=" + rot + "°",
    stCursor: (x, y) => "x=" + x + " y=" + y,

    /* ---- on-canvas labels -------------------------------------- */
    chipInvalid:   "⚠ invalid",
    ghostCollision:"✗ collision",
  },

  es: {
    /* ---- app shell ---------------------------------------------- */
    pageTitle:     "Planificador de cajas",
    appTitleShort: "Planificador de cajas",

    /* ---- section labels ---------------------------------------- */
    secGuides: "Guías",
    secDraw:   "Dibujar",
    secBox:    "Caja",
    secEdit:   "Editar",
    secOutput: "Salida",
    secStatus: "Estado",

    /* ---- buttons ----------------------------------------------- */
    btnRotL90:   "− 90°",
    btnRotR90:   "+ 90°",
    btnRotL15:   "− 15°",
    btnRotR15:   "+ 15°",

    btnDrawBox:    "Dibujar caja",
    btnCancelDraw: "Cancelar dibujo",

    btnSave:      "Guardar",
    btnUndo:      "Deshacer",
    btnClearAll:  "Borrar todo",

    btnPrintPng:  "Imprimir PNG",
    btnListBoxes: "Listar cajas",

    btnDeleteBox: "Eliminar caja",

    btnHelp:      "?",
    btnClose:     "Cerrar",
    btnShowPanel: "☰ Mostrar panel",
    btnHidePanel: "−",

    /* ---- tooltips ---------------------------------------------- */
    tipRotL90: "Girar la caja seleccionada (o bajo el cursor) −90°",
    tipRotR90: "Girar la caja seleccionada (o bajo el cursor) +90°",
    tipRotL15: "Girar −15°",
    tipRotR15: "Girar +15°",

    tipDrawBox:   "Arma la herramienta de dibujo — clic para colocar una "
                + "caja predefinida, arrastrar para dibujar una caja "
                + "alineada con los ejes (Esc para desarmar)",
    tipSave:      "Guardar en room_boxes.json (Ctrl/Cmd+S)",
    tipUndo:      "Quitar la última caja colocada",
    tipClearAll:  "Borrar todas las cajas",
    tipPrintPng:  "Guardar un room_boxes.png en blanco y negro",
    tipListBoxes: "Imprimir la tabla de cajas en la terminal",

    tipHelp:          "Ver instrucciones (?)",
    tipCollapsePanel: "Contraer panel (H)",
    tipShowPanel:     "Mostrar panel (H)",

    /* ---- field labels ------------------------------------------ */
    lblName: "Nombre",
    lblW:    "An",
    lblH:    "Al",
    lblRot:  "Giro",

    /* ---- box inspector ----------------------------------------- */
    boxSelNone:   "sin caja seleccionada",
    boxInfo:      (x, y) => "x=" + x + " y=" + y,
    boxIdx:       (idx, total, status) =>
      "Caja " + idx + " / " + total + "  ·  " + status,
    boxValid:     "válida",
    boxInvalid:   "inválida",
    boxUnnamed:   "(sin nombre)",

    /* ---- title block ------------------------------------------- */
    tbProject:   "Proyecto",
    tbSheet:     "Lámina",
    tbBoxes:     "Cajas",
    tbScale:     "Escala",
    tbSelection: "Selección",

    tbProjectVal: "room_walls.json",
    tbSheetVal:   (z) => "planta @ " + Math.round(z) + " mm",
    tbBoxesVal:   (validN, totalN) =>
      totalN === 0 ? "0" : validN + " válidas / " + totalN,
    tbScaleVal:   (n) => "1 : " + n,
    tbSelNone:    "—",
    tbSelBox:     (name, status) => name + " · " + status,

    /* ---- popover ----------------------------------------------- */
    popTitle: "Instrucciones",
    popInstructionsHTML:
      "<b>Dibujar caja</b> arma la herramienta de dibujo (Esc la " +
      "desarma).  Mientras está armada: clic → colocar una caja " +
      "predefinida; arrastrar → dibujar una caja alineada con los ejes, " +
      "una esquina en el punto inicial.  Hacer clic en un tirador de " +
      "una caja existente sale del modo y comienza el arrastre " +
      "correspondiente.  Los campos An / Al / Giro y los botones de " +
      "rotación aparecen solo mientras este modo está armado; " +
      "configuran lo que un clic colocará.<br>" +
      "<b>Arrastrar</b> el cuerpo de una caja → moverla (se desliza a " +
      "lo largo de los muros).<br>" +
      "<b>Tirador de esquina</b> (cuadrado grande) → redimensionar " +
      "ancho y alto a la vez; la esquina diagonalmente opuesta queda " +
      "fija.<br>" +
      "<b>Tirador de borde</b> (cuadrado pequeño) → redimensionar un " +
      "lado; el borde opuesto queda fijo.<br>" +
      "<b>Tirador de rotación</b> (↻ sobre la caja) → girar alrededor " +
      "de su propio centro.  <b>Shift</b> ajusta a 15°.<br>" +
      "<b>Doble clic</b> en una caja → enfocar su campo de nombre.<br>" +
      "<b>Clic derecho</b> en una caja → eliminar.  <b>Del</b> → " +
      "eliminar la seleccionada.<br>" +
      "<b>Rueda</b> zoom · <b>arrastrar con botón central</b> " +
      "desplazar · <b>Esc</b> deseleccionar · <b>0</b> ajustar.<br>" +
      "<b>Ctrl/Cmd+S</b> guarda.  Nada se escribe hasta que pulse " +
      "Guardar.<br>" +
      "Una caja con <b>línea discontinua gris</b> es inválida (dentro " +
      "de un muro); arrástrela fuera para rescatarla.  Las cajas " +
      "naranjas se solapan; está permitido y solo se señala.<br>" +
      "Todas las dimensiones en <b>milímetros</b>.",

    /* ---- flash-status messages --------------------------------- */
    msgConfirmClear:    "¿Eliminar todas las cajas?",
    msgSavedOk:         (file) => "✓ Guardado " + file,
    msgDownloadedOk:    (file) => "✓ Descargado " + file,
    msgPrintTerminal:   "✓ Impreso en la terminal",
    msgPrintConsole:    "✓ Impreso en la consola del navegador (F12)",
    msgLoaded:          (n, source, bad) =>
      "Cargadas " + n + " caja" + (n === 1 ? "" : "s") + " desde " +
      source + (bad ? " — " + bad + " inválida" + (bad === 1 ? "" : "s") +
                     " (línea discontinua gris)" : ""),
    msgWallBlocked:     "✗ Cambio bloqueado por un muro",
    msgRotationBlocked: "✗ Rotación bloqueada por un muro",
    msgPlacedOk:        "✓ Colocada (pulse Guardar para conservar)",
    msgPlaceCollision:  "✗ Colisión con un muro — no se puede colocar aquí",
    msgPNGFailed:       "✗ Falló la exportación PNG",
    msgPNGWritten:      (w, h) => "✓ PNG escrito (" + w + "×" + h + ")",

    /* ---- live status line -------------------------------------- */
    stPanningSpace:    "desplazando (Espacio)",
    stVerbRotating:    "girando",
    stVerbResizingBoth:"redimensionando ambos",
    stVerbResizing:    "redimensionando",
    stHandleDrag: (verb, x, y, w, h, rot) =>
      verb + " · x=" + x + " y=" + y + " w=" + w + " h=" + h +
      " rot=" + rot + "°",
    stDragging: (inv, x, y, rot) =>
      (inv ? "arrastrando (inválida)" : "arrastrando") +
      " · x=" + x + " y=" + y + " rot=" + rot + "°",
    stDrawing: (inv, w, h) =>
      (inv ? "dibujando (inválida)" : "dibujando") +
      " · w=" + w + " h=" + h,
    stSpaceHeld:  "Espacio pulsado · arrastrar para desplazar",
    stDrawPreview: (x, y, w, h, rot, bad) =>
      "dibujando · x=" + x + " y=" + y + " w=" + w + " h=" + h +
      " rot=" + rot + "° · " + (bad ? "COLISIÓN" : "OK"),
    stHover: (inv, name, x, y, rot) =>
      (inv ? "sobre (inválida)" : "sobre") + " · " + name +
      " · x=" + x + " y=" + y + " rot=" + rot + "°",
    stCursor: (x, y) => "x=" + x + " y=" + y,

    /* ---- on-canvas labels -------------------------------------- */
    chipInvalid:   "⚠ inválida",
    ghostCollision:"✗ colisión",
  },
};

/* Active table with English fallback for any key the active language
   has not defined yet.  Keeps partial translations working. */
const _T_ACTIVE = TRANSLATIONS[LANG] || TRANSLATIONS.en;

function T(key) {
  const v = _T_ACTIVE[key];
  return (v !== undefined) ? v : TRANSLATIONS.en[key];
}
"""
