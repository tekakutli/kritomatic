"""Interceptor playground — entry point.

    python interceptor_playground.py

Writes interceptor.html, serves it at http://127.0.0.1:8775/.

The interceptor receives tasks from another playground (by default,
the inpaint playground's "Route via interceptor" mode), lets the user
inspect the incoming canvas and edit the prompt, and on button click
runs the real inpaint — `inpaint_server.handle_inpaint` — then POSTs
the result back to the source's callback URL.
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

from interceptor import interceptor_html, interceptor_server

from pgkit.server import serve as kit_serve


HTML_FILE    = "interceptor.html"
PORT         = 8775
OPEN_BROWSER = True


def main():
    html = interceptor_html.render()
    with open(HTML_FILE, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"Wrote {HTML_FILE}")
    print(f"  routes   : {', '.join(sorted(interceptor_server.ROUTES))}")

    kit_serve(
        port           = PORT,
        open_browser   = OPEN_BROWSER,
        html_file      = HTML_FILE,
        routes         = interceptor_server.ROUTES,
        raw_routes     = {},
        module_sources = interceptor_html.module_sources(),
        hub_name       = "interceptor",
    )


if __name__ == "__main__":
    main()