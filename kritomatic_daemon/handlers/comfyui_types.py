"""Stable types and constants for the ComfyUI handler.

Data shapes and path constants used across the handler tree:

  * `_BatchRecord` — live state of one `comfy_generate` call, or one
    externally-registered proxy job (playground inpaint).
  * `InterceptedBatch` — a prepared-but-not-yet-submitted workflow
    waiting in the interceptor playground for user review.
  * Path constants (`_WORKFLOW_DIR`, `_HISTORY_DIR`,
    `_RECENT_ALIASES_PATH`, `_INTERCEPT_CONFIG_PATH`).
  * Recent-alias and intercept-config persistence helpers — tiny,
    best-effort, swallowed.

Nothing here mutates any state the handler tree owns; nothing here
imports from the handler tree.
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Optional


# ============================================================================
#  Constants
# ============================================================================

_RECENT_ALIASES_CAP = 50

# How long an InterceptedBatch survives between intake and resume.
# Matches the old interceptor playground's GC window.  After this, the
# daemon silently drops the record; a late `comfy_resume_intercepted`
# returns "batch expired or unknown".
INTERCEPT_TIMEOUT_SEC = 900

_DEFAULT_INTERCEPT_URL = "http://127.0.0.1:8775/intercept/intake"


_READ_ONLY_COMMANDS = frozenset({
    "comfy_status",
    "comfy_batches",
    "comfy_list_workflows",
    "comfy_history",
    "comfy_list_recent_aliases",
    "comfy_list_intercepted",
    # Proxy diagnostics: read-only, must not trigger a state publish
    # (which would re-probe the proxy on every poll).
    "comfy_proxy_status",
    "comfy_proxy_logs",
    "comfy_proxy_health",
    "comfy_proxy_test",
    # Preparation uploads to the proxy but does not mutate handler
    # state; it is a diagnostic, not a state change.
    "comfy_prepare_only",
})


# ============================================================================
#  Paths
# ============================================================================

_HANDLER_DIR = Path(__file__).resolve().parent.parent
_REPO_ROOT = _HANDLER_DIR.parent
_PANEL_DIR = _REPO_ROOT / "data" / "comfy_panel"
_WORKFLOW_DIR = _PANEL_DIR / "workflows"
_HISTORY_DIR = _PANEL_DIR / "history"
_RECENT_ALIASES_PATH = _PANEL_DIR / "recent_aliases.json"
_INTERCEPT_CONFIG_PATH = _PANEL_DIR / "intercept.json"

_WORKFLOW_DIR.mkdir(parents=True, exist_ok=True)
_HISTORY_DIR.mkdir(parents=True, exist_ok=True)


# ============================================================================
#  Recent-alias persistence
# ============================================================================

def _load_recent_aliases() -> list:
    try:
        if not _RECENT_ALIASES_PATH.is_file():
            return []
        data = json.loads(_RECENT_ALIASES_PATH.read_text(encoding="utf-8"))
        aliases = data.get("recent_aliases")
        if isinstance(aliases, list):
            return [str(a) for a in aliases if isinstance(a, str)][
                :_RECENT_ALIASES_CAP
            ]
    except Exception:
        pass
    return []


def _save_recent_aliases(aliases: list) -> None:
    try:
        _RECENT_ALIASES_PATH.parent.mkdir(parents=True, exist_ok=True)
        _RECENT_ALIASES_PATH.write_text(
            json.dumps(
                {"version": 1, "recent_aliases": list(aliases)},
                indent=2,
            ),
            encoding="utf-8",
        )
    except Exception:
        pass


# ============================================================================
#  Intercept-config persistence
# ============================================================================

def _load_intercept_config() -> dict:
    """Read `data/comfy_panel/intercept.json`, or return defaults.

    Shape: `{"enabled": bool, "url": str}`.  A corrupt or missing file
    produces the defaults (intercept off, default interceptor URL), so
    the daemon is never left in a half-configured state by a
    half-written config file.
    """
    default = {
        "enabled": False,
        "url": _DEFAULT_INTERCEPT_URL,
    }
    try:
        if not _INTERCEPT_CONFIG_PATH.is_file():
            return default
        data = json.loads(
            _INTERCEPT_CONFIG_PATH.read_text(encoding="utf-8")
        )
        if not isinstance(data, dict):
            return default
        return {
            "enabled": bool(data.get("enabled", False)),
            "url": str(data.get("url") or default["url"]),
        }
    except Exception:
        return default


def _save_intercept_config(enabled: bool, url: str) -> None:
    """Persist the intercept toggle + URL.  Best-effort, swallowed."""
    try:
        _INTERCEPT_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        _INTERCEPT_CONFIG_PATH.write_text(
            json.dumps(
                {
                    "version": 1,
                    "enabled": bool(enabled),
                    "url": str(url or _DEFAULT_INTERCEPT_URL),
                },
                indent=2,
            ),
            encoding="utf-8",
        )
    except Exception:
        pass


# ============================================================================
#  Data shapes
# ============================================================================

class _BatchRecord:
    """Live state of one batch.

    Held in `ComfyUIHandler._batches`, keyed by batch id, from the
    moment the batch starts until the moment it returns.

    Two kinds of batch are tracked:

      * **Internal** — one `comfy_generate` call. `job_ids` is the list
        of proxy job ids this call submitted. `cancel_event` is the
        signal the poll loop watches; `comfy_cancel` sets it.

      * **External** — one proxy job submitted directly by a client
        (currently the inpaint playground), via
        `comfy_register_external_job`. The daemon did not submit it and
        is not polling it. `external_job_ids` holds the proxy job id(s)
        the client registered; `comfy_cancel` cancels those via
        `ProxyV2.cancel_job` instead of setting `cancel_event`
        (the client owns its own poll loop and will see the proxy's
        `canceled` status without any cooperation from the daemon).

    A record is one kind or the other. `job_ids` being non-empty and
    `external_job_ids` being non-empty never overlap in practice, but
    the code checks `external_job_ids` first because that is the
    stronger signal — a record with external ids was created by the
    registration path and its `cancel_event` is unused.

    `_cancel_sent` — set of job ids for which `cancel_job` has already
    been called from an internal batch's poll loop. Prevents re-sending
    cancel on every iteration after `cancel_event` is set.
    """

    def __init__(self, batch_id: str, workflow_name: str,
                 requested_count: int, total_runs: int,
                 base_seed: int, output_name: Optional[str]):
        self.batch_id = batch_id
        self.workflow_name = workflow_name
        self.requested_count = requested_count
        self.total_runs = total_runs
        self.base_seed = base_seed
        self.output_name = output_name
        self.started_at = time.time()
        self.cancel_event = threading.Event()

        self.current_phase = "preparing"
        self.current_run_index = 0
        self.run_records: list = []

        self.job_ids: list = []
        self._cancel_sent: set = set()

        # Populated only by `comfy_register_external_job`. A record
        # with a non-empty list was submitted by a client talking to
        # the proxy directly; the daemon tracks it so `comfy_status`'s
        # `active_batches` and `comfy_cancel` can see it, but does not
        # own its lifecycle.
        self.external_job_ids: list = []


class InterceptedBatch:
    """One prepared workflow waiting in the interceptor playground.

    Created by `comfy_generate` in intercept mode after
    `prepare_workflow` has run.  Held in
    `ComfyUIHandler._intercepted_batches` until either:

      * `comfy_resume_intercepted` pops it and runs the
        submit/poll/download/paste half of generation, or
      * `comfy_discard_intercepted` pops it without running, or
      * it expires (`expiry_at <= now`) and a sweep removes it.

    The prepared graph, blobs, and paste parameters are snapshotted at
    creation time.  A config change in the docker between intercept
    and resume does not affect the intercepted run.

    `prompt_node_id` is the node in the prepared graph whose `value`
    input the workflow's "prompt" Primitive writes.  None if the
    workflow has no such slot; in that case `prompt_override` in the
    resume command is a no-op and `comfy_resume_intercepted` logs a
    warning instead of failing.

    `canvas_b64`, `selection_b64`, `context_b64` are the raw PNG bytes
    (base64-encoded for transport) of the three `@`-resources the
    workflow will actually consume.  These are post-prepare: rotated
    and cropped per `/crop_frame` when the inpaint playground was
    active; raw when it wasn't.  They are exactly what the model sees,
    which is what the interceptor should display.
    """

    def __init__(self, *,
                 batch_id: str,
                 workflow_name: str,
                 prepared_graph: dict,
                 prompt_node_id: Optional[str],
                 prompt_original: str,
                 crop_bounds: tuple,
                 output_rotation: float,
                 paste_config: dict,
                 base_seed: int,
                 seed: int,
                 canvas_b64: str,
                 selection_b64: str,
                 context_b64: str,
                 params_snapshot: dict,
                 warnings: list,
                 interceptor_url: str):
        self.batch_id = batch_id
        self.workflow_name = workflow_name
        now = time.time()
        self.created_at = now
        self.expiry_at = now + INTERCEPT_TIMEOUT_SEC
        self.prepared_graph = prepared_graph
        self.prompt_node_id = prompt_node_id
        self.prompt_original = prompt_original
        self.crop_bounds = tuple(crop_bounds)
        self.output_rotation = float(output_rotation)
        self.paste_config = dict(paste_config)
        self.base_seed = int(base_seed)
        self.seed = int(seed)
        self.canvas_b64 = canvas_b64 or ""
        self.selection_b64 = selection_b64 or ""
        self.context_b64 = context_b64 or ""
        self.params_snapshot = dict(params_snapshot or {})
        self.warnings = list(warnings or [])
        self.interceptor_url = interceptor_url