"""Commands for managing the comfy-api-proxy child process.

The proxy is the only surface the daemon uses to reach ComfyUI after
the tooling-nodes migration (see proxy/manager.py). Its lifecycle is
exposed as commands because first-time setup is slow (venv + pip,
~30s) and because operators want to inspect the log when something goes
wrong.

`comfy_proxy_install` is asynchronous on purpose: the CLI's command
transport has a ~2s read timeout, and a synchronous 30s pip install
would just time out client-side. It returns immediately with
`state: "installing"`; `comfy_proxy_status` reports progress and the
final outcome.

`comfy_proxy_health` and `comfy_proxy_test` are diagnostics. The
former is a thin wrapper around GET /api/v2/health; the latter runs
the full round trip — upload an asset, submit a LoadImage→SaveImage
workflow referencing it via core/ASSET, poll to terminal — so the
transport can be verified from the CLI without opening the Scripter
or starting a separate Python process.

comfy_connect auto-starts the proxy if it isn't running (see
comfyui_connection.py). These explicit commands are for setup,
inspection, and recovery.

External-job registration
=========================
`comfy_register_external_job` / `comfy_unregister_external_job` let a
client that submits jobs via ProxyV2 directly — currently the inpaint
playground — announce the job to the daemon so:

  * `comfy_status`'s `active_batches` reflects it, and
  * `comfy_cancel` can reach it via `ProxyV2.cancel_job`.

The daemon does not take over the job's lifecycle: it stores the
proxy job id, and on cancel routes to the proxy, nothing more. The
client keeps polling the proxy itself and sees the `canceled` status
on its own next `get_job` call.
"""

from __future__ import annotations

from ..decorators import command, AutoDispatchHandler
from ..proxy import manager
from .comfyui_types import _BatchRecord


