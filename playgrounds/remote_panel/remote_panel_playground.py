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

The target is selectable at runtime.  The page remembers the last
URL it connected to (in localStorage) and shows a dropdown of the
four known playgrounds; you can also type any URL.

The panel is built from the playground's advertised state and
schema.  When the playground has been migrated to pgkit, the schema
message gives the panel proper widgets (number inputs, checkboxes,
enums).  Against an older playground that only sends values, the
panel falls back to inferring the widget type from each value.

The page does not proxy anything through its own server: the WebSocket
goes directly from the browser to the target playground's `/remote`
endpoint.  That means the target playground must have pgkit's
WebSocket server running, which all pair/cone/board/text_board
entries will once they are migrated.
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
    print(f"  default target: ws://127.0.0.1:8767/remote  (pair)")
    print(f"  other targets : cone 8766, board 8770, text_board 8771")

    if SERVE:
        print()
        kit_serve(
            port         = PORT,
            open_browser = OPEN_BROWSER,
            html_file    = HTML_FILE,
            routes       = {},
        )
    else:
        print(f"  open : file://{os.path.abspath(HTML_FILE)}")


if __name__ == "__main__":
    main()
