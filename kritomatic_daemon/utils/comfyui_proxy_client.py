"""Daemon-side bridge to the shared Comfy API v2 client.

The logic lives in `kritomatic_shared.comfyui_proxy_client`, under
`<repo>/src/`. This module re-exports the pre-migration public
surface so every daemon import site keeps working unchanged.

`from_pid_file()` now probes `/api/v2/health` before returning, so
callers get the same fail-fast `ProxyUnreachable` the daemon used to
get from `manager.status()`, without this module importing
`..proxy.manager`.
"""

import sys
from pathlib import Path

# <repo>/kritomatic_daemon/utils/comfyui_proxy_client.py
#   .parent                → .../utils
#   .parent.parent         → .../kritomatic_daemon
#   .parent.parent.parent  → <repo>
_SRC = Path(__file__).resolve().parent.parent.parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from kritomatic_shared.comfyui_proxy_client import (  # noqa: E402,F401
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
