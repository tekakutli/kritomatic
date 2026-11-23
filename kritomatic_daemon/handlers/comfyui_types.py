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

# Default multimask playground URL, used when a `MultiAreaConditioning`
# node declares no `playground_url` widget value (or one that is blank).
# Mirrors the node's own default in kritomatic_nodes/__init__.py.
_DEFAULT_MULTIMASK_URL = "http://127.0.0.1:8776"

# Default controlnets playground URL, used when a
# `ControlNetsConditioning` node declares no `playground_url` widget
# value (or one that is blank).  Mirrors the node's own default in
# kritomatic_nodes/controlnets/__init__.py.
_DEFAULT_CONTROLNETS_URL = "http://127.0.0.1:8779"


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

    Multimask snapshot
    ------------------
    When the workflow contains a `MultiAreaConditioning` node, the
    daemon fetches multimask's state at intercept time and stores it
    here so the interceptor can display the areas and let the user
    edit their prompts.  The three fields together form the snapshot:

      * `multimask_url` — the URL the daemon used; the interceptor
        pushes back to this same URL on Run.
      * `multimask_state` — the `/state` response body:
        `{"globals": {...}, "areas": [...]}`.  No masks; only metadata
        and prompt strings.
      * `multimask_masks` — `{mask_hash: transformed_b64}` for every
        area.  These are the /mask-transformed PNGs (rotate+crop per
        the prepared frame at intercept time), so they line up with
        `canvas_b64` 1:1 and can be drawn over it directly.

    On `comfy_resume_intercepted`, the interceptor is expected to have
    pushed an updated state (from `multimask_state` with any per-area
    prompt edits applied) back to `multimask_url` before calling the
    daemon.  `MultiAreaConditioning` pulls that state at execution
    time.  The `multimask_masks` dict is display-only — the interceptor
    never pushes masks, only prompts, so multimask's originals stay
    intact and re-transformation at execution time is correct.

    Controlnets snapshot
    --------------------
    Same shape and rationale as the multimask snapshot, for workflows
    that consume the `controlnets` playground via a
    `ControlNetsConditioning` node:

      * `controlnets_url` — the URL the daemon used.
      * `controlnets_state` — the `/state` response body:
        `{"controlnets": [{id, name, image: {hash, width, height}}],
          "apply_enabled": {title: bool}, "version": str}`.
      * `controlnets_images` — `{hash: transformed_b64}` for every
        controlnet entry that had an image.  These are the
        `/controlnet_image`-transformed PNGs (rotate+crop per the
        prepared frame at intercept time), matching the same frame as
        `canvas_b64`.

    Display-only: unlike multimask, the interceptor does NOT push any
    state back to the controlnets playground before resume.  The
    controlnet chain configuration (which ControlNets, strength,
    start, end) and its enable flags live in the workflow file and in
    `/state.apply_enabled`; the interceptor treats them as
    read-only — what the reviewer sees is what runs.  A user who
    wants to disable an apply node does so in the controlnets
    playground before intercepting, not in the interceptor.

    If the workflow has no `ControlNetsConditioning` node,
    `controlnets_state` is `None` and the interceptor hides the
    controlnets section entirely.
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
                 interceptor_url: str,
                 multimask_url: Optional[str] = None,
                 multimask_state: Optional[dict] = None,
                 multimask_masks: Optional[dict] = None,
                 controlnets_url: Optional[str] = None,
                 controlnets_state: Optional[dict] = None,
                 controlnets_images: Optional[dict] = None):
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
        # Multimask snapshot — see class docstring.
        self.multimask_url = multimask_url
        self.multimask_state = multimask_state
        self.multimask_masks = dict(multimask_masks or {})
        # Controlnets snapshot — see class docstring.
        self.controlnets_url = controlnets_url
        self.controlnets_state = controlnets_state
        self.controlnets_images = dict(controlnets_images or {})