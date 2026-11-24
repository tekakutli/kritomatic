import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_PLAYGROUNDS = os.path.dirname(_HERE)
if _PLAYGROUNDS not in sys.path:
    sys.path.insert(0, _PLAYGROUNDS)

from pgruntime.ports import ws_url_for
from pgkit.remote_cli_client import RemoteCliClient

with RemoteCliClient(ws_url_for("segment", "/remote")) as client:
    client.bundle(
        set={
            "settings.threshold":        0.55,
            "settings.refineIterations": 2,
            "prompt.text":               "hair, woman",
        },
        actions=[
            {"kind": "action.segmentAll"},
            {"kind": "action.run"},
        ],
        wait_for_running=True,
    )
    for m in client.entities()["masks"]["items"]:
        print(m["label"])