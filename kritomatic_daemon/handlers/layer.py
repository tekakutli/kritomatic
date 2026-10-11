from .layer_basic import LayerBasicHandler
from .layer_file import LayerFileHandler
from .layer_transform import LayerTransformHandler
from .layer_text import LayerTextHandler
from .layer_fill import LayerFillHandler
from .layer_color import LayerColorHandler
from .layer_export import LayerExportHandler
from .layer_blend import LayerBlendHandler
from ..decorators import AutoDispatchHandler


class LayerHandler(AutoDispatchHandler):
    def __init__(self):
        self.basic = LayerBasicHandler()
        self.file = LayerFileHandler()
        self.transform = LayerTransformHandler()
        self.text = LayerTextHandler()
        self.fill = LayerFillHandler()
        self.color = LayerColorHandler()
        self.export = LayerExportHandler()
        self.blend = LayerBlendHandler()
        # Container dispatch: build_handler_map() descends into every
        # sub-handler and routes each layer command to its owner.
        self._sub_handlers = [
            self.basic,
            self.file,
            self.transform,
            self.text,
            self.fill,
            self.color,
            self.export,
            self.blend,
        ]
        super().__init__()
