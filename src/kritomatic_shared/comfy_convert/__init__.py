"""UI→API workflow conversion, shared between the CLI and the daemon.

The algorithm is in `convert.py`. The CLI's `kritomatic comfyui convert`
subcommand and the daemon's auto-conversion on `comfy_load` both call
`convert_if_ui` from here.

Re-exported symbols:

    ConversionError    raised by every conversion path; the caller
                       catches this single exception type
    DEFAULT_COMFY_URL  default for the (currently unused) --url arg
    convert_if_ui      the high-level entry point: no-op if the file
                       is already API format, convert otherwise
    convert_workflow   the lower-level worker: unconditionally convert
                       a UI-format source; raises if source is API
    is_ui_format       detect UI format without parsing deeper
    default_output_dir returns (and creates) <repo>/data/convert/, the
                       directory `convert_if_ui` writes converted files
                       to by default
"""

from .convert import (
    ConversionError,
    DEFAULT_COMFY_URL,
    convert_if_ui,
    convert_workflow,
    default_output_dir,
    is_ui_format,
)

__all__ = [
    "ConversionError",
    "DEFAULT_COMFY_URL",
    "convert_if_ui",
    "convert_workflow",
    "default_output_dir",
    "is_ui_format",
]
