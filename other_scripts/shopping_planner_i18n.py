"""
shopping_planner_i18n.py — translation table for shopping_planner.py.

One switch and one accessor:

    LANG = "en"                       ← the language switch
    TRANSLATIONS = { "en": ..., "es": ... }
    T(key)                            ← the accessor

Every user-visible string that shopping_planner.py prints or shows
in its GUI lives in TRANSLATIONS.  A key whose value varies with a
number or a filename is a FUNCTION of that argument, not a string
with `{...}` placeholders — this keeps the format spec inside the
language table where it belongs.  A key with no arguments is a
plain string, or a list for a header row; T() returns it as-is.

Adding a language
-----------------
    1.  Copy the `en` (or `es`) table to a new key, say `fr`.
    2.  Translate the values in place.  Keys stay in English.
    3.  Any key the new table is missing falls back to `en`, so a
        partial translation renders with untranslated strings in
        English alongside the translated ones.

Switching the active language
-----------------------------
    Change LANG near the top of this file.  That is the only switch.

Data-schema identifiers are NOT translated
------------------------------------------
The JSON schema keys — "items", "name", "description", "price",
"section", "quantity", and the reserved metadata key "_meta" — are
data identifiers, not user-visible prose, and are not routed
through this table.  The same goes for the section names that come
out of the JSON: they are data.
"""

LANG = "en"


