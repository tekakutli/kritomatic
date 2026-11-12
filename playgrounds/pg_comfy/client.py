"""Backwards-compatibility shim for `pg_comfy.client`.

The real module is `kritomatic_shared.comfyui_client`. Kept here so
that any caller importing `pg_comfy.client` directly (rather than via
the package's `__init__.py` re-exports) continues to work after the
migration.

New code should import from `pg_comfy` (the package) or from
`kritomatic_shared.comfyui_client` directly.
"""

from kritomatic_shared.comfyui_client import (  # noqa: F401
    CANCELLED,
    ComfyCancelled,
    ComfyClient,
    ComfyError,
    ComfyTimeout,
    GenerationResult,
    JobInfo,
    TextOutput,
    decode_preview_frame,
    get_client,
    reset_client,
)

__all__ = [
    "CANCELLED",
    "ComfyCancelled",
    "ComfyClient",
    "ComfyError",
    "ComfyTimeout",
    "GenerationResult",
    "JobInfo",
    "TextOutput",
    "decode_preview_frame",
    "get_client",
    "reset_client",
]
