"""Segment playground — entry point.

    python segment_playground.py

Writes segment.html, serves it at http://127.0.0.1:8773/, and
serves /modules/* from the module manifest.

Two ComfyUI workflows are expected:

  segment.json   Segment All (detection)
  sam2_refine.json    Run on a selected mask (refinement)

ComfyUI must be running on 127.0.0.1:8188. The playground checks on
load and shows status. If SAM2 isn't installed, refine falls back to
SAM3 (individual_masks: true), which supports additions but not
reliable negative-point shrinkage.
"""

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_PLAYGROUNDS = os.path.dirname(_HERE)

try:
    sys.path.remove(_HERE)
except ValueError:
    pass
if _PLAYGROUNDS not in sys.path:
    sys.path.insert(0, _PLAYGROUNDS)

from segment import segment_html, segment_server

from pgkit.server import serve as kit_serve


HTML_FILE    = "segment.html"
PORT         = 8773
OPEN_BROWSER = True


def main():
    html = segment_html.render()
    with open(HTML_FILE, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"Wrote {HTML_FILE}")
    print(f"  comfyui  : http://{segment_server.COMFY_HOST}:{segment_server.COMFY_PORT}")
    print(f"  segment  : {segment_server.WORKFLOW_SEGMENT_PATH}")
    print(f"  refine   : {segment_server.WORKFLOW_REFINE_PATH}")
    print(f"  routes   : {', '.join(sorted(list(segment_server.ROUTES) + list(segment_server.RAW_ROUTES)))}")

    kit_serve(
        port           = PORT,
        open_browser   = OPEN_BROWSER,
        html_file      = HTML_FILE,
        routes         = segment_server.ROUTES,
        raw_routes     = segment_server.RAW_ROUTES,
        module_sources = segment_html.module_sources(),
        hub_name       = "segment",
    )


if __name__ == "__main__":
    main()
