"""
floor_plan_i18n.py — the floor-plan project's single translation table.

One language switch and two accessors:

    LANG = "en"                      ← the language switch
    TRANSLATIONS = { en, es }        ← every string the project shows
    T(key)                           ← the accessor for keys
    T_note(source_label)             ← the accessor for note labels

Every user-visible string the floor-plan project writes to stdout —
the main script's progress lines, the geometry reader's error message,
the PNG exporter's status lines, the notes-block heading drawn onto
the plan itself, and the notes-placement summary — lives in
TRANSLATIONS.  Nothing else in the project should contain a
hardcoded English (or Spanish, or anything else) literal that a
user reads.

This mirrors the translation tables in the sibling playground
projects (pg_i18n.py, bx_i18n.py): the same single switch, the same
key-per-string discipline, the same English-fallback behaviour for
missing keys, and the same "the value is a function when the string
varies with an argument" convention.

Adding a language
-----------------
    1.  Copy the `en` table to a new key, say `fr`.
    2.  Translate the values in place.  Keys stay in English — they
        are identifiers, not prose, and they must stay stable so a
        partial translation keeps working.
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
A key whose value varies with a number or a filename is a FUNCTION
of that argument, not a string with `{...}` placeholders.  This
keeps the format spec and any pluralisation decisions inside the
language table where they belong, instead of scattering them
through the caller.  `T("wroteSvg")(SVG_FILE)` reads as "the
wrote-svg message, called with SVG_FILE".

A key with no arguments is a plain string; `T("notesTitle")` reads
as "the notes heading".

Note-block labels — T_note
--------------------------
The note block ("Room height / Step rise / Wall thickness / Floor
slab") is authored by room.py and read from room_dimensions.json, so
the layout code sees an English source label and cannot know which
translation key it corresponds to.  NOTE_LABEL_KEYS below maps each
source label to a key in this table; T_note performs the lookup with
graceful degradation — a source label that has no key is returned
unchanged, so adding a new note to room.py never crashes the
translation layer, it just renders that one note in English until a
key is added here.

What is NOT translated
----------------------
The label texts that appear on the plan itself — the wall tags
(W4 = 166), the door tags (D1, D2), the column tags (C1, C2, C3),
the STEP label — are produced by floor_plan_layout.py from data and
are treated as identifiers by a reader of the plan in every
language.  The numbers next to the note labels ("238", "28", "15",
"20") are locale-neutral and are passed through as-is.
"""


LANG = "es"


# Source label (as authored in room.py) → translation key.  Only the
# labels the room.py notes block currently emits are listed; a fork
# that adds a new note adds one line here and two table entries below
# (one per language).  Any source label with no key falls through
# T_note unchanged.
NOTE_LABEL_KEYS = {
    "Room height":    "noteRoomHeight",
    "Step rise":      "noteStepRise",
    "Wall thickness": "noteWallThickness",
    "Floor slab":     "noteFloorSlab",
}


