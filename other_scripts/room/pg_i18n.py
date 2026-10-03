"""
pg_i18n.py — the cable playground's single translation table.

One JavaScript object and one function:

    const LANG = "en"                 ← the language switch
    const TRANSLATIONS = { en, es }   ← every string the playground shows
    function T(key)                   ← the accessor

Every user-visible string in the playground — panel labels, tooltips,
status messages, the popover's instructions, the title block's field
names, the ruler's numeric-input chrome, the cable and measure
inspectors, the flash-status confirmations, and the printable export's
captions — lives in TRANSLATIONS.  Nothing in any other module should
contain a hardcoded English (or Spanish, or anything else) literal that
a user reads.

This module replaces the export module's old private table.  pg_export.py
used to carry its own `LANG`, `TRANSLATIONS`, and `T`; those are gone.
The export now calls the same `T` every other module calls, and a fork
that adds a language adds it here once.

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
A key whose value varies with a number is a FUNCTION of that number,
not a string with `${}` placeholders.  This keeps pluralisation and
number-formatting decisions inside the language table where they
belong, instead of scattering ternaries through the renderer.
`T("tbSheetVal")(cutZ)` reads as "the sheet-value function, called
with cutZ".

The one exception is `popInstructionsHTML` — the popover's body.  It
is one long HTML string because the popover is prose-heavy and
interleaving fifty keys with fifty `+` operators would be worse than
one well-formed block.  Translators translate it as one piece.

Concatenation order
-------------------
This module's I18N_JS is prepended to LIVE_JS in cable_live.py, so it
is the first thing the playground's <script> tag defines after the
GEOMETRY const.  Every other live module can therefore call T(key)
at parse time (inside a const initialiser) as well as at call time.
pg_export.py — concatenated after LIVE_JS by cable_html.py —
sees the same T() and reads the same table; it carries no translation
machinery of its own.
"""


