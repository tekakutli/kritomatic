"""
remote_panel_html.py — assemble the remote panel's HTML page.

The page uses pgkit's `page()` helper for the shell (head, shared
CSS, panel wrapper, status line, restore button, presence banner,
`<canvas>`).  The canvas is unused here and is hidden by the local
CSS in rp_base.py.  The panel body comes from rp_base.py; the script
is the concatenation of rp_client.py and rp_ui.py.
"""

import os
import sys

_HERE   = os.path.dirname(os.path.abspath(__file__))
_PARENT = os.path.dirname(_HERE)
for _p in (_HERE, _PARENT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from rp_base   import RP_CSS, RP_PANEL_BODY
from rp_client import RP_CLIENT_JS
from rp_ui     import RP_UI_JS

from pgkit.html_shell import page


def render():
    script = RP_CLIENT_JS + RP_UI_JS
    return page(
        title         = "Remote panel",
        panel_title   = "Remote panel",
        panel_body    = RP_PANEL_BODY,
        script        = script,
        extra_css     = RP_CSS,
        status_text   = "",
        boot_sync_fn  = "",
    )
