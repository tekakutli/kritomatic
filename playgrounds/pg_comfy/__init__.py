"""Vendored ComfyUI client + tooling for the browser playgrounds.

`client.py` and `tooling.py` are copied verbatim from
kritomatic_daemon/utils/comfyui_client.py and comfyui_tooling.py
(attribution comment at the top of each). `image.py` is a
playground-only simplification of comfyui_krita.py, dropping every
Krita-specific path.

To update from upstream: copy the two files, verify the version at the
top of each, run the smoke test in README.

LAZY `image`
============
`pg_comfy.image` imports PIL (`from PIL import Image`), which is
~30 MB of RSS and ~500 ms of import time.  Playgrounds that never
touch image encoding — `board`, `cone`, `text_wall` — should not pay
that cost just because `pg_comfy` is somewhere on the import graph.
`image` is therefore not imported eagerly; `__getattr__` below
resolves it on first attribute access.  Direct submodule imports
(`from pg_comfy.image import ...`) continue to work through Python's
normal submodule import machinery, unaffected by this change.

The playgrounds that do need image encoding — `inpaint` and
`segment` — import `make_encoded_image` inside the specific request
handlers that use it, so the cost is deferred to the first
upload/import/segment/refine request rather than paid at module
load.  In grouped mode (`run_all.py`) a session that only uses
`board` and `text_wall` never loads PIL at all.
"""

from .client import (
    ComfyClient,
    ComfyError,
    ComfyTimeout,
    ComfyCancelled,
    GenerationResult,
    get_client,
    reset_client,
)
from .tooling import (
    EncodedImage,
    KritaResources,
    WorkflowRequirements,
    ExpansionResult,
    ExpansionError,
    expand_custom,
    scan_requirements,
)


def __getattr__(name):
    if name == "image":
        from . import image as _image
        return _image
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "ComfyClient",
    "ComfyError",
    "ComfyTimeout",
    "ComfyCancelled",
    "GenerationResult",
    "get_client",
    "reset_client",
    "EncodedImage",
    "KritaResources",
    "WorkflowRequirements",
    "ExpansionResult",
    "ExpansionError",
    "expand_custom",
    "scan_requirements",
    "image",
]
