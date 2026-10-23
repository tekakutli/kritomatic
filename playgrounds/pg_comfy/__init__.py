"""Vendored ComfyUI client + tooling for the SAM3 playground.

`client.py` and `tooling.py` are copied verbatim from
kritomatic_daemon/utils/comfyui_client.py and comfyui_tooling.py
(attribution comment at the top of each). `image.py` is a
playground-only simplification of comfyui_krita.py, dropping every
Krita-specific path.

To update from upstream: copy the two files, verify the version at the
top of each, run the smoke test in README.
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
from . import image

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
