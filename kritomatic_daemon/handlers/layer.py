from .layer_basic import LayerBasicHandler
from .layer_file import LayerFileHandler
from .layer_transform import LayerTransformHandler
from .layer_text import LayerTextHandler
from .layer_fill import LayerFillHandler
from .layer_color import LayerColorHandler
from .layer_export import LayerExportHandler

class LayerHandler:
    def __init__(self):
        self.basic = LayerBasicHandler()
        self.file = LayerFileHandler()
        self.transform = LayerTransformHandler()
        self.text = LayerTextHandler()
        self.fill = LayerFillHandler()
        self.color = LayerColorHandler()
        self.export = LayerExportHandler()

    def execute(self, cmd_type, params):
        # Basic layer operations
        if cmd_type in ['create_layer', 'list_layers', 'set_active_layer',
                        'rename_active_layer', 'rename_layer_by_name',
                        'move_layer_to_group', 'move_active_layer_to_group']:
            return self.basic.execute(cmd_type, params)

        # File layer operations
        elif cmd_type in ['create_file_layer', 'convert_to_file_layer']:
            return self.file.execute(cmd_type, params)

        # Transform mask operations
        elif cmd_type in ['create_transform_mask', 'transform_mask']:
            return self.transform.execute(cmd_type, params)

        # Text operations
        elif cmd_type in ['add_vector_text', 'update_vector_text', 'list_shapes', 'replace_all_text']:
            return self.text.execute(cmd_type, params)

        # Fill operations
        elif cmd_type in ['fill_layer', 'fill_selection']:
            return self.fill.execute(cmd_type, params)

        # Color operations
        elif cmd_type in ['apply_color_to_alpha', 'add_color_to_alpha_mask']:
            return self.color.execute(cmd_type, params)

        # Export operations
        elif cmd_type in ['move_layer_to_new_document', 'export_layer_to_file']:
            return self.export.execute(cmd_type, params)

        return {'success': False, 'message': f'Unknown layer command: {cmd_type}'}
