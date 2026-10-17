"""
Layer type classification shared by every part of the daemon that asks
"is this layer image-bearing or mask-only?".

Two callers:

  * `comfyui_krita.py`, which reads pixels out of the document. An
    image layer is one whose projection can be read as RGB(A); a mask
    layer is one whose pixelData is grayscale.

  * `panel/connection.py`, which classifies the reply to `list_layers`
    into the two buckets the docker's alias dropdowns offer.

Keeping the classification in one module means a future Krita version
that introduces a new layer type is a one-line edit here, not two
drifting copies of the same frozenset.
"""

IMAGE_LAYER_TYPES = frozenset({
    "paintlayer",
    "grouplayer",
    "filelayer",
    "vectorlayer",
    "clonelayer",
    "filllayer",
    "filterlayer",
})

MASK_LAYER_TYPES = frozenset({
    "transparencymask",
    "selectionmask",
})
