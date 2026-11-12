"""Daemon-side bridge to the shared UI→API workflow conversion.

The algorithm lives in `kritomatic_shared.comfy_convert`, under
`<repo>/src/`. The daemon does not use pip; it reaches that path by
prepending `<repo>/src/` to `sys.path`. The bootstrap is idempotent
and runs once when this module is first imported — which is lazily,
on the first UI-format `comfy_load`, so nothing pays for it at
startup and a broken `kritomatic_shared` install only breaks the
UI-format path (existing API-format loads are unaffected).

Callers use:

    from ..utils.comfy_convert import convert_if_ui, ConversionError

Note on hot-reload: `kritomatic_shared.*` is not in `reload_daemon`'s
drop list, so editing `convert.py` requires a full Krita restart, not
just a `reload_daemon`. That is intentional — the shared module is
not under `kritomatic_daemon.*` and does not participate in the
daemon's reload lifecycle. If that ever becomes a problem, add
`kritomatic_shared`, `kritomatic_shared.comfy_convert`, and
`kritomatic_shared.comfy_convert.convert` to the drop list in
`handlers/daemon.py::reload_daemon` — the sys.path bootstrap here
will re-run on the fresh import of this module.
"""

import sys
from pathlib import Path

# <repo>/kritomatic_daemon/utils/comfy_convert.py
#   .parent                → .../utils
#   .parent.parent         → .../kritomatic_daemon
#   .parent.parent.parent  → <repo>
_SRC = Path(__file__).resolve().parent.parent.parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from kritomatic_shared.comfy_convert import (  # noqa: E402
    ConversionError,
    convert_if_ui,
    is_ui_format,
)

__all__ = ["ConversionError", "convert_if_ui", "is_ui_format"]