TRANSLATIONS = {
    "en": {
        # ---- floor_plan.py — main progress lines -------------------
        "readInputs": lambda walls, dimensions:
            f"Read {walls} and {dimensions}",

        # Each count line is a whole formatted output line.  The
        # per-language padding before the colon is what aligns the
        # block of eight counts into a single column of values.
        "countPlanFaces":  lambda n: f"  plan faces     : {n}",
        "countColumns":    lambda n: f"  columns        : {n}",
        "countSteps":      lambda n: f"  steps          : {n}",
        "countOpenings":   lambda n: f"  openings       : {n}",
        "countDimensions": lambda n: f"  dimensions     : {n}",
        "countHoles":      lambda n: f"  holes          : {n}",
        "countLabels":     lambda n: f"  labels         : {n}",
        "notesPlacement":  lambda p: f"  notes placement: {p}",

        "layoutClean": lambda pen: (
            "  layout         : no overlaps "
            f"(obstacle penetration {pen:.1f})"
        ),
        "layoutOverlaps": lambda n_ov, n_labels, area: (
            f"  layout         : {n_ov} overlap(s) across "
            f"{n_labels} label(s); total overlap area {area:.1f}"
        ),

        "wroteSvg":        lambda file: f"Wrote {file}",
        "wroteLayoutMeta": lambda file: f"Wrote {file}",
        "wrotePng":        lambda file: f"Wrote {file}",

        # ---- floor_plan_geometry.py --------------------------------
        "errWallsMissing": lambda path: (
            f"{path} not found — run room.py first."
        ),

        # ---- floor_plan_png.py -------------------------------------
        "pngSkipped": (
            "  (skipping PNG — install cairosvg: pip install cairosvg)"
        ),
        "pngFailed": lambda err: f"  (PNG export failed: {err})",

        # ---- floor_plan_layout.py — notes block --------------------
        "notesTitle":             "GENERAL NOTES",
        "notesPlacementTop":      "top",
        "notesPlacementBottom":   "bottom",
        "notesPlacementLeft":     "left",
        "notesPlacementRight":    "right",
        "notesPlacementInside":   lambda v, h: f"inside {v}-{h}",
        "notesPlacementOutside":  lambda v, h: f"outside {v}-{h}",
        "notesPlacementFallback": "fallback (no free pocket)",

        # ---- note-block row labels (source labels from room.py) ----
        "noteRoomHeight":    "Room height",
        "noteStepRise":      "Step rise",
        "noteWallThickness": "Wall thickness",
        "noteFloorSlab":     "Floor slab",
    },

    "es": {
        # ---- floor_plan.py — main progress lines -------------------
        "readInputs": lambda walls, dimensions:
            f"Leídos {walls} y {dimensions}",

        "countPlanFaces":  lambda n: f"  caras de planta: {n}",
        "countColumns":    lambda n: f"  columnas       : {n}",
        "countSteps":      lambda n: f"  escalones      : {n}",
        "countOpenings":   lambda n: f"  aberturas      : {n}",
        "countDimensions": lambda n: f"  dimensiones    : {n}",
        "countHoles":      lambda n: f"  huecos         : {n}",
        "countLabels":     lambda n: f"  etiquetas      : {n}",
        "notesPlacement":  lambda p: f"  posición notas : {p}",

        "layoutClean": lambda pen: (
            "  disposición    : sin solapamientos "
            f"(penetración de obstáculos {pen:.1f})"
        ),
        "layoutOverlaps": lambda n_ov, n_labels, area: (
            f"  disposición    : {n_ov} solapamiento(s) en "
            f"{n_labels} etiqueta(s); área total solapada {area:.1f}"
        ),

        "wroteSvg":        lambda file: f"Escrito {file}",
        "wroteLayoutMeta": lambda file: f"Escrito {file}",
        "wrotePng":        lambda file: f"Escrito {file}",

        # ---- floor_plan_geometry.py --------------------------------
        "errWallsMissing": lambda path: (
            f"no se encontró {path} — ejecute room.py primero."
        ),

        # ---- floor_plan_png.py -------------------------------------
        "pngSkipped": (
            "  (omitiendo PNG — instale cairosvg: pip install cairosvg)"
        ),
        "pngFailed": lambda err: (
            f"  (la exportación a PNG falló: {err})"
        ),

        # ---- floor_plan_layout.py — notes block --------------------
        "notesTitle":             "NOTAS GENERALES",
        "notesPlacementTop":      "arriba",
        "notesPlacementBottom":   "abajo",
        "notesPlacementLeft":     "izquierda",
        "notesPlacementRight":    "derecha",
        "notesPlacementInside":   lambda v, h: f"interior {v}-{h}",
        "notesPlacementOutside":  lambda v, h: f"exterior {v}-{h}",
        "notesPlacementFallback": "alternativa (sin hueco libre)",

        # ---- note-block row labels (source labels from room.py) ----
        "noteRoomHeight":    "Altura de la habitación",
        "noteStepRise":      "Altura del escalón",
        "noteWallThickness": "Espesor del muro",
        "noteFloorSlab":     "Losa de piso",
    },
}


def T(key):
    """Return the active language's value for `key`, falling back to
    English when the active language does not define it.

    The return value is whatever the table holds: a plain string for
    a key with no arguments, a callable for a key whose text varies
    with its arguments.  The caller supplies the arguments when the
    value is callable.  This matches the pg_i18n / bx_i18n
    convention exactly."""
    active = TRANSLATIONS.get(LANG) or TRANSLATIONS["en"]
    v = active.get(key)
    if v is None:
        v = TRANSLATIONS["en"].get(key)
    return v


def T_note(source_label):
    """Return the translated label for a note entry whose source
    (room.py-authored) label is `source_label`.

    Looks up the key in NOTE_LABEL_KEYS, then calls T on it.  A
    source label with no key is returned unchanged — a fork that
    adds a note to room.py without adding a key here renders that
    one note in its source language instead of dropping it or
    raising."""
    key = NOTE_LABEL_KEYS.get(source_label)
    if key is None:
        return source_label
    v = T(key)
    return v if v is not None else source_label
