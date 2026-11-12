"""Backwards-compatibility shim for `pg_comfy.proxy_client`.

The real module is `kritomatic_shared.comfyui_proxy_client`. Kept here
so that any caller importing `pg_comfy.proxy_client` directly (rather
than via the package's `__init__.py` re-exports) continues to work
after the migration.

New code should import from `pg_comfy` (the package) or from
`kritomatic_shared.comfyui_proxy_client` directly.
"""

from kritomatic_shared.comfyui_proxy_client import (  # noqa: F401
    ProxyError,
    ProxyHTTPError,
    ProxyUnreachable,
    ProxyV2,
)

__all__ = [
    "ProxyError",
    "ProxyHTTPError",
    "ProxyUnreachable",
    "ProxyV2",
]
