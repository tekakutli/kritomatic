from pgkit.remote_cli_client import RemoteCliClient

with RemoteCliClient("ws://127.0.0.1:8773/remote") as client:
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
