"""
remote_panel_html.py — assemble the remote panel's HTML page.

The remote panel is not a playground.  It has no canvas, no sidebar,
and no pgkit shell.  This module writes its own page: RP_CSS from
rp_base.py, RP_BODY from rp_base.py, and the concatenation of the
two JS modules.
"""

import os
import sys

_HERE   = os.path.dirname(os.path.abspath(__file__))
_PARENT = os.path.dirname(_HERE)
for _p in (_HERE, _PARENT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from rp_base   import RP_CSS, RP_BODY
from rp_client import RP_CLIENT_JS
from rp_ui     import RP_UI_JS


def render():
    return (
        "<!DOCTYPE html>\n"
        "<html lang=\"en\">\n"
        "<head>\n"
        "<meta charset=\"utf-8\">\n"
        "<title>Remote panel</title>\n"
        "<style>\n"
        + RP_CSS + "\n"
        "</style>\n"
        "</head>\n"
        "<body>\n"
        + RP_BODY + "\n"
        "<script>\n"
        '"use strict";\n'
        + RP_CLIENT_JS + "\n"
        + RP_UI_JS
        + "\n</script>\n"
        "</body>\n"
        "</html>\n"
    )
