"""Shared ComfyUI client + tooling for the browser playgrounds.

Four modules now live in `kritomatic_shared`, under `<repo>/src/`, so
the daemon and the playgrounds share one copy of each:

    comfyui_client.py        HTTP + WebSocket client  (setup.py dep:
                             websockets>=12, needed by generate*)
    comfyui_proxy_client.py  Comfy API v2 client       (pure stdlib)
    comfyui_tooling.py       ETN_* rewrite, expand_custom  (pure stdlib)
    comfyui_inspect.py       native-v2 slot extractors (pure stdlib)
    comfyui_substitute.py    resource + param substitution (pure stdlib)

This package's `client.py`, `proxy_client.py`, and `tooling.py` are
now backwards-compatibility shims that re-export from shared.

`image.py` is a playground-only simplification of the daemon's
`comfyui_krita.py` — PIL-based instead of Qt-based. Not shared, not
duplicated content.

Shared-client bootstrap
=======================
`kritomatic_shared` is at `<repo>/src/kritomatic_shared/`. The
playgrounds are launched from `playgrounds/` and are not installed
into any venv, so this package has to reach into the repo to find
`shared`. `_find_repo_src()` walks up from `__file__` looking for
`<candidate>/src/kritomatic`, the same pattern
`playgrounds/board/board_krita.py` uses.
"""

from pathlib import Path
import sys


def _find_repo_src() -> Path:
    p = Path(__file__).resolve().parent
    for _ in range(10):
        cand = p / "src"
        if (cand / "kritomatic").is_dir():
            return cand
        if p == p.parent:
            break
        p = p.parent
    raise RuntimeError(
        "Could not locate src/kritomatic from "
        + str(Path(__file__).resolve())
    )


_SRC = _find_repo_src()
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))


from kritomatic_shared.comfyui_client import (  # noqa: E402
    ComfyClient,
    ComfyError,
    ComfyTimeout,
    ComfyCancelled,
    GenerationResult,
    get_client,
    reset_client,
)
from kritomatic_shared.comfyui_proxy_client import (  # noqa: E402
    ProxyV2,
    ProxyError,
    ProxyHTTPError,
    ProxyUnreachable,
)
from kritomatic_shared.comfyui_tooling import (  # noqa: E402
    EncodedImage,
    KritaResources,
    WorkflowRequirements,
    ExpansionResult,
    ExpansionError,
    expand_custom,
    scan_requirements,
)
from kritomatic_shared.comfyui_inspect import (  # noqa: E402
    extract_resource_slots,
    extract_param_slots,
    extract_output_nodes,
    extract_all_slots,
)
from kritomatic_shared.comfyui_substitute import (  # noqa: E402
    substitute_into_graph,
)


# ============================================================================
#  Playground extensions
# ============================================================================

import time as _time

_OBJECT_INFO_DEFAULT_TTL = 30.0


def cached_object_info(client, ttl=_OBJECT_INFO_DEFAULT_TTL):
    """Return `client.object_info()`, cached on the client for `ttl` s.

    ComfyUI's `/object_info` endpoint returns a ~200 KB JSON document
    listing every installed custom node and its schema. It changes only
    when ComfyUI restarts or a custom node is installed, so caching it
    on the client (which is process-wide via `get_client()`) removes
    most of the cost without any staleness risk: `reset_client()` drops
    the client and the cache together.
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
    "extract_resource_slots",
    "extract_param_slots",
    "extract_output_nodes",
    "extract_all_slots",
    "substitute_into_graph",
    "cached_object_info",
    "image",
    "ProxyV2",
    "ProxyError",
    "ProxyHTTPError",
    "ProxyUnreachable",
]