I18N_JS = r"""
/* ==========================================================================
   TRANSLATIONS
   ==========================================================================

   LANG is the single switch.  Set it to "en" or "es" (or any key you
   add).  Missing keys fall back to English, so a partially-translated
   table keeps rendering.

   Every key whose value varies with a number is a FUNCTION of that
   number.  Every key with no arguments is a plain string.  See the
   module docstring for why. */

const LANG = "es";

const TRANSLATIONS = {
  en: {
    /* ---- app shell ---------------------------------------------- */
    pageTitle:     "Cable / pipe layout planner",
    appTitleShort: "Cable plan",

    /* ---- section labels ---------------------------------------- */
    secGuides:  "Guides",
    secDraw:    "Draw",
    secMeasure: "Measure",
    secCable:   "Cable",
    secEdit:    "Edit",
    secOutput:  "Output",
    secStatus:  "Status",

    /* ---- snap pill + view reset -------------------------------- */
    snapOff: "snap: off",
    snapOn:  "snap: on",

    /* ---- zoom reset -------------------------------------------- */
    btnZoomReset: "Reset view",
    tipZoomReset: "Reset both views to their fit zoom (Home)",

    /* ---- buttons ----------------------------------------------- */
    btnAddNsGrid:  "+ N–S",
    btnAddEwGrid:  "+ E–W",
    btnAddWallV:   "+ Wall |",
    btnAddWallH:   "+ Wall ─",

    btnSave:         "Save",
    btnDeleteVertex: "Delete vertex",
    btnDeleteCable:  "Delete cable",
    btnClearAll:     "Clear all",

    btnDrawCable:  "Draw cable",
    btnCancelDraw: "Cancel draw",
    btnMeasure:    "Measure",
    btnExitRuler:  "Exit ruler",

    btnDeleteMeasurement: "Delete measurement",
    btnDiagnostics:       "Diagnostics",
    btnExportRuns:        "Export runs",

    btnShowPanel: "☰ Show panel",
    btnHidePanel: "−",
    btnHelp:      "?",
    btnClose:     "Close",

    /* ---- tooltips ---------------------------------------------- */
    tipCollapsePanel: "Collapse panel (H)",
    tipShowPanel:     "Show panel (H)",
    tipHelp:          "Show instructions (?)",

    tipAddNsGrid: "Add a north-south floor grid line",
    tipAddEwGrid: "Add an east-west floor grid line",
    tipAddWallV:  "Add a vertical wall grid line",
    tipAddWallH:  "Add a horizontal wall grid line",

    tipSave:         "Save the current layout to room_layout.json",
    tipDeleteVertex: "Delete the selected vertex",
    tipDeleteCable:  "Delete the selected cable",
    tipClearAll:     "Clear all grids and cables",

    tipDrawCable: "Arm the drawing tool — click first point in the "
                + "floor plan OR wall strip.  Alt+click a vertex to "
                + "connect.  Enter / Esc finishes.",
    tipMeasure:   "Ruler mode — click two points to measure a distance "
                + "(Shift+R)",
    tipDiagnostics: "Dump the arrow-field state to the console and the "
                  + "Python terminal",
    tipExportRuns:  "Export cable runs as printable diagrams",

    /* ---- field labels (panel inspectors) ------------------------ */
    lblSpanCm:   "Span cm",
    lblAngleDeg: "Angle °",

    /* ---- ruler numeric input (transient, canvas) --------------- */
    rlInputSpan:  "span",
    rlInputAngle: "angle",
    rlUnitCm:     "cm",
    rlUnitDeg:    "°",

    /* ---- title block ------------------------------------------- */
    tbProject:   "Project",
    tbSheet:     "Sheet",
    tbCeiling:   "Ceiling",
    tbScale:     "Scale",
    tbSelection: "Selection",

    tbProjectVal: "room.step",
    tbSheetVal:   (z) => "plan @ " + Math.round(z) + " mm",
    tbCeilingVal: (mm) => Math.round(mm) + " mm",
    tbScaleVal:   (n, zoom) =>
      "1 : " + n + (Math.abs(zoom - 1) > 0.02
                    ? "  (" + Math.round(zoom * 100) + " %)" : ""),
    tbSelNone:    "—",
    tbSelCable:   (id, n) =>
      "Cable " + id + " · " + n + " part" + (n === 1 ? "" : "s"),
    tbSelVertex:  (n) => "vertex " + n,
    tbSelDrawing: (n) => "drawing · " + n + " pt",

    /* ---- cable inspector --------------------------------------- */
    ciHeader: (id, n, f, w) =>
      "Cable " + id + "  ·  " + n + " part" + (n === 1 ? "" : "s") +
      "  (" + f + " floor, " + w + " wall)",
    ciVertexNone:     "no vertex selected",
    ciVertexBase:     (n) => "vertex " + n,
    ciVertexStepTop:  "step-top",
    ciVertexStepBot:  "step-bottom",
    ciVertexWallEdge: "wall-edge",
    ciVertexCorner:   "corner",
    ciVertexGrid:     "grid",
    ciVertexFree:     "free",

    /* ---- popover ----------------------------------------------- */
    popTitle: "Instructions",
    popInstructionsHTML:
      "<b>Floor plan</b> (top) — <b>Unfolded wall</b> (bottom).  " +
      "Hold <b>Shift</b> while clicking to enable snapping.<br>" +
      "<b>Draw cable</b>: click once, click again.  If the two clicks " +
      "are on the same wall, a straight segment is drawn.  If they are " +
      "on two different walls, the route planner builds a virtual " +
      "straight line through every physically-connected wall between " +
      "them, splits it at each corner, and files the pieces as one " +
      "cable.  Right-click undoes the last leg.  Enter / Esc finishes.<br>" +
      "<b>Alt+click</b> a vertex to connect to it, adopt its cable, or " +
      "merge the current drawing into it.  A plain click during drawing " +
      "always creates a fresh vertex — your new cable is independent of " +
      "anything you click near, even if the vertex lands on the same " +
      "physical point as an existing one.  (Cross-view connections — " +
      "floor↔wall — still happen automatically: the two views share " +
      "anchors through the wall footprint.)<br>" +
      "<b>Focus mode</b>: the strip narrows to the wall you clicked " +
      "and its physical neighbours.  Visual aid — the route planner " +
      "works regardless.<br>" +
      "<b>Hops</b> (teal arcs): a cable that turns a corner between two " +
      "walls far apart in the strip draws a teal hop arc instead of a " +
      "diagonal.<br>" +
      "<b>Drag</b> a corner anchor: only its height moves; every " +
      "partner at that physical corner tracks it.<br>" +
      "Select a vertex, then <b>Delete</b>; with no vertex selected, " +
      "<b>Delete</b> removes the whole cable.  Press <b>H</b> to " +
      "hide / show this panel.<br>" +
      "<b>Wheel</b>: uniform zoom.  <b>Shift+wheel</b> over the wall " +
      "strip: horizontal-only stretch (spread a packed wall out " +
      "sideways without changing any height).  <b>Home</b>: reset " +
      "both views to their fit zoom.",

    /* ---- flash-status messages (transient) --------------------- */
    msgDrawStart:
      "Drawing — click first point in the floor plan OR wall strip · " +
      "Enter / Esc finishes",
    msgDrawCancelled:    "Draw cancelled",
    msgNeedTwoPoints:    "Draw cancelled (need at least 2 points)",
    msgCableCreated:     (n) => "✓ Cable created (" + n + " points)",
    msgAdopted:          "Adopted existing cable — keep clicking to "
                       + "extend it",
    msgMerged:           (n) => "✓ Merged into existing cable (" + n +
                       " points)",
    msgCableDeleted:     "✓ Cable deleted",
    msgVertexDeleted:    "✓ Vertex deleted",
    msgNoCableSelected:  "No cable selected",
    msgNoVertexSelected: "No vertex selected — click a cable vertex first",
    msgViewReset:        "View reset to fit",
    msgConfirmClear:     "Clear all grids and cables?",
    msgSavedOk:          (file) => "✓ Saved " + file,
    msgSaveFailed:       "✗ Save failed (no server?)",

    /* ---- live status line (persistent) ------------------------- */
    stPanning: (view) => "panning " + view,

    stRulerCount: (n) =>
      "ruler · " + n + " measurement" + (n === 1 ? "" : "s"),
    stRulerStepNew:     "click first point or a pill to edit",
    stRulerStepSecond:  "click second point",
    stRulerStepLocked:  "locked — click to commit, Esc to release",
    stRulerStepEditing: (i) =>
      "editing #" + i + " — click empty canvas to start a new one",
    stRulerAxisAuto:  " · auto EW/NS (Shift+X cycle · Alt free)",
    stRulerAxisFixed: (a) =>
      " · axis " + a.toUpperCase() + " (Shift+X cycle · Alt free)",
    stRulerNumeric:   " · Shift+D span · Shift+A angle",
    stRulerSkipOff:   " · Shift skips steps",
    stRulerSkipOn:    " · skipping steps",
    stRulerExit:      " · Esc / Shift+R to exit",

    stDrawingFirst:
      "drawing · click first point in floor plan OR wall strip" +
      " · Alt+click a vertex to connect/adopt",
    stDrawingN: (view, n) =>
      "drawing on " + view + " · " + n + " point" + (n === 1 ? "" : "s"),
    stDrawingAdopted:  " · adopted a base cable",
    stDrawingFocus:    (tag) => " · FOCUS " + tag,
    stDrawingFinish:   " · Enter / Esc finishes",
    stDrawingSnapOn:   " · snap ON",
    stDrawingSnapHint: " · hold Shift to snap",

    stVertexTagBase:  (n) => "vertex " + n,
    stVertexStepTop:  " · STEP-TOP",
    stVertexStepBot:  " · STEP-BOTTOM",
    stVertexWallEdge: " · WALL-EDGE",
    stVertexCorner:   " · CORNER",
    stVertexGrid:     " · GRID",
    stVertexFree:     " · free",
    stVertexSnapHint: " · hold Shift to snap",

    stGridDrag: (view, t, pos) =>
      "dragging " + view + " grid · " + t + "=" + pos,

    stHoverSeg:  (tag) => "→ " + tag,
    stHoverStep: "  ·  step riser",
    stHoverLen:  (cm) => "  ·  " + cm + " cm",

    stCableGroup: (id, n, f, w) =>
      "Cable " + id + "  ·  " + n + " parts (" + f + " floor, " +
      w + " wall)",

    stFloorPos: (x, y) => "floor  x=" + x + "  y=" + y,
    stWallPos:  (u, v) => "wall   u=" + u + "  v=" + v,
    stOnStep:   "  ·  on step",
    stZoom:     (pct) => "  ·  " + pct + " %",

    /* ---- arrow-field toggle (pg_arrows_diag) ------------------- */
    arrowToggleLabel: "Show arrows",
    arrowToggleTitle: "Show or hide the wall→plan escape arrows",

    /* ---- export (pg_export) ------------------------------------ */
    cableTitle: "Cable",
    subtitle: (anchors, parts, total) =>
      `${anchors} anchor${anchors === 1 ? "" : "s"} . ` +
      `${parts} part${parts === 1 ? "" : "s"} . total ${total}`,
    heightAxis:             "height (cm)",
    lengthBreakdown:        "Length breakdown",
    wallRuns:               "Wall runs",
    floorRuns:              "Floor runs",
    totalRow:               "Total",
    legend:                 "Legend",
    legendCable:            "cable",
    legendWallFloorLink:    "wall<->floor link",
    legendTraversedWall:    "traversed wall",
    legendStepFace:         "step face (forward hatch)",
    legendVoid:             "void (backslash hatch)",
    wallSegmentsArrowStyle: "Wall segments . arrow style",

    previewTitle: "Cable run diagrams",
    previewSubtitle: (n) =>
      `${n} cable${n === 1 ? "" : "s"} - black-and-white print layout`,
    previewHint:
      "Each diagram is rendered at ~2200 px wide. " +
      "For best legibility when printing, place it in your document at " +
      "<b>180 - 210 mm</b> wide (roughly A5 landscape height).",
    downloadAllPngs:      (n) => `Download all PNGs (${n})`,
    downloadAllJson:      (n) => `Download all JSON (${n})`,
    filterCollinear:      "Filter collinear vertices in strip",
    filterCollinearTitle: "Collapse wall-edge anchors that lie on a " +
                          "straight run of the same wall",
    showAngles:           "Show cable angles",
    showAnglesTitle:      "Add the slope angle of every connecting " +
                          "cable segment to its vertex pill",
    downloadPng:          "Download PNG",
    downloadJson:         "Download JSON",
    cardSizeMeta: (w, h, mm) =>
      `${w} x ${h} px  .  ${mm} mm wide at 300 DPI`,
    cardAlt:      (id) => `Cable ${id} run diagram`,
    doneHint:     "Done. If your browser asks to allow multiple " +
                  "downloads, click Allow.",
    downloadedBundle: "Downloaded cable_runs.json",

    popupBlocked:     "Popup blocked - allow popups for this page",
    noCablesToExport: "No cables to export",
    nothingToExport:  (failed) =>
      `Nothing to export - ${failed} cable(s) failed`,
    exportedOk: (n) =>
      `Exported ${n} cable run${n === 1 ? "" : "s"}`,
    exportedWithFailures: (n, failed) =>
      `Exported ${n} cable run${n === 1 ? "" : "s"} - ${failed} failed`,
    exportButton:      "Export cable runs (print)",
    exportButtonTitle: "Render one black-and-white diagram per " +
                       "physical cable",
  },

  es: {
    /* ---- app shell ---------------------------------------------- */
    pageTitle:     "Planificador de cables / tuberías",
    appTitleShort: "Plano de cables",

    /* ---- section labels ---------------------------------------- */
    secGuides:  "Guías",
    secDraw:    "Dibujar",
    secMeasure: "Medir",
    secCable:   "Cable",
    secEdit:    "Editar",
    secOutput:  "Salida",
    secStatus:  "Estado",

    /* ---- snap pill + view reset -------------------------------- */
    snapOff: "snap: off",
    snapOn:  "snap: on",

    /* ---- zoom reset -------------------------------------------- */
    btnZoomReset: "Vista",
    tipZoomReset: "Restablecer ambas vistas a su zoom de ajuste (Inicio)",

    /* ---- buttons ----------------------------------------------- */
    btnAddNsGrid:  "+ N–S",
    btnAddEwGrid:  "+ E–O",
    btnAddWallV:   "+ Muro |",
    btnAddWallH:   "+ Muro ─",

    btnSave:         "Guardar",
    btnDeleteVertex: "Eliminar vértice",
    btnDeleteCable:  "Eliminar cable",
    btnClearAll:     "Borrar todo",

    btnDrawCable:  "Dibujar cable",
    btnCancelDraw: "Cancelar dibujo",
    btnMeasure:    "Medir",
    btnExitRuler:  "Salir de medición",

    btnDeleteMeasurement: "Eliminar medición",
    btnDiagnostics:       "Diagnóstico",
    btnExportRuns:        "Exportar tendidos",

    btnShowPanel: "☰ Mostrar panel",
    btnHidePanel: "−",
    btnHelp:      "?",
    btnClose:     "Cerrar",

    /* ---- tooltips ---------------------------------------------- */
    tipCollapsePanel: "Contraer panel (H)",
    tipShowPanel:     "Mostrar panel (H)",
    tipHelp:          "Ver instrucciones (?)",

    tipAddNsGrid: "Añadir línea de retícula norte-sur",
    tipAddEwGrid: "Añadir línea de retícula este-oeste",
    tipAddWallV:  "Añadir línea de retícula vertical de muro",
    tipAddWallH:  "Añadir línea de retícula horizontal de muro",

    tipSave:         "Guardar el diseño actual en room_layout.json",
    tipDeleteVertex: "Eliminar el vértice seleccionado",
    tipDeleteCable:  "Eliminar el cable seleccionado",
    tipClearAll:     "Borrar todas las retículas y cables",

    tipDrawCable: "Arma la herramienta de dibujo — clic en el primer "
                + "punto del plano o de la tira de muro.  Alt+clic en "
                + "un vértice para conectar.  Enter / Esc finaliza.",
    tipMeasure:   "Modo regla — clic en dos puntos para medir una "
                + "distancia (Shift+R)",
    tipDiagnostics: "Volcar el estado del campo de flechas a la consola "
                  + "y a la terminal de Python",
    tipExportRuns:  "Exportar tendidos de cable como diagramas "
                  + "imprimibles",

    /* ---- field labels ------------------------------------------ */
    lblSpanCm:   "Luz cm",
    lblAngleDeg: "Ángulo °",

    /* ---- ruler numeric input ---------------------------------- */
    rlInputSpan:  "luz",
    rlInputAngle: "ángulo",
    rlUnitCm:     "cm",
    rlUnitDeg:    "°",

    /* ---- title block ------------------------------------------- */
    tbProject:   "Proyecto",
    tbSheet:     "Lámina",
    tbCeiling:   "Techo",
    tbScale:     "Escala",
    tbSelection: "Selección",

    tbProjectVal: "room.step",
    tbSheetVal:   (z) => "planta @ " + Math.round(z) + " mm",
    tbCeilingVal: (mm) => Math.round(mm) + " mm",
    tbScaleVal:   (n, zoom) =>
      "1 : " + n + (Math.abs(zoom - 1) > 0.02
                    ? "  (" + Math.round(zoom * 100) + " %)" : ""),
    tbSelNone:    "—",
    tbSelCable:   (id, n) =>
      "Cable " + id + " · " + n + " parte" + (n === 1 ? "" : "s"),
    tbSelVertex:  (n) => "vértice " + n,
    tbSelDrawing: (n) => "dibujando · " + n + " pt",

    /* ---- cable inspector --------------------------------------- */
    ciHeader: (id, n, f, w) =>
      "Cable " + id + "  ·  " + n + " parte" + (n === 1 ? "" : "s") +
      "  (" + f + " suelo, " + w + " muro)",
    ciVertexNone:     "sin vértice seleccionado",
    ciVertexBase:     (n) => "vértice " + n,
    ciVertexStepTop:  "escalón-sup",
    ciVertexStepBot:  "escalón-inf",
    ciVertexWallEdge: "borde-muro",
    ciVertexCorner:   "esquina",
    ciVertexGrid:     "retícula",
    ciVertexFree:     "libre",

    /* ---- popover ----------------------------------------------- */
    popTitle: "Instrucciones",
    popInstructionsHTML:
      "<b>Plano de planta</b> (arriba) — <b>Muro desplegado</b> (abajo).  " +
      "Mantenga <b>Shift</b> al hacer clic para activar el ajuste.<br>" +
      "<b>Dibujar cable</b>: un clic, otro clic.  Si los dos clics están " +
      "en el mismo muro, se dibuja un segmento recto.  Si están en dos " +
      "muros distintos, el planificador de ruta construye una línea " +
      "recta virtual a través de cada muro físicamente conectado entre " +
      "ellos, la divide en cada esquina y archiva las piezas como un " +
      "solo cable.  Clic derecho deshace el último tramo.  Enter / Esc " +
      "finaliza.<br>" +
      "<b>Alt+clic</b> en un vértice para conectarse a él, adoptar su " +
      "cable o fusionar el dibujo actual en él.  Un clic simple durante " +
      "el dibujo siempre crea un vértice nuevo — su cable es " +
      "independiente de cualquier cosa cerca, aunque el vértice caiga " +
      "sobre el mismo punto físico que uno existente.  (Las conexiones " +
      "entre vistas — planta↔muro — siguen ocurriendo automáticamente: " +
      "las dos vistas comparten anclajes a través de la huella del " +
      "muro.)<br>" +
      "<b>Modo enfoque</b>: la tira se reduce al muro en el que hizo " +
      "clic y sus vecinos físicos.  Ayuda visual — el planificador de " +
      "ruta funciona igual.<br>" +
      "<b>Saltos</b> (arcos turquesa): un cable que gira en una esquina " +
      "entre dos muros alejados en la tira dibuja un arco turquesa en " +
      "lugar de una diagonal.<br>" +
      "<b>Arrastrar</b> un anclaje de esquina: solo se mueve su altura; " +
      "todos los socios de esa esquina física lo siguen.<br>" +
      "Seleccione un vértice y pulse <b>Suprimir</b>; sin vértice " +
      "seleccionado, <b>Suprimir</b> elimina el cable completo.  Pulse " +
      "<b>H</b> para ocultar / mostrar este panel.<br>" +
      "<b>Rueda</b>: zoom uniforme.  <b>Shift+rueda</b> sobre la tira " +
      "de muro: estirado solo horizontal (separa un muro comprimido " +
      "lateralmente sin tocar ninguna altura).  <b>Inicio</b>: " +
      "restablece ambas vistas a su zoom de ajuste.",

    /* ---- flash-status messages --------------------------------- */
    msgDrawStart:
      "Dibujando — clic en el primer punto del plano O de la tira de " +
      "muro · Enter / Esc finaliza",
    msgDrawCancelled:    "Dibujo cancelado",
    msgNeedTwoPoints:    "Dibujo cancelado (se necesitan al menos 2 "
                       + "puntos)",
    msgCableCreated:     (n) => "✓ Cable creado (" + n + " puntos)",
    msgAdopted:          "Cable existente adoptado — siga haciendo clic "
                       + "para extenderlo",
    msgMerged:           (n) => "✓ Fusionado con cable existente (" + n +
                       " puntos)",
    msgCableDeleted:     "✓ Cable eliminado",
    msgVertexDeleted:    "✓ Vértice eliminado",
    msgNoCableSelected:  "Ningún cable seleccionado",
    msgNoVertexSelected: "Ningún vértice seleccionado — haga clic en un "
                       + "vértice del cable primero",
    msgViewReset:        "Vista restablecida al ajuste",
    msgConfirmClear:     "¿Borrar todas las retículas y cables?",
    msgSavedOk:          (file) => "✓ Guardado " + file,
    msgSaveFailed:       "✗ Falló al guardar (¿sin servidor?)",

    /* ---- live status line -------------------------------------- */
    stPanning: (view) => "desplazando " + view,

    stRulerCount: (n) =>
      "regla · " + n + " medición" + (n === 1 ? "" : "es"),
    stRulerStepNew:     "clic en el primer punto o en una píldora "
                      + "para editar",
    stRulerStepSecond:  "clic en el segundo punto",
    stRulerStepLocked:  "bloqueado — clic para confirmar, Esc para "
                      + "liberar",
    stRulerStepEditing: (i) =>
      "editando #" + i + " — clic en lienzo vacío para empezar otra",
    stRulerAxisAuto:  " · E-O/N-S automático (Shift+X cicla · Alt libre)",
    stRulerAxisFixed: (a) =>
      " · eje " + a.toUpperCase() + " (Shift+X cicla · Alt libre)",
    stRulerNumeric:   " · Shift+D luz · Shift+A ángulo",
    stRulerSkipOff:   " · Shift omite escalones",
    stRulerSkipOn:    " · omitiendo escalones",
    stRulerExit:      " · Esc / Shift+R para salir",

    stDrawingFirst:
      "dibujando · clic en el primer punto del plano O de la tira de " +
      "muro · Alt+clic en un vértice para conectar/adoptar",
    stDrawingN: (view, n) =>
      "dibujando en " + view + " · " + n + " punto" +
      (n === 1 ? "" : "s"),
    stDrawingAdopted:  " · cable base adoptado",
    stDrawingFocus:    (tag) => " · ENFOQUE " + tag,
    stDrawingFinish:   " · Enter / Esc finaliza",
    stDrawingSnapOn:   " · ajuste ACTIVO",
    stDrawingSnapHint: " · mantenga Shift para ajustar",

    stVertexTagBase:  (n) => "vértice " + n,
    stVertexStepTop:  " · ESCALÓN-SUP",
    stVertexStepBot:  " · ESCALÓN-INF",
    stVertexWallEdge: " · BORDE-MURO",
    stVertexCorner:   " · ESQUINA",
    stVertexGrid:     " · RETÍCULA",
    stVertexFree:     " · libre",
    stVertexSnapHint: " · mantenga Shift para ajustar",

    stGridDrag: (view, t, pos) =>
      "arrastrando retícula " + view + " · " + t + "=" + pos,

    stHoverSeg:  (tag) => "→ " + tag,
    stHoverStep: "  ·  contrahuella de escalón",
    stHoverLen:  (cm) => "  ·  " + cm + " cm",

    stCableGroup: (id, n, f, w) =>
      "Cable " + id + "  ·  " + n + " partes (" + f + " suelo, " +
      w + " muro)",

    stFloorPos: (x, y) => "planta  x=" + x + "  y=" + y,
    stWallPos:  (u, v) => "muro    u=" + u + "  v=" + v,
    stOnStep:   "  ·  sobre escalón",
    stZoom:     (pct) => "  ·  " + pct + " %",

    /* ---- arrow-field toggle ------------------------------------ */
    arrowToggleLabel: "Mostrar flechas",
    arrowToggleTitle: "Mostrar u ocultar las flechas de escape "
                    + "muro→planta",

    /* ---- export ------------------------------------------------ */
    cableTitle: "Cable",
    subtitle: (anchors, parts, total) =>
      `${anchors} anclaje${anchors === 1 ? "" : "s"} . ` +
      `${parts} parte${parts === 1 ? "" : "s"} . total ${total}`,
    heightAxis:             "altura (cm)",
    lengthBreakdown:        "Desglose de longitud",
    wallRuns:               "Tramos de muro",
    floorRuns:              "Tramos de suelo",
    totalRow:               "Total",
    legend:                 "Leyenda",
    legendCable:            "cable",
    legendWallFloorLink:    "enlace muro<->suelo",
    legendTraversedWall:    "muro atravesado",
    legendStepFace:         "cara de escalón (rayado diag.)",
    legendVoid:             "vacío (rayado inv.)",
    wallSegmentsArrowStyle: "Segmentos de muro . estilo de flecha",

    previewTitle: "Diagramas de tendido de cables",
    previewSubtitle: (n) =>
      `${n} cable${n === 1 ? "" : "s"} - diseño de impresión en blanco ` +
      `y negro`,
    previewHint:
      "Cada diagrama se renderiza a ~2200 px de ancho. " +
      "Para mejor legibilidad al imprimir, colóquelo en su documento a " +
      "<b>180 - 210 mm</b> de ancho (aprox. la altura de un A5 " +
      "horizontal).",
    downloadAllPngs:      (n) => `Descargar todos los PNG (${n})`,
    downloadAllJson:      (n) => `Descargar todos los JSON (${n})`,
    filterCollinear:      "Filtrar vértices colineales en la tira",
    filterCollinearTitle: "Colapsar anclajes de borde de muro que yacen "
                        + "sobre un tramo recto del mismo muro",
    showAngles:           "Mostrar ángulos",
    showAnglesTitle:      "Añadir el ángulo de inclinación de cada " +
                          "tramo de cable conectado a su píldora " +
                          "de vértice",
    downloadPng:          "Descargar PNG",
    downloadJson:         "Descargar JSON",
    cardSizeMeta: (w, h, mm) =>
      `${w} x ${h} px  .  ${mm} mm de ancho a 300 DPI`,
    cardAlt:      (id) => `Diagrama de tendido del cable ${id}`,
    doneHint:     "Listo. Si su navegador pide permitir varias "
                + "descargas, pulse Permitir.",
    downloadedBundle: "Descargado cable_runs.json",

    popupBlocked:     "Ventana emergente bloqueada - permita las "
                    + "ventanas emergentes para esta página",
    noCablesToExport: "No hay cables para exportar",
    nothingToExport:  (failed) =>
      `Nada para exportar - ${failed} cable(s) con error`,
    exportedOk: (n) =>
      `Exportado${n === 1 ? "" : "s"} ${n} tendido${n === 1 ? "" : "s"} ` +
      `de cable`,
    exportedWithFailures: (n, failed) =>
      `Exportado${n === 1 ? "" : "s"} ${n} tendido${n === 1 ? "" : "s"} ` +
      `de cable - ${failed} con error`,
    exportButton:      "Exportar tendidos de cable (impresión)",
    exportButtonTitle: "Renderiza un diagrama en blanco y negro por "
                     + "cable físico",
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
