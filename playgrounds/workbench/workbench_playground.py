# workbench/workbench_playground.py
import os, sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_PARENT = os.path.dirname(_HERE)
for _p in (_HERE, _PARENT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import workbench_html
from pgruntime.ports import port_for
from pgkit.server import serve as kit_serve

HTML_FILE = "workbench.html"
PORT      = port_for("workbench")

def main():
    with open(HTML_FILE, "w", encoding="utf-8") as f:
        f.write(workbench_html.render())
    print(f"Wrote {HTML_FILE}")
    print(f"  open : http://127.0.0.1:{PORT}/{HTML_FILE}")
    kit_serve(
        port         = PORT,
        open_browser = True,
        html_file    = HTML_FILE,
        routes       = {},
        hub_name     = "workbench",
        hub_role     = "panel",   # <-- key: not a playground, not killable
    )

if __name__ == "__main__":
    main()