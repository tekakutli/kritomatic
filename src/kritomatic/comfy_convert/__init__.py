"""ComfyUI UI-format to API-format workflow conversion (CLI surface).

The algorithm is in `kritomatic_shared.comfy_convert`. This package
provides the argparse wrapper (`kritomatic comfyui convert`) and
re-exports the algorithm names for backward compatibility.

Existing imports keep working:

    from comfy_convert import run_convert_command
    from comfy_convert import convert_if_ui, ConversionError
    from kritomatic.comfy_convert import convert_workflow
    from kritomatic.comfy_convert.cli import populate_convert_args
"""

from kritomatic_shared.comfy_convert import (
    ConversionError,
    convert_if_ui,
    convert_workflow,
    is_ui_format,
)
from .cli import run_convert_command

__all__ = [
    "ConversionError",
    "convert_if_ui",
    "convert_workflow",
    "is_ui_format",
    "run_convert_command",
]