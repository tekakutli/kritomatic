from krita import *
from ..utils.node_utils import NodeUtils
import math
from xml.etree import ElementTree

class TransformHandler:
    def execute(self, cmd_type, params):
        if cmd_type == 'create_transform_mask':
            return self.create_transform_mask(
                params.get('layer_name', ''),
                params.get('mask_name', 'Transform Mask')
            )
        elif cmd_type == 'transform_mask':
            return self.transform_mask(
                params.get('mask_name', ''),
                params.get('translate_x', 0),
                params.get('translate_y', 0),
                params.get('rotation', 0),
                params.get('scale_x', 1.0),
                params.get('scale_y', 1.0)
            )
        return {'success': False, 'message': f'Unknown transform command: {cmd_type}'}

    def create_transform_mask(self, layer_name, mask_name):
        try:
            doc = Krita.instance().activeDocument()
            if not doc:
                return {'success': False, 'message': 'No active document'}

            target = doc.nodeByName(layer_name)
            if not target:
                return {'success': False, 'message': f'Layer "{layer_name}" not found'}

            mask = doc.createTransformMask(mask_name)
            target.addChildNode(mask, None)
            doc.setActiveNode(mask)
            doc.refreshProjection()

            return {'success': True, 'message': f'Created transform mask "{mask_name}" on "{layer_name}"',
                    'data': {'mask_name': mask_name, 'layer_name': layer_name}}
        except Exception as e:
            return {'success': False, 'message': str(e)}

    def transform_mask(self, mask_name, tx=0, ty=0, rot=0, sx=1.0, sy=1.0):
        try:
            doc = Krita.instance().activeDocument()
            if not doc:
                return {'success': False, 'message': 'No active document'}

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

            import xml.etree.ElementTree as ET
            xml_str = mask.toXML()
            root = ET.fromstring(xml_str)

            # Update scale
            for scale_x in root.findall('.//scaleX'):
                scale_x.set('value', str(sx))
            for scale_y in root.findall('.//scaleY'):
                scale_y.set('value', str(sy))

            # Update position (translation)
            for transform in root.findall('.//flattenedPerspectiveTransform'):
                transform.set('m31', str(tx))
                transform.set('m32', str(ty))

            new_xml = ET.tostring(root, encoding='unicode')
            mask.fromXML(new_xml)

            doc.refreshProjection()
            return {'success': True, 'message': f'Transform mask "{mask_name}" updated'}

        except Exception as e:
            return {'success': False, 'message': str(e)}
