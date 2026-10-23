"""SAM3 segmentation playground — entry point.

    python sam3_segment_playground.py

Writes sam3_segment.html, serves it at http://127.0.0.1:8773/, and
serves /modules/* from the module manifest. ComfyUI must be running on
127.0.0.1:8188; the playground checks on load and shows a clear status.
"""

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_PARENT = os.path.dirname(_HERE)
for _p in (_HERE, _PARENT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import sam3_segment_html
import sam3_server

from pgkit.server import serve as kit_serve


HTML_FILE    = "sam3_segment.html"
PORT         = 8773
OPEN_BROWSER = True


def main():
    html = sam3_segment_html.render()
    with open(HTML_FILE, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"Wrote {HTML_FILE}")
    print(f"  comfyui  : http://{sam3_server.COMFY_HOST}:{sam3_server.COMFY_PORT}")
    print(f"  workflow : {sam3_server.WORKFLOW_PATH}")
    print(f"  routes   : {', '.join(sorted(list(sam3_server.ROUTES) + list(sam3_server.RAW_ROUTES)))}")

    kit_serve(
        port           = PORT,
        open_browser   = OPEN_BROWSER,
        html_file      = HTML_FILE,
        routes         = sam3_server.ROUTES,
        raw_routes     = sam3_server.RAW_ROUTES,
        module_sources = sam3_segment_html.module_sources(),
    )


if __name__ == "__main__":
    main()
