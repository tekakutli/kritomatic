import json
import math
import re
import xml.etree.ElementTree as ET
from krita import Krita
from ..decorators import command


_DOCTYPE_RE = re.compile(r'^\s*<!DOCTYPE[^>]*>\s*', re.IGNORECASE)


def _strip_doctype(xml_str):
    """Remove a leading DOCTYPE declaration.

    ElementTree's parser (expat) can choke on the bare declaration
    Krita emits (`<!DOCTYPE transform_params>`, no internal subset).
    Stripping before parse and restoring on the way out sidesteps
    that.
    """
    return _DOCTYPE_RE.sub('', xml_str, count=1)


def _serialize_transform_xml(root):
    """Serialise back with the DOCTYPE Krita expects."""
    return ('<!DOCTYPE transform_params>\n'
            + ET.tostring(root, encoding='unicode'))


def _compute_homography(src, dst):
    """Solve the 4-point homography from src to dst.

    Returns 9 floats in row-major, column-vector form:

        [x']   [h11 h12 h13] [u]
        [y'] = [h21 h22 h23] [v]
        [w']   [h31 h32 h33] [1]

    with h33 normalised to 1.
    """
    A = []
    b = []
    for (u, v), (x, y) in zip(src, dst):
        A.append([u, v, 1.0, 0.0, 0.0, 0.0, -u * x, -v * x])
        A.append([0.0, 0.0, 0.0, u, v, 1.0, -u * y, -v * y])
        b.append(x)
        b.append(y)

    n = 8
    for i in range(n):
        piv = i
        for j in range(i + 1, n):
            if abs(A[j][i]) > abs(A[piv][i]):
                piv = j
        A[i], A[piv] = A[piv], A[i]
        b[i], b[piv] = b[piv], b[i]
        for j in range(i + 1, n):
            factor = A[j][i] / A[i][i]
            for k in range(i, n):
                A[j][k] -= factor * A[i][k]
            b[j] -= factor * b[i]

    h = [0.0] * n
    for i in range(n - 1, -1, -1):
        s = b[i]
        for k in range(i + 1, n):
            s -= A[i][k] * h[k]
        h[i] = s / A[i][i]

    return h + [1.0]


def _ensure_transform_nodes(root):
    """Navigate the real structure of a Krita transform mask's XML.

        <transform_params>
          <data mode="N">
            <free_transform>
              ...
              <originalCenter .../>
              <transformedCenter .../>
              <scaleX .../>
              <scaleY .../>
              ...
              <flattenedPerspectiveTransform m11=".." ... m33=".."/>
              ...
            </free_transform>
          </data>
        </transform_params>

    Returns (data, free, originalCenter, transformedCenter, persp,
    scaleX, scaleY), creating any that are missing.  Setting
    attributes on nodes that already exist preserves every field we
    do not touch.
    """
    data = root.find('data')
    if data is None:
        data = ET.SubElement(root, 'data')
        data.set('mode', '0')

    free = data.find('free_transform')
    if free is None:
        free = ET.SubElement(data, 'free_transform')

    original_center = free.find('originalCenter')
    if original_center is None:
        original_center = ET.SubElement(free, 'originalCenter')
        original_center.set('type', 'pointf')
        original_center.set('x', '0')
        original_center.set('y', '0')

    transformed_center = free.find('transformedCenter')
    if transformed_center is None:
        transformed_center = ET.SubElement(free, 'transformedCenter')
        transformed_center.set('type', 'pointf')
        transformed_center.set('x', '0')
        transformed_center.set('y', '0')

    persp = free.find('flattenedPerspectiveTransform')
    if persp is None:
        persp = ET.SubElement(free, 'flattenedPerspectiveTransform')
        persp.set('type', 'transform')

    scale_x = free.find('scaleX')
    if scale_x is None:
        scale_x = ET.SubElement(free, 'scaleX')
        scale_x.set('type', 'value')
        scale_x.set('value', '1')

    scale_y = free.find('scaleY')
    if scale_y is None:
        scale_y = ET.SubElement(free, 'scaleY')
        scale_y.set('type', 'value')
        scale_y.set('value', '1')

    return (data, free, original_center, transformed_center,
            persp, scale_x, scale_y)


def _column_to_qt_attrs(h):
    """Convert column-vector row-major h (9 floats) into Qt's
    row-vector attribute names.

    Qt's QTransform maps a point as [x' y' w'] = [u v 1] @ M_row,
    where M_row is the transpose of the column-vector homography.
    """
    m11 = h[0]; m12 = h[3]; m13 = h[6]
    m21 = h[1]; m22 = h[4]; m23 = h[7]
    m31 = h[2]; m32 = h[5]; m33 = h[8]
    return m11, m12, m13, m21, m22, m23, m31, m32, m33