TRANSLATIONS = {
    "en": {
        # ---- module import ------------------------------------------
        "warnNoTkinter": (
            "⚠️  Could not import tkinter/tksheet."
        ),
        "warnNoTkinterHint1": (
            "   To use the quantity editor, install:"
        ),
        "warnNoTkinterHint2": "   pip install tksheet",
        "warnNoTkinterHint3": (
            "   (tkinter usually ships with Python)"
        ),

        # ---- JSON loading -------------------------------------------
        "errJsonMissing": lambda path: (
            f"❌ Error: the JSON file '{path}' does not exist."
        ),
        "errJsonMissingHint": (
            "   Make sure the file has an 'items' sheet."
        ),
        "errNoItems": lambda path: (
            f"❌ Error: the JSON file '{path}' does not contain "
            f"an 'items' sheet."
        ),
        "errNoItemsHint": (
            "   The 'items' sheet must exist with columns: "
            "name, description, price, section."
        ),
        "warnItemsEmpty": (
            "⚠️  The 'items' sheet has no data (headers only)."
        ),

        # ---- PDF generation -----------------------------------------
        "pdfSaveError": lambda err: (
            f"Error saving PDF: {err}. Using fallback method..."
        ),
        "pdfAltFailed": lambda err: (
            f"The fallback method also failed: {err}"
        ),
        "hrLine": "=" * 46,
        "genPdf": lambda name: f"Generating PDF: {name}",
        "totalElements": lambda n: f"Total elements: {n}",
        "fontSize": lambda n: f"Font size: {n} px",
        "padding": lambda t, b: (
            f"Top padding: {t} px, bottom: {b} px"
        ),
        "margins": lambda l, r, t, b: (
            f"Margins (cm): L={l}, R={r}, T={t}, B={b}"
        ),
        "twoPagesMode": lambda yes: (
            f"Two pages per sheet: {'YES' if yes else 'NO'}"
        ),
        "footerHeight": lambda n: f"Footer height: {n} px",
        "virtualSize": lambda w, h: f"Virtual size: {w}×{h} px",
        "rowsPerVirtual": lambda n: f"Rows per virtual page: {n}",
        "virtualPagesNeeded": lambda n: (
            f"Virtual pages needed: {n}"
        ),
        "genVirtualPage": lambda i, total, rows: (
            f"--- Generating virtual page {i}/{total} "
            f"({rows} rows) ---"
        ),
        "physicalPagesGenerated": lambda n: (
            f"Physical pages (sheets) generated: {n}"
        ),
        "savingPdf": "\nSaving PDF...",
        "pdfGenerated": lambda name: f"✓ PDF generated: {name}",
        "totalSheets": lambda n: f"  Total sheets: {n}",
        "pdfFailed": "❌ PDF generation failed",
        "pdfNoPages": (
            "⚠️  No pages to save — nothing to export."
        ),

        # ---- editor startup -----------------------------------------
        "errNoEditor": (
            "❌ Cannot open the editor because tkinter/tksheet "
            "is missing."
        ),
        "errNoEditorHint": "   Install tksheet: pip install tksheet",
        "autoLoaded": lambda name: (
            f"Autoloaded latest saved sheet: {name}"
        ),
        "autoLoadedNone": (
            "No previously saved sheet to autoload."
        ),

        # ---- GUI — window, status, sheet selector -------------------
        "windowTitle": "Shopping planner — tksheet",
        "statusSaved": "Saved",
        "statusModified": "Modified",

        # ---- GUI — dialogs & messages -------------------------------
        "errTitle": "Error",
        "errSheetMissing": lambda name: (
            f"Sheet '{name}' does not exist."
        ),
        "infoEmptyTitle": "Empty",
        "infoSheetEmpty": lambda name: f"Sheet '{name}' is empty.",
        "infoLoadedTitle": "Loaded",
        "infoSheetLoaded": lambda name: f"Sheet '{name}' loaded.",
        "labelSheet": "Sheet:",
        "warnSelectTitle": "Selection",
        "warnNoSheetSelected": "No sheet selected.",
        "warnUnsavedTitle": "Unsaved changes",
        "warnUnsavedLoadMsg": (
            "There are unsaved changes.  Save before loading "
            "another sheet?\n"
            "Yes = save, No = discard, Cancel = cancel the load"
        ),
        "warnUnsavedExitMsg": (
            "There are unsaved changes.  Save before exiting?\n"
            "Yes = save, No = exit without saving, Cancel = go back"
        ),
        "warnNoSheetToDelete": "No sheet selected to delete.",

        "btnLoad": "Load",
        "btnSave": "Save",
        "btnDelete": "Delete sheet",
        "btnCancel": "Cancel",

        "dlgSaveSheetTitle": "Save sheet",
        "dlgSaveSheetPrompt": "Enter a name for the sheet:",
        "warnNameEmptyTitle": "Empty name",
        "warnNameEmptyMsg": "The sheet name cannot be empty.",
        "dlgOverwriteTitle": "Overwrite",
        "dlgOverwriteMsg": lambda name: (
            f"A sheet named '{name}' already exists.  Overwrite?"
        ),
        "infoSavedTitle": "Saved",
        "infoSheetSaved": lambda name, fname: (
            f"Sheet '{name}' saved to {fname}"
        ),
        "dlgConfirmDeleteTitle": "Confirm deletion",
        "dlgConfirmDeleteMsg": lambda name: (
            f"Are you sure you want to delete sheet '{name}'?"
        ),
        "infoDeletedTitle": "Deleted",
        "infoSheetDeleted": lambda name: f"Sheet '{name}' deleted.",

        # ---- GUI — tabs, headers, search ----------------------------
        "tabQuantities": "Quantities",
        "tabResults": "Results",
        "colName": "Name",
        "colDescription": "Description",
        "colPrice": "Price",
        "colQuantity": "Quantity",
        "colArticle": "Article",
        "colPriceXQty": "Price × Qty",
        "colSubtotal": "Subtotal",
        "dlgSearchTitle": "Search",
        "dlgSearchPrompt": "Search product:",
        "infoSearchTitle": "Search",
        "infoSearchNotFound": lambda text: (
            f"'{text}' was not found in the product list."
        ),

        # ---- GUI — actions & result rows ----------------------------
        "infoNoDataTitle": "No data",
        "infoNoDataShort": (
            "No items with quantity greater than zero to export."
        ),
        "btnCalc": "Compute",
        "btnCalcDetailed": "Compute detailed",
        "btnExportPdf": "Export PDF",
        "btnExportPdfDetailed": "Export PDF detailed",
        "sectionLine": lambda name: f"--- {name} ---",
        "subtotalLine": lambda name: f"--- Subtotal {name} ---",
        "subtotalOfSection": lambda name: f"Subtotal {name}",
        "totalLabel": "TOTAL",

        # ---- PDF — headers and footer -------------------------------
        "pdfHeaders": [
            "Product", "Description", "Cost",
            "Month 1", "Month 2", "Month 3", "Month 4",
        ],
        "pdfHeadersDetailed": [
            "Article", "Price × Quantity", "Subtotal",
        ],
        "pageFooter": lambda page, total: (
            f"Page {page} of {total}"
        ),

        # ---- CLI ----------------------------------------------------
        "argDescription": "Shopping planner with data from JSON",
        "argJsonHelp": lambda default: (
            f"JSON file with the data (default: {default})"
        ),
        "argExportCalendarHelp": (
            "Headless: write the calendar PDF (no editor)"
        ),
        "argExportCostsHelp": (
            "Headless: write the costs PDF from the latest "
            "saved sheet"
        ),
        "costsNoSheet": (
            "No saved sheet found — nothing to build a costs PDF from."
        ),
    },

    "es": {
        # ---- module import ------------------------------------------
        "warnNoTkinter": (
            "⚠️  No se pudo importar tkinter/tksheet."
        ),
        "warnNoTkinterHint1": (
            "   Para usar el editor de cantidades, instala:"
        ),
        "warnNoTkinterHint2": "   pip install tksheet",
        "warnNoTkinterHint3": (
            "   (tkinter normalmente viene con Python)"
        ),

        # ---- JSON loading -------------------------------------------
        "errJsonMissing": lambda path: (
            f"❌ Error: El archivo JSON '{path}' no existe."
        ),
        "errJsonMissingHint": (
            "   Asegúrate de tener el archivo con la hoja 'items'."
        ),
        "errNoItems": lambda path: (
            f"❌ Error: El archivo JSON '{path}' no contiene la "
            f"hoja 'items'."
        ),
        "errNoItemsHint": (
            "   La hoja 'items' debe existir con columnas: "
            "name, description, price, section."
        ),
        "warnItemsEmpty": (
            "⚠️  La hoja 'items' no contiene datos (solo encabezados)."
        ),

        # ---- PDF generation -----------------------------------------
        "pdfSaveError": lambda err: (
            f"Error al guardar PDF: {err}. Usando método alternativo..."
        ),
        "pdfAltFailed": lambda err: (
            f"El método alternativo también falló: {err}"
        ),
        "hrLine": "=" * 46,
        "genPdf": lambda name: f"Generando PDF: {name}",
        "totalElements": lambda n: f"Total de elementos: {n}",
        "fontSize": lambda n: f"Tamaño de fuente: {n} px",
        "padding": lambda t, b: (
            f"Padding superior: {t} px, inferior: {b} px"
        ),
        "margins": lambda l, r, t, b: (
            f"Márgenes (cm): Izq={l}, Der={r}, Sup={t}, Inf={b}"
        ),
        "twoPagesMode": lambda yes: (
            f"Modo dos páginas por hoja: {'SÍ' if yes else 'NO'}"
        ),
        "footerHeight": lambda n: f"Altura del pie de página: {n} px",
        "virtualSize": lambda w, h: f"Tamaño virtual: {w}×{h} px",
        "rowsPerVirtual": lambda n: f"Filas por página virtual: {n}",
        "virtualPagesNeeded": lambda n: (
            f"Páginas virtuales necesarias: {n}"
        ),
        "genVirtualPage": lambda i, total, rows: (
            f"--- Generando página virtual {i}/{total} "
            f"({rows} filas) ---"
        ),
        "physicalPagesGenerated": lambda n: (
            f"Páginas físicas (hojas) generadas: {n}"
        ),
        "savingPdf": "\nGuardando PDF...",
        "pdfGenerated": lambda name: f"✓ PDF generado: {name}",
        "totalSheets": lambda n: f"  Total de hojas: {n}",
        "pdfFailed": "❌ Falló la generación del PDF",
        "pdfNoPages": (
            "⚠️  No hay páginas para guardar — nada que exportar."
        ),

        # ---- editor startup -----------------------------------------
        "errNoEditor": (
            "❌ No se puede abrir el editor porque falta "
            "tkinter/tksheet."
        ),
        "errNoEditorHint": "   Instala tksheet: pip install tksheet",
        "autoLoaded": lambda name: (
            f"Cargada automáticamente la hoja guardada más reciente: "
            f"{name}"
        ),
        "autoLoadedNone": (
            "No hay ninguna hoja guardada previamente que cargar."
        ),

        # ---- GUI — window, status, sheet selector -------------------
        "windowTitle": "Planificador de compras — tksheet",
        "statusSaved": "Guardado",
        "statusModified": "Modificado",

        # ---- GUI — dialogs & messages -------------------------------
        "errTitle": "Error",
        "errSheetMissing": lambda name: f"La hoja '{name}' no existe.",
        "infoEmptyTitle": "Vacío",
        "infoSheetEmpty": lambda name: f"La hoja '{name}' está vacía.",
        "infoLoadedTitle": "Cargado",
        "infoSheetLoaded": lambda name: (
            f"Hoja '{name}' cargada correctamente."
        ),
        "labelSheet": "Hoja:",
        "warnSelectTitle": "Selección",
        "warnNoSheetSelected": "No hay hoja seleccionada.",
        "warnUnsavedTitle": "Cambios sin guardar",
        "warnUnsavedLoadMsg": (
            "Hay cambios sin guardar. ¿Quieres guardarlos antes de "
            "cargar otra hoja?\n"
            "Sí = guardar, No = descartar, Cancelar = cancelar carga"
        ),
        "warnUnsavedExitMsg": (
            "Hay cambios sin guardar. ¿Quieres guardarlos antes de "
            "salir?\n"
            "Sí = guardar, No = salir sin guardar, Cancelar = volver"
        ),
        "warnNoSheetToDelete": (
            "No hay hoja seleccionada para eliminar."
        ),

        "btnLoad": "Cargar",
        "btnSave": "Guardar",
        "btnDelete": "Eliminar hoja",
        "btnCancel": "Cancelar",

        "dlgSaveSheetTitle": "Guardar hoja",
        "dlgSaveSheetPrompt": "Ingresa un nombre para la hoja:",
        "warnNameEmptyTitle": "Nombre vacío",
        "warnNameEmptyMsg": (
            "El nombre de la hoja no puede estar vacío."
        ),
        "dlgOverwriteTitle": "Sobrescribir",
        "dlgOverwriteMsg": lambda name: (
            f"Ya existe una hoja con el nombre '{name}'. "
            f"¿Sobrescribir?"
        ),
        "infoSavedTitle": "Guardado",
        "infoSheetSaved": lambda name, fname: (
            f"Hoja '{name}' guardada en {fname}"
        ),
        "dlgConfirmDeleteTitle": "Confirmar eliminación",
        "dlgConfirmDeleteMsg": lambda name: (
            f"¿Seguro que quieres eliminar la hoja '{name}'?"
        ),
        "infoDeletedTitle": "Eliminado",
        "infoSheetDeleted": lambda name: f"Hoja '{name}' eliminada.",

        # ---- GUI — tabs, headers, search ----------------------------
        "tabQuantities": "Cantidades",
        "tabResults": "Resultados",
        "colName": "Nombre",
        "colDescription": "Descripción",
        "colPrice": "Precio",
        "colQuantity": "Cantidad",
        "colArticle": "Artículo",
        "colPriceXQty": "Precio × Cantidad",
        "colSubtotal": "Subtotal",
        "dlgSearchTitle": "Buscar",
        "dlgSearchPrompt": "Buscar producto:",
        "infoSearchTitle": "Búsqueda",
        "infoSearchNotFound": lambda text: (
            f"No se encontró '{text}' en la lista de productos."
        ),

        # ---- GUI — actions & result rows ----------------------------
        "infoNoDataTitle": "Sin datos",
        "infoNoDataShort": (
            "No hay artículos con cantidad mayor a cero para exportar."
        ),
        "btnCalc": "Calcular",
        "btnCalcDetailed": "Calcular detallado",
        "btnExportPdf": "Exportar PDF",
        "btnExportPdfDetailed": "Exportar PDF detallado",
        "sectionLine": lambda name: f"--- {name} ---",
        "subtotalLine": lambda name: f"--- Subtotal {name} ---",
        "subtotalOfSection": lambda name: f"Subtotal {name}",
        "totalLabel": "TOTAL",

        # ---- PDF — headers and footer -------------------------------
        "pdfHeaders": [
            "Producto", "Descripción", "Costo",
            "Mes 1", "Mes 2", "Mes 3", "Mes 4",
        ],
        "pdfHeadersDetailed": [
            "Artículo", "Precio × Cantidad", "Subtotal",
        ],
        "pageFooter": lambda page, total: (
            f"Página {page} de {total}"
        ),

        # ---- CLI ----------------------------------------------------
        "argDescription": "Planificador de compras con datos desde JSON",
        "argJsonHelp": lambda default: (
            f"Archivo JSON con los datos (default: {default})"
        ),
        "argExportCalendarHelp": (
            "Sin interfaz: genera el PDF de calendario (sin editor)"
        ),
        "argExportCostsHelp": (
            "Sin interfaz: genera el PDF de costos desde la hoja "
            "guardada más reciente"
        ),
        "costsNoSheet": (
            "No se encontró ninguna hoja guardada — nada para "
            "construir un PDF de costos."
        ),
    },
}


def T(key):
    """Return the active language's value for `key`, falling back
    to English when the active language does not define it.

    The return value is whatever the table holds: a plain string or
    a list for a key with no arguments, a callable for a key whose
    text varies with its arguments.  The caller supplies the
    arguments when the value is callable."""
    active = TRANSLATIONS.get(LANG) or TRANSLATIONS["en"]
    v = active.get(key)
    if v is None:
        v = TRANSLATIONS["en"].get(key)
    return v
