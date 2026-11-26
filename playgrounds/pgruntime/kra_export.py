"""
pgruntime.kra_export — shared scaffolding for playgrounds that export
a Krita document via the daemon's `BatchExecutor`.

Two playgrounds produce `.kra` files: `cone` and `text_wall`.  Both:

    · walk up to find `src/kritomatic` (so `kritomatic.batch` is
      importable regardless of launch directory),
    · create a document sized to the playground's on-screen band,
    · create a top-level `Export` group,
    · append their own commands (polygons, texts, masks),
    · append a `save_document` command,
    · run the whole bundle through `BatchExecutor`,
    · check for per-command errors and summarize.

The per-playground part is *which commands to emit* — cone emits
five modes of shapes/masks; text_wall emits trapezoid-affine texts
and polygons.  The scaffolding around that emission is identical
across the two, and lives here so a future change (a new constant, a
different summary message, a change to how `save_document` is
appended) doesn't have to be made twice.

The module is intentionally narrow: it does NOT expose a `generate()`
or a payload schema.  Each playground's `<name>_kra.py` still owns
its `generate(payload)` entry point and its mode-specific builders.
This module provides the pieces those call into.

LAYERING
========
    pgruntime.krita_api        one command, one response, (data, err)
    pgruntime.kra_export       a command bundle, per-command results,
                               standard success/failure summary shape
    playgrounds/<name>/..._kra.py  mode-specific payload construction,
                                    calls the two modules above

`krita_api` re-exports `BatchExecutor` so `kra_export` doesn't need
its own bootstrap.  Nothing in `kra_export` imports a playground;
playgrounds import from it, not the other way around.
"""

from pathlib import Path
import json

from . import krita_api


# ---- constants shared by every KRA-exporting playground ----------

DEFAULT_CANVAS_W = 1920
DEFAULT_CANVAS_H = 1080
DEFAULT_RESOLUTION = 72
DEFAULT_FONT_FAMILY = "sans-serif"
EXPORT_GROUP_NAME = "Export"


# ---- helpers -----------------------------------------------------

def coerce_transform(raw):
    """Accept a list or a JSON string; return a 6-float list or None.

    The daemon's `add_vector_text` command takes `transform` as a
    JSON-encoded 6-element affine.  Playground code holds the value
    as either a list (freshly computed) or a string (round-tripped
    through a scene file).  This accepts both and returns a list,
    or None if the input is malformed.
    """
    if raw is None:
        return None
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except Exception:
            return None
    if not isinstance(raw, (list, tuple)):
        return None
    if len(raw) != 6:
        return None
    try:
        return [float(v) for v in raw]
    except (TypeError, ValueError):
        return None


def normalize_band(raw):
    """Return {width: int, height: int} with defaults and clamps.

    The band is the playground's on-screen canvas height — the .kra
    is created at the same aspect the user sees.  A missing or
    malformed band falls back to the 1920×1080 default rather than
    failing the export.
    """
    if not isinstance(raw, dict):
        return {"width": DEFAULT_CANVAS_W, "height": DEFAULT_CANVAS_H}
    w = raw.get("width", DEFAULT_CANVAS_W)
    h = raw.get("height", DEFAULT_CANVAS_H)
    try:
        w = int(round(float(w)))
    except (TypeError, ValueError):
        w = DEFAULT_CANVAS_W
    try:
        h = int(round(float(h)))
    except (TypeError, ValueError):
        h = DEFAULT_CANVAS_H
    if w < 1:
        w = DEFAULT_CANVAS_W
    if h < 1:
        h = DEFAULT_CANVAS_H
    return {"width": w, "height": h}


def common_header(band, doc_name):
    """Return the two-command header every KRA export begins with.

    Creates the document at the band's dimensions, then creates an
    `Export` group at the top of the document's layer stack.  Every
    subsequent command's `parent` is this group; rectangles go
    below texts (or vice versa) by order of `create_layer` within
    the bundle.
    """
    return [
        {
            "type": "create_new_with_dimensions",
            "name": doc_name,
            "width": band["width"],
            "height": band["height"],
            "resolution": DEFAULT_RESOLUTION,
        },
        {
            "type": "create_layer",
            "name": EXPORT_GROUP_NAME,
            "layer_type": "grouplayer",
            "position": "top",
        },
    ]


def prepare_output_path(output_path):
    """Expand `~`, ensure the `.kra` suffix, return a Path.

    The user (or a remote client) supplies the path; this makes a
    path ending in `.png` or nothing at all become `.kra` without
    touching the stem.
    """
    out = Path(str(output_path)).expanduser()
    if out.suffix.lower() != ".kra":
        out = out.with_suffix(".kra")
    return out


def execute_bundle(bundle, out_path, *, count, kind):
    """Append `save_document`, run through BatchExecutor, summarize.

    `count` and `kind` describe the export for the success message
    (e.g. `count=len(shapes), kind="shape(s)"`).

    Returns the standard response shape both playgrounds use:

        {"success": False, "message": "...", "results": [...]}
        {"success": True,  "message": "Wrote ...", "output_path": ..., "results": [...]}

    A per-command failure on any command causes `success: False` and
    lists every failed command in the summary.  The `results` array
    is always included (even on success) so a debugging caller can
    inspect the daemon's full response.
    """
    bundle["commands"].append({
        "type": "save_document",
        "file_path": str(out_path),
    })

    executor = krita_api.BatchExecutor()
    try:
        results = executor.execute(bundle)
    finally:
        executor.close()

    failed = [r for r in results if r["status"] == "error"]
    if failed:
        summary = "; ".join(
            f"{r['command']} -> {r['message']}" for r in failed
        )
        return {
            "success": False,
            "message": f"{len(failed)} command(s) failed: {summary}",
            "results": results,
        }

    return {
        "success": True,
        "message": f"Wrote {out_path} ({count} {kind})",
        "output_path": str(out_path),
        "results": results,
    }