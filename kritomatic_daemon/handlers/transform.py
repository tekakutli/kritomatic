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

            mask = NodeUtils.find_transform_mask(doc.rootNode(), mask_name)
            if not mask:
                return {'success': False, 'message': f'Transform mask "{mask_name}" not found'}

            # Get and modify XML
            xml_str = mask.toXML()
            root = ElementTree.fromstring(xml_str)

            rad = math.radians(rot)
            cos_r = math.cos(rad)
            sin_r = math.sin(rad)

            m11 = sx * cos_r
            m12 = -sx * sin_r
            m21 = sy * sin_r
            m22 = sy * cos_r

            final = root.find('finalTransform')
            if final is None:
                final = ElementTree.SubElement(root, 'finalTransform')

            final.set('m11', str(m11))
            final.set('m12', str(m12))
            final.set('m21', str(m21))
            final.set('m22', str(m22))
            final.set('dx', str(tx))
            final.set('dy', str(ty))

            mask.fromXML(ElementTree.tostring(root, encoding='unicode'))
            doc.refreshProjection()

            return {'success': True, 'message': f'Transform mask "{mask_name}" updated',
                    'data': {'translate_x': tx, 'translate_y': ty, 'rotation': rot, 'scale_x': sx, 'scale_y': sy}}
        except Exception as e:
            return {'success': False, 'message': str(e)}
