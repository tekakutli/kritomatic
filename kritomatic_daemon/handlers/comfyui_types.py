"""Stable types and constants for the ComfyUI handler.

Data shapes and path constants used across the handler tree:

  * `_BatchRecord` — live state of one `comfy_generate` call.
  * `_PendingJob` — legacy async generation state. Unused by the
    v2 pipeline (Stage 5 removed comfy_generate_async / comfy_wait);
    kept here so `comfyui_types` stays importable while Stage 6
    cleanup removes it.
  * Path constants (`_WORKFLOW_DIR`, `_HISTORY_DIR`, `_RECENT_ALIASES_PATH`).
  * Recent-alias persistence helpers — tiny, best-effort, swallowed.

Nothing here mutates any state the handler tree owns; nothing here
imports from the handler tree.
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Optional

from ..utils.comfyui_client import GenerationResult


# ============================================================================
#  Constants
# ============================================================================

_SEED_PARAMETER_NAME = "seed"
_CANVAS_SEED_SLOT = 3
_RECENT_ALIASES_CAP = 50


_READ_ONLY_COMMANDS = frozenset({
    "comfy_status",
    "comfy_batches",
    "comfy_list_publishers",
    "comfy_list_workflows",
    "comfy_history",
    "comfy_list_recent_aliases",
    "comfy_list_scratch_blobs",
    "comfy_dump_scratch_blobs",
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
#  Data shapes
# ============================================================================

class _BatchRecord:
    """Live state of one `comfy_generate` call.

    Held in `ComfyUIHandler._batches`, keyed by batch id, from the
    moment the batch starts until the moment it returns.

    Fields added in Stage 5 (v2 pipeline):

      * `job_ids` — proxy job ids, in submission order. Every entry
        corresponds to a submitted run. Used by `comfy_cancel` (to
        cancel each in-flight job) and echoed in the response so a
        caller can reconcile against `GET /api/v2/jobs/{id}`.
      * `_cancel_sent` — set of job ids for which `cancel_job` has
        already been called. Prevents the poll loop from re-sending
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

        # Stage 5: proxy job ids + cancellation tracking.
        self.job_ids: list = []
        self._cancel_sent: set = set()


class _PendingJob:
    """Legacy async generation state.

    Retained so `comfyui_types` stays importable; nothing in the v2
    pipeline uses it. Stage 6 removes it along with the rest of the
    WebSocket path.
    """

    def __init__(self, prompt_id: str, workflow_name: str,
                 params: dict, seed: int):
        self.prompt_id = prompt_id
        self.workflow_name = workflow_name
        self.params = params
        self.seed = seed
        self.event = threading.Event()
        self.result: Optional[GenerationResult] = None
        self.error: Optional[Exception] = None
        self.document = None
        self.document_key: tuple = ("", "")
        self.started_at = time.time()
        self.warnings: list = []