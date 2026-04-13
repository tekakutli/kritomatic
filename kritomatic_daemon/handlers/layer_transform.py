import xml.etree.ElementTree as ET
from krita import Krita

class LayerTransformHandler:
    def execute(self, cmd_type, params):
        if cmd_type == 'create_transform_mask':
            return self.create_transform_mask(params)
        elif cmd_type == 'transform_mask':
            return self.transform_mask(params)
        return {'success': False, 'message': f'Unknown transform command: {cmd_type}'}

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
