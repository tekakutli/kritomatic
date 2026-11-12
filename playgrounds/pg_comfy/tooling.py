"""Backwards-compatibility shim for `pg_comfy.tooling`.

The real module is `kritomatic_shared.comfyui_tooling`, under
`<repo>/src/`. Kept here so that any caller importing
`pg_comfy.tooling` directly (rather than via the package's
`__init__.py` re-exports) continues to work after the migration.

New code should import from `pg_comfy` (the package) or from
`kritomatic_shared.comfyui_tooling` directly.

The sys.path bootstrap for `kritomatic_shared` lives in
`pg_comfy/__init__.py`. Python imports the parent package before the
submodule, so any import of `pg_comfy.tooling` has already run that
bootstrap by the time this module executes. No local bootstrap is
needed here.
"""

from kritomatic_shared.comfyui_tooling import (  # noqa: F401
    EncodedImage,
    KritaResources,
    WorkflowRequirements,
    ExpansionResult,
    ExpansionError,
    expand_custom,
    scan_requirements,
)

__all__ = [
    "EncodedImage",
    "KritaResources",
    "WorkflowRequirements",
    "ExpansionResult",
    "ExpansionError",
    "expand_custom",
    "scan_requirements",
]
