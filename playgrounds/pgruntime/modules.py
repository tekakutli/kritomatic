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

"""
pgruntime.modules — the module_sources entries for pgruntime's own JS.
"""

from .js_daemon_client import DAEMON_CLIENT_JS
from .js_constants     import CONSTANTS_JS
from .js_state_base    import STATE_BASE_JS
from .js_pan_zoom      import PAN_ZOOM_JS
from .js_chrome        import CHROME_JS
from .js_panel_fields  import PANEL_FIELDS_JS
from .js_state_sync    import STATE_SYNC_JS
from .js_autosync      import AUTOSYNC_JS


def pgruntime_modules() -> dict:
    return {
        "pgruntime/js/daemon_client.js":  DAEMON_CLIENT_JS,
        "pgruntime/js/constants.js":      CONSTANTS_JS,
        "pgruntime/js/state_base.js":     STATE_BASE_JS,
        "pgruntime/js/pan_zoom.js":       PAN_ZOOM_JS,
        "pgruntime/js/chrome.js":         CHROME_JS,
        "pgruntime/js/panel_fields.js":   PANEL_FIELDS_JS,
        "pgruntime/js/state_sync.js":     STATE_SYNC_JS,
        "pgruntime/js/autosync.js":       AUTOSYNC_JS,
    }
