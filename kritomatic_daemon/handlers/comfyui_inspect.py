"""Daemon-side bridge to the shared slot extractors.

See kritomatic_daemon/utils/comfy_convert.py for the bootstrap pattern.
"""
import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parent.parent.parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from kritomatic_shared.comfyui_inspect import (  # noqa: E402,F401
    extract_all_slots,
    extract_output_nodes,
    extract_param_slots,
    extract_resource_slots,
    get_primary_output_node_id,  # if you adopt the @theoutput change
)

__all__ = [
    "extract_all_slots",
    "extract_output_nodes",
    "extract_param_slots",
    "extract_resource_slots",
    "get_primary_output_node_id",
]
