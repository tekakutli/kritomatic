"""
remote_panel_playground.py — a browser-based remote control panel.

    python remote_panel_playground.py

A standalone web page that connects to any pgkit playground over its
WebSocket interface and drives it from a separate browser tab.

The intended use is to keep a playground running in one tab while
its panel is hidden — the pgkit presence protocol hides the local
panel whenever a `remote` client is connected, and this page is that
`remote` client — and to manipulate the playground's values from
this page instead.

Under the pgkit hub, `remote_panel` registers with `role="panel"`.
That makes the hub refuse to kill it cooperatively, and it makes the
panel's own target dropdown skip it (only `role="playground"` entries
appear as targets). See pgkit/hub_client.py's docstring.

The page does not proxy anything through its own server: the
WebSocket goes directly from the browser to the target playground's
`/remote` endpoint (via the hub if running). That means the target
playground must have pgkit's WebSocket server running, which all
entries do.

TARGETS ARE DISCOVERED, NOT LISTED
==================================
The startup banner below names only the hub URL. There is no
hardcoded list of playgrounds or their ports — the hub's registry is
the source of truth, and the panel's dropdown reflects it. The user
sees their live playground set by opening the panel, not by reading
a stale console line.
"""

import os
import sys

_HERE   = os.path.dirname(os.path.abspath(__file__))
_PARENT = os.path.dirname(_HERE)
for _p in (_HERE, _PARENT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import remote_panel_html

from pgkit.server import serve as kit_serve


HTML_FILE    = "remote_panel.html"
PORT         = 8772
SERVE        = True
OPEN_BROWSER = True


def main():
    html = remote_panel_html.render()
    with open(HTML_FILE, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"Wrote {HTML_FILE}")
    print(f"  hub           : ws://127.0.0.1:8780  "
          f"(registered targets appear as playgrounds start)")

    if SERVE:
        print()
        kit_serve(
            port           = PORT,
            open_browser   = OPEN_BROWSER,
            html_file      = HTML_FILE,
            routes         = {},
            hub_name       = "remote_panel",
            hub_role       = "panel",
        )
    else:
        print(f"  open : file://{os.path.abspath(HTML_FILE)}")


if __name__ == "__main__":
    main()
