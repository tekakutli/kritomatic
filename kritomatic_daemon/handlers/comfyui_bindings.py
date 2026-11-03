"""Canvas and selection source bindings.

Small, stable commands that tell the workflow-read path where to pull
canvas and selection pixels from. The actual reading lives in
`comfyui_krita.py`; this module just stores the choices.
"""

from __future__ import annotations

from ..decorators import command, AutoDispatchHandler


class BindingsCommands(AutoDispatchHandler):

    def __init__(self, parent):
        self.parent = parent
        super().__init__()

    @command(
        category="comfyui",
        help_text="Choose what ETN_KritaCanvas reads from the document.",
        args={
            "--source": {"type": "str", "default": "active",
                         "choices": ["active", "layer"],
                         "help": "active = flattened composite of all "
                                 "visible layers; layer = one layer's "
                                 "projection"},
            "--layer_name": {"type": "str", "required": False,
                             "help": "Required when source=layer"},
        },
    )
    def comfy_bind_canvas(self, params):
        parent = self.parent
        source = params.get("source", "active")
        layer_name = params.get("layer_name")
        if source == "layer" and not layer_name:
            return {"success": False,
                    "message": "source=layer requires --layer_name"}
        parent._canvas_source = source
        parent._canvas_layer = layer_name
        return {"success": True,
                "message": f"Canvas source: {source}"
                           + (f" ({layer_name})" if layer_name else "")}

    @command(
        category="comfyui",
        help_text="Choose what ETN_KritaSelection reads, and how the "
                  "selection region is cropped for the workflow.",
        args={
            "--source": {"type": "str", "default": "global",
                         "choices": ["global", "none", "layer"],
                         "help": "global = Krita's current selection; "
                                 "none = synthesize an all-selected mask; "
                                 "layer = a mask layer's alpha"},
            "--layer_name": {"type": "str", "required": False,
                             "help": "Required when source=layer"},
            "--context": {"type": "str", "default": "entire_image",
                          "choices": ["entire_image", "automatic",
                                      "mask_bounds"],
                          "help": "Crop region for the workflow's input"},
            "--padding": {"type": "int", "default": 0,
                          "help": "Extra pixels around the selection"},
        },
    )
    def comfy_bind_selection(self, params):
        parent = self.parent
        source = params.get("source", "global")
        layer_name = params.get("layer_name")
        if source == "layer" and not layer_name:
            return {"success": False,
                    "message": "source=layer requires --layer_name"}
        parent._selection_source = source
        parent._selection_layer = layer_name
        parent._selection_context = params.get("context", "entire_image")
        parent._selection_padding = int(params.get("padding", 0) or 0)
        return {
            "success": True,
            "message": f"Selection source: {source}"
                       + (f" ({layer_name})" if layer_name else "")
                       + f", context: {parent._selection_context}",
        }