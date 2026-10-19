"""
pgkit.modules — the module_sources entries for pgkit's own JS.

A project builds its `_MODULE_SOURCES` dict by starting from
`pgkit_modules()` and adding its own files:

    from pgkit.modules import pgkit_modules

    _MODULE_SOURCES = {
        **pgkit_modules(),
        "pg_core.js": CORE_JS,
        ...
    }

The keys are the URL paths *under* `/modules/` — so `"pgkit/shell.js"`
is served at `/modules/pgkit/shell.js`, matching the `import "..."`
paths from the project's boot module and panel files.
"""

from .panel_core    import PANEL_CORE_JS
from .remote_client import REMOTE_CLIENT_JS
from .shell         import SHELL_JS


def pgkit_modules() -> dict:
    return {
        "pgkit/panel_core.js":    PANEL_CORE_JS,
        "pgkit/remote_client.js": REMOTE_CLIENT_JS,
        "pgkit/shell.js":         SHELL_JS,
    }
