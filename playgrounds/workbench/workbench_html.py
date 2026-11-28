# workbench/workbench_html.py
import os
import sys

_HERE   = os.path.dirname(os.path.abspath(__file__))
_PARENT = os.path.dirname(_HERE)
for _p in (_HERE, _PARENT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from wb_base   import WB_CSS, WB_BODY
from wb_client import WB_CLIENT_JS
from pgruntime.hub_control   import HUB_CONTROL_JS
from pgruntime.hub_manage_ui import (
    HUB_MANAGE_UI_CSS, HUB_MANAGE_UI_BODY, HUB_MANAGE_UI_JS,
)


def render():
    return (
        "<!DOCTYPE html>\n<html lang=\"en\">\n<head>\n"
        "<meta charset=\"utf-8\">\n<title>Workbench</title>\n"
        "<style>\n"
        + HUB_MANAGE_UI_CSS + "\n"
        + WB_CSS + "\n"
        "</style>\n"
        "</head>\n<body>\n"
        + WB_BODY
        + HUB_MANAGE_UI_BODY
        + "<script>\n\"use strict\";\n"
        + HUB_CONTROL_JS   + "\n"
        + HUB_MANAGE_UI_JS + "\n"
        + WB_CLIENT_JS + "\n</script>\n"
        "</body>\n</html>\n"
    )
