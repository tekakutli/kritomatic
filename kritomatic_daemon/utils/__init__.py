# kritomatic_daemon/utils/__init__.py
#
# Some of the modules under this package (krita_actions, color_utils,
# refresh, layer_resolve, text_metrics) import `krita` and only work
# inside Krita. Others (comfyui_client) are pure Python and can be
# imported by any interpreter.
#
# The eager imports below would drag `krita` into a plain Python
# process the moment anything under this package is touched, which is
# why this file guards them. Consumers outside Krita can still do
#
#     from kritomatic_daemon.utils.comfyui_client import ComfyClient
#
# because the submodule import does not depend on anything this
# __init__ re-exports.

try:
    from krita import Krita  # noqa: F401
    _KRITA_AVAILABLE = True
except ImportError:
    _KRITA_AVAILABLE = False


if _KRITA_AVAILABLE:
    from .color_utils import ColorUtils
    from .krita_actions import enumerate_actions, find_rotate_action

    __all__ = ['ColorUtils', 'enumerate_actions', 'find_rotate_action']
else:
    __all__ = []
