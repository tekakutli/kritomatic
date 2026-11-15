"""Read resolution from the layout playground's Generation Frame.

Drop `PlaygroundResolutionSelect` into the sampler workflow in place of
`PixArtResolutionSelect`.  It reads `viewer.model` and `viewer.ratio`
from the playground's `/state` and returns the corresponding (width,
height) from the same `pixart_res` table the stock node uses.

The playground's Generation Frame is what determines the region that
will be generated — bboxes drawn inside it are the ones `DrawatoonSetIP`
consumes.  So picking dimensions from the same Frame keeps the sampler's
latent size and the bbox layout consistent by construction.

Caching
=======
`fingerprint_inputs` returns the playground's state hash, so changing
the Frame's ratio or model in the browser causes the node to
re-execute on the next queue.  ComfyUI caches the node otherwise.

Fallback
========
If the playground is unreachable, the node returns 512×512 (the
drawatoon-native budget) and prints a warning to ComfyUI's stdout.
A silent mismatch is impossible — the warning is visible in the
console.  Users who never run the playground should keep using
`PixArtResolutionSelect` instead.
"""

from __future__ import annotations

from comfy_api.latest import io

from .layout_client import fetch_state_dict, probe_version


_DEFAULT_WIDTH  = 512
_DEFAULT_HEIGHT = 512


class PlaygroundResolutionSelect(io.ComfyNode):

    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="PlaygroundResolutionSelect",
            display_name="Playground Resolution Select",
            category="ExtraModels/PixArt",
            inputs=[],
            outputs=[
                io.Int.Output("width"),
                io.Int.Output("height"),
            ],
        )

    @classmethod
    def fingerprint_inputs(cls, **kwargs):
        return probe_version()

    @classmethod
    def execute(cls) -> io.NodeOutput:
        from .PixArt.conf import pixart_res

        state = fetch_state_dict()
        if not state:
            print(
                "[PlaygroundResolutionSelect] layout playground "
                "unreachable; falling back to "
                f"{_DEFAULT_WIDTH}x{_DEFAULT_HEIGHT}. Start the layout "
                "playground or use PixArtResolutionSelect.",
                flush=True,
            )
            return io.NodeOutput(_DEFAULT_WIDTH, _DEFAULT_HEIGHT)

        viewer = state.get("viewer") or {}
        model = viewer.get("model") or "PixArtMS_Sigma_XL_2_512"
        ratio = viewer.get("ratio") or "1.00"

        model_table = pixart_res.get(model)
        if model_table is None:
            raise RuntimeError(
                f"PlaygroundResolutionSelect: unknown model {model!r}. "
                f"Available: {sorted(pixart_res.keys())}"
            )

        dims = model_table.get(ratio)
        if dims is None:
            raise RuntimeError(
                f"PlaygroundResolutionSelect: ratio {ratio!r} not found "
                f"for model {model!r}."
            )

        width, height = dims
        print(
            f"[PlaygroundResolutionSelect] {model} / {ratio} → "
            f"{width}x{height}",
            flush=True,
        )
        return io.NodeOutput(int(width), int(height))