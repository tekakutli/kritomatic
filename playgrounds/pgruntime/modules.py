"""
pgruntime.modules — the module_sources entries for pgruntime's own JS.

A playground builds its `_MODULE_SOURCES` dict by starting from
`pgkit_modules()`, blending in `pgruntime_modules()`, and adding its
own files:

    from pgkit.modules import pgkit_modules
    from pgruntime.modules import pgruntime_modules

    _MODULE_SOURCES = {
        **pgkit_modules(),
        **pgruntime_modules(),
        "pg_state.js": STATE_JS,
        "pg_view.js":  VIEW_JS,
        ...
    }

The keys are the URL paths *under* `/modules/` — so
`"pgruntime/daemon_client.js"` is served at
`/modules/pgruntime/daemon_client.js`, matching the import path from
a playground's `pg_krita_client.js`:

    import { makeKritaClient }
      from "./pgruntime/daemon_client.js";

The `/modules/` prefix is added by pgkit's server; pgruntime doesn't
need to know about it.
"""

from .js_daemon_client import DAEMON_CLIENT_JS


def pgruntime_modules() -> dict:
    return {
        "pgruntime/daemon_client.js": DAEMON_CLIENT_JS,
    }