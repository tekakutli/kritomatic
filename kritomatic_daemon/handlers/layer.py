from .layer_basic import LayerBasicHandler
from .layer_file import LayerFileHandler
from .layer_transform import LayerTransformHandler
from .layer_text import LayerTextHandler
from .layer_fill import LayerFillHandler
from .layer_color import LayerColorHandler
from .layer_export import LayerExportHandler
from .layer_blend import LayerBlendHandler
from ..decorators import build_dispatch_map


class LayerHandler:
    def __init__(self):
        self.basic = LayerBasicHandler()
        self.file = LayerFileHandler()
        self.transform = LayerTransformHandler()
        self.text = LayerTextHandler()
        self.fill = LayerFillHandler()
        self.color = LayerColorHandler()
        self.export = LayerExportHandler()
        self.blend = LayerBlendHandler()

        # Declared explicitly so the base dispatch map (built in
        # CommandHandler.__init__) can see through this container and
        # claim every layer command for the LayerHandler instance.
        # The recursion in all_command_names() reads exactly this
        # attribute.
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

        # Route each layer command name to its owning sub-handler.
        # Replaces the if/elif chain that used to live in execute().
        self._cmd_map = build_dispatch_map(self._sub_handlers)

    def execute(self, cmd_type, params):
        handler = self._cmd_map.get(cmd_type)
        if handler is None:
            return {'success': False,
                    'message': f'Unknown layer command: {cmd_type}'}
        return handler.execute(cmd_type, params)
