"""Stable types and constants for the ComfyUI handler.

Data shapes and path constants used across the handler tree:

  * `_BatchRecord` — live state of one `comfy_generate` call, or one
    externally-registered proxy job (playground inpaint).
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


# ============================================================================
#  Constants
# ============================================================================

_RECENT_ALIASES_CAP = 50


_READ_ONLY_COMMANDS = frozenset({
    "comfy_status",
    "comfy_batches",
    "comfy_list_workflows",
    "comfy_history",
    "comfy_list_recent_aliases",
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