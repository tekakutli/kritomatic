"""Vendored ComfyUI client + tooling for the browser playgrounds.

`client.py` and `tooling.py` are copied verbatim from
kritomatic_daemon/utils/comfyui_client.py and comfyui_tooling.py.
`image.py` is a playground-only simplification of comfyui_krita.py.
`proxy_client.py` is a copy of kritomatic_daemon/utils/comfyui_proxy_client.py
(Comfy API v2 client), used by the inpaint playground to talk to
comfy-api-proxy directly.

To update from upstream: copy the files, verify the version at the top
of each, run the smoke test in README. Two things in this file are
**playground extensions**, both documented below and both independent
of the upstream copies:

    LAZY `image`              defers PIL until first use
    cached_object_info(...)   30 s TTL cache around client.object_info()

LAZY `image`
============
`pg_comfy.image` imports PIL (`from PIL import Image`), which is
~30 MB of RSS and ~500 ms of import time.  Playgrounds that never
touch image encoding — `board`, `cone`, `text_wall` — should not pay
that cost just because `pg_comfy` is somewhere on the import graph.
`image` is therefore not imported eagerly; `__getattr__` below
resolves it on first attribute access.  Direct submodule imports
(`from pg_comfy.image import ...`) continue to work through Python's
normal submodule import machinery.

The playgrounds that do need image encoding — `inpaint` and
`segment` — import `make_encoded_image` inside the specific request
handlers that use it, so the cost is deferred to the first
upload/import/segment/refine request rather than paid at module
load.  In grouped mode (`run_all.py`) a session that only uses
`board` and `text_wall` never loads PIL at all.

OBJECT_INFO CACHE
=================
ComfyUI's `/object_info` endpoint returns a ~200 KB JSON document
listing every installed custom node and its schema.  It changes only
when ComfyUI restarts or a custom node is installed.  `segment_server`
calls it twice per Segment All or Refine cycle (once to validate node
existence, once inside `_run_workflow` to expand the graph), and once
per status check.

`cached_object_info(client, ttl=30.0)` returns
`client.object_info()`, cached on the client instance for `ttl`
seconds.  The cache lives on the client, not in a module-global:
`get_client()` returns a process-wide singleton per (host, port), so
the effect is process-wide, but `reset_client()` drops the client
(and the cache with it), so a ComfyUI restart — which always produces
a fresh client on the next request — cannot hit a stale cache.

No lock is needed: the cache read is a plain attribute access and the
write is an atomic reference assignment under the GIL.  Two threads
racing on a miss both fetch; last write wins; correctness is
preserved.

PROXY V2 CLIENT
===============
`proxy_client.py` is a byte-for-byte copy of the daemon's
`ProxyV2` client (the HTTP surface for comfy-api-proxy on 8189). It
is re-exported here for consistency with the other `pg_comfy` symbols.

Live consumers: `inpaint_server.handle_inpaint` (from Stage 8
onwards).  `segment` still uses `expand_custom` from `tooling.py` —
its migration to the v2 proxy is out of scope for this stage.
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
from .proxy_client import (
    ProxyV2,
    ProxyError,
    ProxyHTTPError,
    ProxyUnreachable,
)


# ============================================================================
#  Playground extensions
# ============================================================================

import time as _time

_OBJECT_INFO_DEFAULT_TTL = 30.0


def cached_object_info(client, ttl=_OBJECT_INFO_DEFAULT_TTL):
    """Return `client.object_info()`, cached on the client for `ttl` s.

    See the module docstring's OBJECT_INFO CACHE note.  The client is
    the natural cache holder: `get_client()` returns a process-wide
    singleton, and `reset_client()` drops it, so a ComfyUI restart
    always forces a refetch.
    """
    cached = getattr(client, "_object_info_cache", None)
    now = _time.monotonic()
    if cached is not None and (now - cached["at"]) < ttl:
        return cached["data"]
    data = client.object_info()
    client._object_info_cache = {"at": now, "data": data}
    return data


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
    "cached_object_info",
    "image",
    "ProxyV2",
    "ProxyError",
    "ProxyHTTPError",
    "ProxyUnreachable",
]