class LayerTransformHandler:
    def execute(self, cmd_type, params):
        if cmd_type == 'create_transform_mask':
            return self.create_transform_mask(params)
        elif cmd_type == 'transform_mask':
            return self.transform_mask(params)
        elif cmd_type == 'set_perspective_transform_mask':
            return self.set_perspective_transform_mask(params)
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

            return {
                'success': True,
                'message': f'Created transform mask "{mask_name}" on "{layer_name}"',
                'data': {
                    'mask_name': mask_name,
                    'layer_name': layer_name,
                    'xml': mask.toXML(),
                },
            }
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

            rad = math.radians(rot)
            cos_r = math.cos(rad)
            sin_r = math.sin(rad)

            # Column-vector form: T @ S @ R.
            h11 = sx * cos_r; h12 = -sx * sin_r; h13 = tx
            h21 = sy * sin_r; h22 =  sy * cos_r; h23 = ty
            h31 = 0.0;        h32 =  0.0;        h33 = 1.0
            h = [h11, h12, h13, h21, h22, h23, h31, h32, h33]

            m11, m12, m13, m21, m22, m23, m31, m32, m33 = \
                _column_to_qt_attrs(h)

            root = ET.fromstring(_strip_doctype(mask.toXML()))
            data, free, oc, tc, persp, sx_e, sy_e = \
                _ensure_transform_nodes(root)

            # Free transform mode.  The affine matrix goes into the
            # matrix element; but for a pure free-transform Krita
            # actually reads scaleX/scaleY/transformedCenter, so we
            # set those too and skip mode=4.
            data.set('mode', '0')

            persp.set('m11', str(m11))
            persp.set('m12', str(m12))
            persp.set('m13', str(m13))
            persp.set('m21', str(m21))
            persp.set('m22', str(m22))
            persp.set('m23', str(m23))
            persp.set('m31', str(m31))
            persp.set('m32', str(m32))
            persp.set('m33', str(m33))

            oc.set('x', '0')
            oc.set('y', '0')
            tc.set('x', str(tx))
            tc.set('y', str(ty))

            sx_e.set('value', str(sx))
            sy_e.set('value', str(sy))

            mask.fromXML(_serialize_transform_xml(root))
            doc.refreshProjection()

            return {
                'success': True,
                'message': f'Transform mask "{mask_name}" updated',
                'data': {
                    'translate_x': tx, 'translate_y': ty,
                    'rotation': rot, 'scale_x': sx, 'scale_y': sy,
                    'xml_after': mask.toXML(),
                },
            }
        except Exception as e:
            return {'success': False, 'message': str(e)}

    @command(
        category='layer',
        help_text='Set a transform mask to a 4-point perspective transform',
        args={
            '--mask_name': {'type': 'str', 'required': True,
                            'help': 'Name of the transform mask'},
            '--src_points': {'type': 'str', 'required': True,
                             'help': 'JSON list of 4 [x, y] source corners, '
                                     'in TL, TR, BR, BL order, in canvas pixels'},
            '--dst_points': {'type': 'str', 'required': True,
                             'help': 'JSON list of 4 [x, y] destination corners, '
                                     'in the same order, in canvas pixels'},
        }
    )
    def set_perspective_transform_mask(self, params):
        try:
            doc = Krita.instance().activeDocument()
            if not doc:
                return {'success': False, 'message': 'No active document'}

            mask_name = params.get('mask_name', '')
            src_raw = params.get('src_points', '')
            dst_raw = params.get('dst_points', '')

            src = json.loads(src_raw) if isinstance(src_raw, str) else src_raw
            dst = json.loads(dst_raw) if isinstance(dst_raw, str) else dst_raw

            if len(src) != 4 or len(dst) != 4:
                return {'success': False,
                        'message': 'src_points and dst_points must each have '
                                   'exactly 4 points'}

            def find_mask(node, name):
                if node.type() == "transformmask" and node.name() == name:
                    return node
                for child in node.childNodes():
                    r = find_mask(child, name)
                    if r:
                        return r
                return None

            mask = find_mask(doc.rootNode(), mask_name)
            if not mask:
                return {'success': False,
                        'message': f'Transform mask "{mask_name}" not found'}

            # Centroids of source and destination.
            s_cx = sum(p[0] for p in src) / 4.0
            s_cy = sum(p[1] for p in src) / 4.0
            d_cx = sum(p[0] for p in dst) / 4.0
            d_cy = sum(p[1] for p in dst) / 4.0

            # Shift both to origin.  The homography then maps
            # (src - src_center) to (dst - dst_center) with no
            # translation term inside the matrix.
            src_sh = [(p[0] - s_cx, p[1] - s_cy) for p in src]
            dst_sh = [(p[0] - d_cx, p[1] - d_cy) for p in dst]

            h = _compute_homography(src_sh, dst_sh)
            m11, m12, m13, m21, m22, m23, m31, m32, m33 = \
                _column_to_qt_attrs(h)

            root = ET.fromstring(_strip_doctype(mask.toXML()))
            data, free, oc, tc, persp, sx_e, sy_e = \
                _ensure_transform_nodes(root)

            # Perspective mode.  This is what makes Krita read
            # flattenedPerspectiveTransform at all; with mode=0 it
            # reads the free-transform fields and ignores the matrix.
            data.set('mode', '4')

            persp.set('m11', str(m11))
            persp.set('m12', str(m12))
            persp.set('m13', str(m13))
            persp.set('m21', str(m21))
            persp.set('m22', str(m22))
            persp.set('m23', str(m23))
            persp.set('m31', str(m31))
            persp.set('m32', str(m32))
            persp.set('m33', str(m33))

            # Pivots.  originalCenter = the source centroid,
            # transformedCenter = the destination centroid.
            oc.set('x', str(s_cx))
            oc.set('y', str(s_cy))
            tc.set('x', str(d_cx))
            tc.set('y', str(d_cy))

            sx_e.set('value', '1')
            sy_e.set('value', '1')

            mask.fromXML(_serialize_transform_xml(root))
            doc.refreshProjection()

            return {
                'success': True,
                'message': f'Applied perspective transform to mask '
                           f'"{mask_name}"',
                'data': {
                    'mask_name': mask_name,
                    'matrix': [m11, m12, m13, m21, m22, m23, m31, m32, m33],
                    'src_center': [s_cx, s_cy],
                    'dst_center': [d_cx, d_cy],
                    'xml_after': mask.toXML(),
                },
            }
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
        try:
            doc = Krita.instance().activeDocument()
            if not doc:
                return {'success': False, 'message': 'No active document'}

            layer_name = params.get('layer_name', '')

            target_layer = doc.nodeByName(layer_name)
            if not target_layer:
                return {'success': False, 'message': f'Layer "{layer_name}" not found'}

            bounds = target_layer.bounds()
            layer_width = bounds.width()
            layer_height = bounds.height()
            layer_x = bounds.x()
            layer_y = bounds.y()

            if layer_width == 0 or layer_height == 0:
                return {'success': False, 'message': f'Layer "{layer_name}" has no content'}

            canvas_width = doc.width()
            canvas_height = doc.height()

            scale_x = canvas_width / layer_width
            scale_y = canvas_height / layer_height
            scale = min(scale_x, scale_y)

            scaled_width = layer_width * scale
            scaled_height = layer_height * scale

            final_x = (canvas_width - scaled_width) / 2
            final_y = (canvas_height - scaled_height) / 2

            translate_x = final_x - (layer_x * scale)
            translate_y = final_y - (layer_y * scale)

            transform_mask = doc.createTransformMask(f"{layer_name}_fit")
            target_layer.addChildNode(transform_mask, None)

            # Column-vector form: scale then translate.
            h11 = scale; h12 = 0.0;   h13 = translate_x
            h21 = 0.0;   h22 = scale; h23 = translate_y
            h31 = 0.0;   h32 = 0.0;   h33 = 1.0
            h = [h11, h12, h13, h21, h22, h23, h31, h32, h33]

            m11, m12, m13, m21, m22, m23, m31, m32, m33 = \
                _column_to_qt_attrs(h)

            root = ET.fromstring(_strip_doctype(transform_mask.toXML()))
            data, free, oc, tc, persp, sx_e, sy_e = \
                _ensure_transform_nodes(root)

            data.set('mode', '4')

            persp.set('m11', str(m11))
            persp.set('m12', str(m12))
            persp.set('m13', str(m13))
            persp.set('m21', str(m21))
            persp.set('m22', str(m22))
            persp.set('m23', str(m23))
            persp.set('m31', str(m31))
            persp.set('m32', str(m32))
            persp.set('m33', str(m33))

            oc.set('x', '0')
            oc.set('y', '0')
            tc.set('x', '0')
            tc.set('y', '0')

            sx_e.set('value', '1')
            sy_e.set('value', '1')

            transform_mask.fromXML(_serialize_transform_xml(root))

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
                    'translation': (translate_x, translate_y),
                }
            }
        except Exception as e:
            return {'success': False, 'message': str(e)}
