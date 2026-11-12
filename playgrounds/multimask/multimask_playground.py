"""Multimask playground — entry point.

    python multimask_playground.py

Writes multimask.html, serves it at http://127.0.0.1:8776/.
The playground's state is served at /state for the
`MultiAreaConditioning` ComfyUI node."""

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

from multimask import multimask_html, multimask_server

from pgkit.server import serve as kit_serve


HTML_FILE    = "multimask.html"
PORT         = 8776
OPEN_BROWSER = True


def main():
    html = multimask_html.render()
    with open(HTML_FILE, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"Wrote {HTML_FILE}")
    print(f"  routes   : {', '.join(sorted(list(multimask_server.ROUTES) + list(multimask_server.RAW_ROUTES)))}")
    print(f"  state    : http://127.0.0.1:{PORT}/state")

    kit_serve(
        port           = PORT,
        open_browser   = OPEN_BROWSER,
        html_file      = HTML_FILE,
        routes         = multimask_server.ROUTES,
        raw_routes     = multimask_server.RAW_ROUTES,
        module_sources = multimask_html.module_sources(),
        hub_name       = "multimask",
    )


if __name__ == "__main__":
    main()