class ProxyCommands(AutoDispatchHandler):

    def __init__(self, parent):
        self.parent = parent
        super().__init__()

    @command(
        category="comfyui",
        help_text="Create the dedicated venv and pip-install the pinned "
                  "comfy-api-proxy version. Returns immediately; poll "
                  "comfy_proxy_status for install.state == 'done'. "
                  "Slow (~30s) and one-time. Requires a Python 3.10+ on "
                  "PATH; if none is found, install the proxy manually "
                  "with `pipx install comfy-api-proxy` and run "
                  "comfy_proxy_start (which finds the pipx binary on "
                  "PATH).",
        args={
            "--force": {
                "type": "bool", "default": False,
                "help": "Reinstall even if already present.",
            },
        },
    )
    def comfy_proxy_install(self, params):
        return manager.install_async(force=bool(params.get("force", False)))

    @command(
        category="comfyui",
        help_text="Spawn the proxy in the background and wait for "
                  "/api/v2/health. Idempotent when an existing proxy "
                  "already points at the same ComfyUI.",
        args={
            "--host": {"type": "str", "default": "127.0.0.1",
                       "help": "Address to bind the proxy on."},
            "--port": {"type": "int", "default": 8189,
                       "help": "Port to serve the v2 API on."},
            "--comfyui": {"type": "str",
                          "default": "http://127.0.0.1:8188",
                          "help": "Base URL of the ComfyUI to proxy."},
            "--comfyui-base-dir": {"type": "str", "required": False,
                                   "help": "Filesystem root of a "
                                           "co-located ComfyUI install; "
                                           "enables direct model-directory "
                                           "placement. Off when omitted."},
            "--token": {"type": "str", "required": False,
                        "help": "Optional static bearer token that the "
                                "proxy will require on /api/v2/*. Off "
                                "when omitted (loopback-only default)."},
        },
    )
    def comfy_proxy_start(self, params):
        return manager.start(
            host=params.get("host", "127.0.0.1"),
            port=int(params.get("port", 8189)),
            comfyui=params.get("comfyui", "http://127.0.0.1:8188"),
            comfyui_base_dir=params.get("comfyui_base_dir"),
            token=params.get("token"),
        )

    @command(
        category="comfyui",
        help_text="Stop the background proxy.",
        args={},
    )
    def comfy_proxy_stop(self, params):
        return manager.stop()

    @command(
        category="comfyui",
        help_text="Report proxy status: install progress, running, "
                  "healthy, pid, host, port, and the ComfyUI it points "
                  "at. Poll this after comfy_proxy_install to learn "
                  "when the install finishes and whether it succeeded.",
        args={},
    )
    def comfy_proxy_status(self, params):
        return manager.status()

    @command(
        category="comfyui",
        help_text="Tail the proxy log.",
        args={
            "--lines": {"type": "int", "default": 100,
                        "help": "How many trailing lines to return."},
        },
    )
    def comfy_proxy_logs(self, params):
        return manager.logs(lines=int(params.get("lines", 100)))

    @command(
        category="comfyui",
        help_text="Stop the proxy and remove its venv. Use before "
                  "reinstalling under a different pinned version.",
        args={},
    )
    def comfy_proxy_uninstall(self, params):
        return manager.uninstall()

    # ------------------------------------------------------------------
    #  Diagnostics
    # ------------------------------------------------------------------

    @command(
        category="comfyui",
        help_text="Call GET /api/v2/health from the daemon's own "
                  "ProxyV2 client. Confirms the client module loads "
                  "and the proxy answers. Returns the health dict.",
        args={},
    )
    def comfy_proxy_health(self, params):
        from ..utils.comfyui_proxy_client import ProxyV2, ProxyUnreachable
        try:
            client = ProxyV2.from_pid_file()
        except (ProxyUnreachable, ImportError) as e:
            return {"success": False, "message": str(e)}
        try:
            result = client.health()
        except ProxyUnreachable as e:
            return {"success": False, "message": str(e)}
        return {
            "success": True,
            "message": "proxy healthy",
            "data": {"base_url": client.base_url, "health": result},
        }

    @command(
        category="comfyui",
        help_text="Smoke-test the v2 transport end to end: upload a "
                  "tiny PNG as an asset, submit a LoadImage→SaveImage "
                  "workflow that references it via core/ASSET, poll "
                  "until terminal, and report the outcome. Fails "
                  "loudly on any transport error with the proxy's "
                  "error code.",
        args={
            "--timeout": {"type": "float", "default": 30.0,
                          "help": "Seconds to poll for terminal state."},
        },
    )
    def comfy_proxy_test(self, params):
        import base64
        import time
        from ..utils.comfyui_proxy_client import (
            ProxyV2, ProxyHTTPError, ProxyUnreachable,
        )
        try:
            client = ProxyV2.from_pid_file()
        except (ProxyUnreachable, ImportError) as e:
            return {"success": False, "message": str(e)}

        try:
            timeout = float(params.get("timeout", 30.0))
        except (TypeError, ValueError):
            timeout = 30.0

        # 1x1 red PNG — enough bytes to exercise multipart + dedup.
        png = base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADUlEQVR4nGP4"
            "z8DwnwAEAQH/m7jBnQAAAABJRU5ErkJggg=="
        )

        steps: dict = {}
        try:
            asset = client.upload_asset(png, "input/_kritomatic_test.png",
                                        "image/png")
            steps["upload"] = {"id": asset["id"],
                               "hash": asset.get("hash")}
        except ProxyHTTPError as e:
            return {"success": False,
                    "message": f"upload failed: {e}",
                    "data": {"steps": steps,
                             "status": e.status, "code": e.code}}

        workflow = {
            "1": {
                "class_type": "LoadImage",
                "inputs": {
                    "image": {"__type": "core/ASSET",
                              "info": {"id": asset["id"]}},
                },
            },
            "2": {
                "class_type": "SaveImage",
                "inputs": {
                    "filename_prefix": "_kritomatic_test",
                    "images": ["1", 0],
                },
            },
        }
        try:
            job = client.submit_job(workflow)
            steps["submit"] = {"id": job["id"], "status": job["status"]}
        except ProxyHTTPError as e:
            return {"success": False,
                    "message": f"submit failed: {e}",
                    "data": {"steps": steps,
                             "status": e.status, "code": e.code,
                             "details": e.details}}

        deadline = time.monotonic() + timeout
        final = job
        while time.monotonic() < deadline:
            try:
                final = client.get_job(job["id"])
            except ProxyHTTPError as e:
                return {"success": False,
                        "message": f"poll failed: {e}",
                        "data": {"steps": steps,
                                 "status": e.status, "code": e.code}}
            if final["status"] in ("succeeded", "failed", "canceled",
                                   "expired"):
                break
            time.sleep(0.25)

        status = final["status"]
        steps["terminal"] = {
            "status": status,
            "outputs": len(final.get("outputs") or []),
        }

        if status == "succeeded":
            return {
                "success": True,
                "message": (
                    f"transport OK — job succeeded with "
                    f"{len(final.get('outputs') or [])} output(s)"
                ),
                "data": {"steps": steps, "final_status": status},
            }
        if status == "failed":
            err = final.get("error") or {}
            return {
                "success": True,
                "message": (
                    f"transport OK — job failed at execution: "
                    f"{err.get('message') or '(no message)'}"
                ),
                "data": {"steps": steps, "final_status": status,
                         "error": err},
            }
        return {
            "success": False,
            "message": (
                f"transport OK but job did not terminate within "
                f"{timeout}s (status: {status})"
            ),
            "data": {"steps": steps, "final_status": status},
        }

    # ------------------------------------------------------------------
    #  External-job registration
    # ------------------------------------------------------------------
    #
    # A client that submits jobs via ProxyV2 directly (currently the
    # inpaint playground, which moved off the daemon's comfy_generate
    # in Stage 8) announces each job with `comfy_register_external_job`
    # and removes it with `comfy_unregister_external_job`. The daemon
    # creates a normal `_BatchRecord`, stores the proxy job id under
    # `external_job_ids`, and does nothing else. `comfy_status` picks
    # the record up automatically because `_batches` is what it walks.
    # `comfy_cancel` sees `external_job_ids` and routes to
    # `ProxyV2.cancel_job` instead of setting `cancel_event` (there is
    # no local poll loop for the daemon to interrupt — the client has
    # its own).

    @command(
        category="comfyui",
        help_text="Register a proxy-submitted job with the daemon so "
                  "comfy_status's active_batches reflects it and "
                  "comfy_cancel can reach it. The daemon does not take "
                  "over the job's lifecycle — it just records the id "
                  "and forwards cancels. Callers must pair every "
                  "register with a matching unregister when the job "
                  "reaches a terminal state.",
        args={
            "--batch_id": {"type": "str", "required": True,
                           "help": "Client-chosen batch id. Must be "
                                   "unique among active batches."},
            "--job_id":   {"type": "str", "required": True,
                           "help": "Proxy job id (from submit_job)."},
            "--workflow": {"type": "str", "required": False,
                           "help": "Workflow name for display in "
                                   "comfy_batches. Defaults to "
                                   "'(external)'."},
        },
    )
    def comfy_register_external_job(self, params):
        parent = self.parent
        batch_id = params.get("batch_id") or ""
        job_id = params.get("job_id") or ""
        if not batch_id or not job_id:
            return {"success": False,
                    "message": "batch_id and job_id are required"}

        record = _BatchRecord(
            batch_id=batch_id,
            workflow_name=(params.get("workflow") or "(external)"),
            requested_count=1,
            total_runs=1,
            base_seed=0,
            output_name=None,
        )
        record.external_job_ids = [job_id]
        record.current_phase = "running"

        with parent._batches_lock:
            parent._batches[batch_id] = record

        return {
            "success": True,
            "message": f"registered external job {job_id} as {batch_id}",
            "data": {"batch_id": batch_id, "job_id": job_id},
        }

    @command(
        category="comfyui",
        help_text="Remove an external job's registration. Safe to call "
                  "whether or not the batch is still present.",
        args={
            "--batch_id": {"type": "str", "required": True,
                           "help": "Batch id previously passed to "
                                   "comfy_register_external_job."},
        },
    )
    def comfy_unregister_external_job(self, params):
        parent = self.parent
        batch_id = params.get("batch_id") or ""
        if not batch_id:
            return {"success": False, "message": "batch_id is required"}

        with parent._batches_lock:
            parent._batches.pop(batch_id, None)

        return {"success": True, "message": "unregistered",
                "data": {"batch_id": batch_id}}