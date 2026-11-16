"""HTTP route for saving workflows into the kritomatic repo.

Registers `POST /kritomatic/save_workflow` on ComfyUI's PromptServer.
Body: `{ "filename": "name.json", "workflow": {...} }`.

The workflow is the UI-format graph (exactly what ComfyUI's "Export"
produces, i.e. `app.graph.serialize()` in the frontend). It is written
as pretty-printed JSON to:

    <kritomatic repo root>/data/comfy_panel/workflows/<filename>

Repo root is resolved from this file's real path. Because `install.py`
symlinks `<repo>/playgrounds/kritomatic_nodes` into ComfyUI's
`custom_nodes/`, `Path(__file__).resolve()` follows the symlink back to
`<repo>/playgrounds/kritomatic_nodes/`, and `.parents[2]` is `<repo>`.
`install.py` enforces this layout; no other assumptions are made.

Registration is a side effect of import, matching how `Magi/nodes.py`
registers `POST /magi/crop_embed`.
"""

from __future__ import annotations

import json
import re
from pathlib import Path


# ---------------------------------------------------------------------------
# Path resolution
#
# __file__:      <repo>/playgrounds/kritomatic_nodes/kritomatic_workflows.py
# .parents[0]:   <repo>/playgrounds/kritomatic_nodes
# .parents[1]:   <repo>/playgrounds
# .parents[2]:   <repo>                            ← repo root
# ---------------------------------------------------------------------------
_REPO_ROOT = Path(__file__).resolve().parents[2]
_WORKFLOWS_DIR = _REPO_ROOT / "data" / "comfy_panel" / "workflows"

# Filenames are restricted to a conservative character class. The
# caller supplies a plain name; we append `.json` if missing. Path
# separators and leading dots are rejected outright — the file must
# land directly in `_WORKFLOWS_DIR` with no traversal.
_FILENAME_RE = re.compile(r"^[A-Za-z0-9 _\-.]+$")

_ROUTE_REGISTERED = False


def _json(data, status: int = 200):
    from aiohttp import web
    return web.json_response(data, status=status)


def _sanitize_filename(raw: str) -> str:
    name = (raw or "").strip()
    if not name:
        raise ValueError("filename is empty")
    if not name.endswith(".json"):
        name = name + ".json"
    if name.startswith("."):
        raise ValueError("filename may not start with a dot")
    if "/" in name or "\\" in name:
        raise ValueError("filename may not contain path separators")
    if not _FILENAME_RE.match(name):
        raise ValueError(
            "filename may only contain letters, digits, spaces, "
            "underscores, hyphens, and dots"
        )
    return name


def _register_route() -> None:
    global _ROUTE_REGISTERED
    if _ROUTE_REGISTERED:
        return

    try:
        from server import PromptServer
    except ImportError:
        return

    if PromptServer.instance is None:
        return

    @PromptServer.instance.routes.post("/kritomatic/save_workflow")
    async def save_workflow(request):
        try:
            body = await request.json()
        except Exception as e:
            return _json(
                {"success": False, "message": f"bad JSON body: {e}"},
                status=400,
            )

        filename_raw = body.get("filename")
        workflow = body.get("workflow")

        if not isinstance(filename_raw, str) or not filename_raw.strip():
            return _json(
                {"success": False, "message": "filename is required"},
                status=400,
            )
        if not isinstance(workflow, dict):
            return _json(
                {"success": False,
                 "message": "workflow must be a JSON object (UI-format map)"},
                status=400,
            )
        # Sanity: UI-format workflows always have a `nodes` list.
        if not isinstance(workflow.get("nodes"), list):
            return _json(
                {"success": False,
                 "message": "workflow.nodes missing or not a list "
                            "(is this UI format, not API format?)"},
                status=400,
            )

        try:
            filename = _sanitize_filename(filename_raw)
        except ValueError as e:
            return _json({"success": False, "message": str(e)}, status=400)

        try:
            _WORKFLOWS_DIR.mkdir(parents=True, exist_ok=True)
            target = _WORKFLOWS_DIR / filename
            target.write_text(
                json.dumps(workflow, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
        except Exception as e:
            print(f"[kritomatic:workflows] save failed: {e}", flush=True)
            return _json(
                {"success": False, "message": f"save failed: {e}"},
                status=500,
            )

        n_nodes = len(workflow.get("nodes") or [])
        n_links = len(workflow.get("links") or [])
        print(
            f"[kritomatic:workflows] saved {target} "
            f"({n_nodes} nodes, {n_links} links)",
            flush=True,
        )
        return _json({
            "success": True,
            "filename": filename,
            "path": str(target),
            "nodes": n_nodes,
            "links": n_links,
        })

    _ROUTE_REGISTERED = True
    print(
        f"[kritomatic:workflows] registered /kritomatic/save_workflow "
        f"(target: {_WORKFLOWS_DIR})",
        flush=True,
    )


_register_route()