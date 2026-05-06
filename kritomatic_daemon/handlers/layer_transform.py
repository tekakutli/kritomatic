import xml.etree.ElementTree as ET
from krita import Krita
from ..decorators import command

class LayerTransformHandler:
    def execute(self, cmd_type, params):
        if cmd_type == 'create_transform_mask':
            return self.create_transform_mask(params)
        elif cmd_type == 'transform_mask':
            return self.transform_mask(params)
        elif cmd_type == 'fit_to_canvas':
            return self.fit_to_canvas(params)
        return {'success': False, 'message': f'Unknown transform command: {cmd_type}'}

    @command(
        category='layer',
        help_text='Create a transform mask on a layer',
        args={
            '--layer_name': {'type': 'str', 'required': True, 'help': 'Target layer name'},
            '--mask_name': {'type': 'str', 'default': 'Transform Mask', 'help': 'Name for the transform mask'}
        }
    )
    def create_transform_mask(self, params):
        try:
            doc = Krita.instance().activeDocument()
            if not doc:
                return {'success': False, 'message': 'No active document'}

            layer_name = params.get('layer_name', '')
            mask_name = params.get('mask_name', 'Transform Mask')

            target = doc.nodeByName(layer_name)
            if not target:
                return {'success': False, 'message': f'Layer "{layer_name}" not found'}

            mask = doc.createTransformMask(mask_name)
            target.addChildNode(mask, None)
            doc.setActiveNode(mask)
            doc.refreshProjection()

            return {'success': True, 'message': f'Created transform mask "{mask_name}" on "{layer_name}"', 'data': {'mask_name': mask_name, 'layer_name': layer_name}}
        except Exception as e:
            return {'success': False, 'message': str(e)}

    @command(
        category='layer',
        help_text='Apply transformation to a transform mask',
        args={
            '--mask_name': {'type': 'str', 'required': True, 'help': 'Name of the transform mask'},
            '--translate_x': {'type': 'float', 'default': 0, 'help': 'X translation in pixels'},
            '--translate_y': {'type': 'float', 'default': 0, 'help': 'Y translation in pixels'},
            '--rotation': {'type': 'float', 'default': 0, 'help': 'Rotation in degrees'},
            '--scale_x': {'type': 'float', 'default': 1.0, 'help': 'X scale factor (1.0 = 100 percent)'},
            '--scale_y': {'type': 'float', 'default': 1.0, 'help': 'Y scale factor (1.0 = 100 percent)'}
        }
    )
    def transform_mask(self, params):
        try:
            doc = Krita.instance().activeDocument()
            if not doc:
                return {'success': False, 'message': 'No active document'}

            mask_name = params.get('mask_name', '')
            tx = params.get('translate_x', 0)
            ty = params.get('translate_y', 0)
            rot = params.get('rotation', 0)
            sx = params.get('scale_x', 1.0)
            sy = params.get('scale_y', 1.0)

            def find_mask(node, name):
                if node.type() == "transformmask" and node.name() == name:
                    return node
                for child in node.childNodes():
                    result = find_mask(child, name)
                    if result:
                        return result
                return None

            mask = find_mask(doc.rootNode(), mask_name)
            if not mask:
                return {'success': False, 'message': f'Transform mask "{mask_name}" not found'}

            import math
            rad = math.radians(rot)
            cos_r = math.cos(rad)
            sin_r = math.sin(rad)

            m11 = sx * cos_r
            m12 = -sx * sin_r
            m21 = sy * sin_r
            m22 = sy * cos_r

            xml_str = mask.toXML()
            root = ET.fromstring(xml_str)

            for elem in root.findall('.//scaleX'):
                elem.set('value', str(sx))
            for elem in root.findall('.//scaleY'):
                elem.set('value', str(sy))
            for elem in root.findall('.//flattenedPerspectiveTransform'):
                elem.set('m11', str(m11))
                elem.set('m12', str(m12))
                elem.set('m21', str(m21))
                elem.set('m22', str(m22))
                elem.set('m31', str(tx))
                elem.set('m32', str(ty))
            for elem in root.findall('.//transformedCenter'):
                elem.set('x', str(tx))
                elem.set('y', str(ty))

            mask.fromXML(ET.tostring(root, encoding='unicode'))
            doc.refreshProjection()

            return {'success': True, 'message': f'Transform mask "{mask_name}" updated', 'data': {'translate_x': tx, 'translate_y': ty, 'rotation': rot, 'scale_x': sx, 'scale_y': sy}}
        except Exception as e:
            return {'success': False, 'message': str(e)}

    @command(
        category='layer',
        help_text='Scale layer content to fit canvas size while preserving aspect ratio',
        args={
            '--layer_name': {'type': 'str', 'required': True, 'help': 'Name of the layer to fit'},
        }
    )
    def fit_to_canvas(self, params):
        """
        Scale layer content to fit canvas size while preserving aspect ratio (centered)
        """
        try:
            doc = Krita.instance().activeDocument()
            if not doc:
                return {'success': False, 'message': 'No active document'}

            layer_name = params.get('layer_name', '')

            target_layer = doc.nodeByName(layer_name)
            if not target_layer:
                return {'success': False, 'message': f'Layer "{layer_name}" not found'}

            # Get layer bounds (the actual content area)
            bounds = target_layer.bounds()
            layer_width = bounds.width()
            layer_height = bounds.height()

            # Get layer's current position (top-left corner)
            layer_x = bounds.x()
            layer_y = bounds.y()

            if layer_width == 0 or layer_height == 0:
                return {'success': False, 'message': f'Layer "{layer_name}" has no content'}

            # Get canvas dimensions
            canvas_width = doc.width()
            canvas_height = doc.height()

            # Calculate scale to fit completely within canvas
            scale_x = canvas_width / layer_width
            scale_y = canvas_height / layer_height
            scale = min(scale_x, scale_y)

            # Calculate the scaled dimensions
            scaled_width = layer_width * scale
            scaled_height = layer_height * scale

            # Center the scaled content on canvas
            final_x = (canvas_width - scaled_width) / 2
            final_y = (canvas_height - scaled_height) / 2

            # Calculate translation needed from layer's current position
            translate_x = final_x - (layer_x * scale)
            translate_y = final_y - (layer_y * scale)

            # Create transform mask
            transform_mask = doc.createTransformMask(f"{layer_name}_fit")
            target_layer.addChildNode(transform_mask, None)

            # Apply transformation via XML
            import xml.etree.ElementTree as ET

            xml_str = transform_mask.toXML()
            root = ET.fromstring(xml_str)

            # Update scale and position
            for elem in root.findall('.//scaleX'):
                elem.set('value', str(scale))
            for elem in root.findall('.//scaleY'):
                elem.set('value', str(scale))
            for elem in root.findall('.//flattenedPerspectiveTransform'):
                elem.set('m11', str(scale))
                elem.set('m12', '0')
                elem.set('m21', '0')
                elem.set('m22', str(scale))
                elem.set('m31', str(translate_x))
                elem.set('m32', str(translate_y))
            for elem in root.findall('.//transformedCenter'):
                elem.set('x', str(translate_x))
                elem.set('y', str(translate_y))

            transform_mask.fromXML(ET.tostring(root, encoding='unicode'))

            # Make the mask active
            doc.setActiveNode(transform_mask)
            doc.refreshProjection()

            return {
                'success': True,
                'message': f'Layer "{layer_name}" fitted to canvas',
                'data': {
                    'original_size': (layer_width, layer_height),
                    'original_position': (layer_x, layer_y),
                    'canvas_size': (canvas_width, canvas_height),
                    'scale': scale,
                    'scaled_size': (scaled_width, scaled_height),
                    'final_position': (final_x, final_y),
                    'translation': (translate_x, translate_y)
                }
            }

        except Exception as e:
            return {'success': False, 'message': str(e)}
