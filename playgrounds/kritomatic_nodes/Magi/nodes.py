"""Magi nodes and HTTP route.

Two things live here:

  1. `POST /magi/crop_embed` — an HTTP endpoint on ComfyUI's
     `PromptServer` route table.  The magi playground POSTs
     `{image_b64, bboxes_px}` and gets back
     `{success, reference_embeddings: [[768 floats], ...]}`.

     Lazy-loads the crop embedder on first call.  First call is slow
     (~3-5s for CPU ViT-MAE); subsequent calls are ~50-200ms per
     crop.

  2. `MagiLoader` — a V3 node returning a `MAGI_CROP_EMBEDDER`
     handle.  Not needed for the current pipeline (the route is the
     only consumer), but exposed for future graph wiring.  Same
     singleton as the route.

The route is registered at module import time so it's live as soon
as ComfyUI finishes loading the pack.  If ComfyUI is imported
without a `PromptServer` (e.g. unit-test harness), registration is
silently skipped and `MagiLoader` still works.
"""

from __future__ import annotations

import base64
from io import BytesIO

import numpy as np
from PIL import Image

from comfy_api.latest import io

from .crop_embedder import get_or_create_embedder, clear_embedder


# ---------------------------------------------------------------------------
#  HTTP route
# ---------------------------------------------------------------------------

_ROUTE_REGISTERED = False


def _json(data, status: int = 200):
    from aiohttp import web
    return web.json_response(data, status=status)


def _register_route() -> None:
    """Register `/magi/crop_embed` on ComfyUI's PromptServer.

    Called at module import.  Safe to call if PromptServer isn't up
    yet (silently returns).  Idempotent.
    """
    global _ROUTE_REGISTERED
    if _ROUTE_REGISTERED:
        return

    try:
        from server import PromptServer
    except ImportError:
        return

    if PromptServer.instance is None:
        return

    @PromptServer.instance.routes.post("/magi/crop_embed")
    async def crop_embed(request):
        try:
            body = await request.json()
        except Exception as e:
            return _json(
                {"success": False, "message": f"bad JSON body: {e}"},
                status=400,
            )

        image_b64 = body.get("image_b64")
        bboxes_px = body.get("bboxes_px")

        if not image_b64:
            return _json(
                {"success": False, "message": "image_b64 is required"},
                status=400,
            )
        if not isinstance(bboxes_px, list) or not bboxes_px:
            return _json(
                {"success": False,
                 "message": "bboxes_px must be a non-empty list"},
                status=400,
            )
        for i, b in enumerate(bboxes_px):
            if not isinstance(b, (list, tuple)) or len(b) != 4:
                return _json(
                    {"success": False,
                     "message": f"bboxes_px[{i}] is not a 4-tuple"},
                    status=400,
                )

        try:
            image_bytes = base64.b64decode(image_b64)
            image_pil   = Image.open(BytesIO(image_bytes)).convert("RGB")
            image_np    = np.array(image_pil)
        except Exception as e:
            return _json(
                {"success": False,
                 "message": f"could not decode image: {e}"},
                status=400,
            )

        try:
            embedder = get_or_create_embedder()
            embs = embedder.predict_as_lists(image_np, bboxes_px)
        except Exception as e:
            print(f"[magi] /magi/crop_embed failed: {e}", flush=True)
            return _json(
                {"success": False,
                 "message": f"inference failed: {e}"},
                status=500,
            )

        return _json({
            "success": True,
            "reference_embeddings": embs,
        })

    _ROUTE_REGISTERED = True
    print("[magi] registered /magi/crop_embed", flush=True)


_register_route()


# ---------------------------------------------------------------------------
#  MagiLoader node (V3)
# ---------------------------------------------------------------------------

class MagiLoader(io.ComfyNode):
    """Return a handle to the process-wide ViT-MAE crop embedder.

    The embedder is the same instance the `/magi/crop_embed` route
    uses; loading here just makes it available earlier, avoiding the
    first-call latency on the HTTP route.  Wiring this node into a
    workflow is optional — the route is self-sufficient.
    """

    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="MagiLoader",
            display_name="Magi Crop Embedder Loader",
            category="Magi",
            description=(
                "Loads ViT-MAE-base for character crop embedding. "
                "Feeds the magi playground's /analyze_characters route. "
                "Optional — the HTTP route lazy-loads the same "
                "singleton on first call."
            ),
            inputs=[
                io.String.Input(
                    "vit_mae_source",
                    default="facebook/vit-mae-base",
                    tooltip=(
                        "HF Hub name or local path. Default "
                        "'facebook/vit-mae-base' is the exact backbone "
                        "MAGI was trained against."
                    ),
                ),
            ],
            outputs=[
                io.Custom("MAGI_CROP_EMBEDDER").Output("embedder"),
            ],
        )

    @classmethod
    def execute(cls, vit_mae_source: str) -> io.NodeOutput:
        embedder = get_or_create_embedder(source=vit_mae_source)
        return io.NodeOutput(embedder)