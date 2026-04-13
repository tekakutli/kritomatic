from krita import Krita

class LayerColorHandler:
    def execute(self, cmd_type, params):
        if cmd_type == 'apply_color_to_alpha':
            return self.apply_color_to_alpha(params)
        elif cmd_type == 'add_color_to_alpha_mask':
            return self.add_color_to_alpha_mask(params)
        return {'success': False, 'message': f'Unknown color command: {cmd_type}'}

    def apply_color_to_alpha(self, params):
        try:
            app = Krita.instance()
            doc = app.activeDocument()
            if not doc:
                return {'success': False, 'message': 'No active document'}

            layer_name = params.get('layer_name', '')
            target_color = params.get('target_color', '#ffffff')
            threshold = params.get('threshold', 255)

            target_layer = doc.nodeByName(layer_name)
            if not target_layer:
                return {'success': False, 'message': f'Layer "{layer_name}" not found'}

            # Duplicate layer
            dup_layer = target_layer.duplicate()
            dup_layer.setName(f"{layer_name}_alpha")
            target_layer.parentNode().addChildNode(dup_layer, target_layer)

            # Apply Color to Alpha filter
            color_filter = app.filter("colortoalpha")
            if not color_filter:
                return {'success': False, 'message': 'Color to Alpha filter not found'}

            target_color = target_color.lstrip('#')
            r = int(target_color[0:2], 16)
            g = int(target_color[2:4], 16)
            b = int(target_color[4:6], 16)

            config = color_filter.configuration()
            config.setProperty("red", r)
            config.setProperty("green", g)
            config.setProperty("blue", b)
            config.setProperty("threshold", threshold)
            color_filter.setConfiguration(config)

            color_filter.apply(dup_layer, 0, 0, doc.width(), doc.height())
            doc.refreshProjection()

            return {
                'success': True,
                'message': f'Applied Color to Alpha to duplicate of "{layer_name}"',
                'data': {
                    'original_layer': layer_name,
                    'new_layer': f"{layer_name}_alpha",
                    'target_color': f"#{r:02x}{g:02x}{b:02x}",
                    'threshold': threshold
                }
            }
        except Exception as e:
            return {'success': False, 'message': str(e)}

    def add_color_to_alpha_mask(self, params):
        try:
            app = Krita.instance()
            doc = app.activeDocument()
            if not doc:
                return {'success': False, 'message': 'No active document'}

            layer_name = params.get('layer_name', '')
            target_color = params.get('target_color', '#ffffff')
            threshold = params.get('threshold', 255)

            target_layer = doc.nodeByName(layer_name)
            if not target_layer:
                return {'success': False, 'message': f'Layer "{layer_name}" not found'}

            color_filter = app.filter("colortoalpha")
            if not color_filter:
                return {'success': False, 'message': 'Color to Alpha filter not found'}

            target_color = target_color.lstrip('#')
            r = int(target_color[0:2], 16)
            g = int(target_color[2:4], 16)
            b = int(target_color[4:6], 16)

            config = color_filter.configuration()
            config.setProperty("red", r)
            config.setProperty("green", g)
            config.setProperty("blue", b)
            config.setProperty("threshold", threshold)
            color_filter.setConfiguration(config)

            mask_name = f"{layer_name}_color_to_alpha"
            filter_mask = doc.createFilterMask(mask_name, color_filter, None)

            if not filter_mask:
                return {'success': False, 'message': 'Failed to create filter mask'}

            target_layer.addChildNode(filter_mask, None)
            doc.setActiveNode(filter_mask)
            doc.refreshProjection()

            return {
                'success': True,
                'message': f'Added Color to Alpha mask to "{layer_name}"',
                'data': {
                    'layer_name': layer_name,
                    'mask_name': mask_name,
                    'target_color': f"#{r:02x}{g:02x}{b:02x}",
                    'threshold': threshold
                }
            }
        except Exception as e:
            return {'success': False, 'message': str(e)}
