import base64
import numpy as np
from PyQt6.QtGui import QImage
from PyQt6.QtCore import QByteArray, Qt

from krita import *
from ..decorators import command, AutoDispatchHandler
from ..utils.layer_resolve import resolve as resolve_node, register as register_node


class MaskHandler(AutoDispatchHandler):

    def _decode_mask_to_binary(self, mask_b64, target_w, target_h, threshold):
        """Base64 PNG → np.uint8 (h, w), values 0 or 255.

        Resizes to target dims if needed (mask and document may differ
        if the user switched docs between segmentation and export)."""
        try:
            raw = base64.b64decode(mask_b64)
        except Exception as e:
            return None, f"base64 decode failed: {e}"

        qimg = QImage()
        if not qimg.loadFromData(QByteArray(raw), "PNG"):
            return None, "PNG decode failed"
        qimg = qimg.convertToFormat(QImage.Format.Format_Grayscale8)

        if qimg.width() != target_w or qimg.height() != target_h:
            qimg = qimg.scaled(
                target_w, target_h,
                Qt.AspectRatioMode.IgnoreAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            qimg = qimg.convertToFormat(QImage.Format.Format_Grayscale8)

        ptr = qimg.constBits()
        ptr.setsize(qimg.sizeInBytes())
        data = bytes(ptr)
        stride = qimg.bytesPerLine()
        arr = np.frombuffer(data, dtype=np.uint8).reshape((target_h, stride))
        arr = arr[:, :target_w]
        binary = (arr >= threshold).astype(np.uint8) * 255
        return binary, None

    @command(
        category='mask',
        help_text='Add a selection mask to a layer',
        args={
            '--layer_name': {'type': 'str', 'required': True, 'help': 'Name of the target layer'},
            '--use_current_selection': {'type': 'bool', 'default': False, 'help': 'Use current global selection'}
        },
        arg_map={'use_current_selection': 'use_selection'},
    )
    def add_selection_mask(self, layer_name, use_selection=False):
        try:
            doc = Krita.instance().activeDocument()
            if not doc:
                return {'success': False, 'message': 'No active document'}

            target = resolve_node(doc, layer_name)
            if not target:
                return {'success': False, 'message': f'Layer "{layer_name}" not found'}

            original = doc.activeNode()
            doc.setActiveNode(target)

            Krita.instance().action('add_new_transparency_mask').trigger()
            new_mask = doc.activeNode()

            desired_name = f"{layer_name}_selection"
            new_mask.setName(desired_name)

            if use_selection:
                selection = doc.selection()
                if selection and not selection.isNull():
                    new_mask.setPixelData(selection.pixelData())

            Krita.instance().action('convert_to_selection_mask').trigger()

            doc.activeNode().setName(desired_name)
            register_node(desired_name, doc.activeNode())

            doc.setActiveNode(original)
            doc.refreshProjection()

            return {'success': True,
                    'message': f'Added selection mask to "{layer_name}"',
                    'data': {'mask_name': desired_name}}
        except Exception as e:
            return {'success': False, 'message': str(e)}

    @command(
        category='mask',
        help_text='Add a selection mask to the currently active layer',
        args={
            '--use_current_selection': {'type': 'bool', 'default': False, 'help': 'Use current global selection'}
        },
        arg_map={'use_current_selection': 'use_selection'},
    )
    def add_selection_mask_to_active(self, use_selection=False):
        try:
            doc = Krita.instance().activeDocument()
            if not doc:
                return {'success': False, 'message': 'No active document'}
            active = doc.activeNode()
            if not active:
                return {'success': False, 'message': 'No active layer'}
            return self.add_selection_mask(active.name(), use_selection)
        except Exception as e:
            return {'success': False, 'message': str(e)}

    # ------------------------------------------------------------------
    # Base64-mask based commands (used by the SAM3 playground exporter)
    # ------------------------------------------------------------------

    @command(
        category='mask',
        help_text='Set the document selection from a base64-encoded PNG mask',
        args={
            '--mask_b64': {'type': 'str', 'required': True,
                           'help': 'Base64 PNG mask (white = selected)'},
            '--mode': {'type': 'str', 'default': 'replace',
                       'choices': ['replace', 'add', 'subtract'],
                       'help': 'How to combine with any existing selection'},
            '--threshold': {'type': 'int', 'default': 128,
                            'help': 'Pixel intensity threshold (0-255); '
                                    'pixels >= this value count as selected'},
        },
    )
    def set_selection_from_mask_b64(self, mask_b64="", mode="replace", threshold=128):
        try:
            doc = Krita.instance().activeDocument()
            if not doc:
                return {'success': False, 'message': 'No active document'}
            if not mask_b64:
                return {'success': False, 'message': 'mask_b64 required'}
            if mode not in ('replace', 'add', 'subtract'):
                return {'success': False, 'message': f'Unknown mode: {mode!r}'}

            doc_w, doc_h = doc.width(), doc.height()
            binary, err = self._decode_mask_to_binary(
                mask_b64, doc_w, doc_h, threshold,
            )
            if err:
                return {'success': False, 'message': err}

            new_sel = Selection()
            new_sel.setPixelData(QByteArray(binary.tobytes()),
                                 0, 0, doc_w, doc_h)

            if mode == 'replace':
                doc.setSelection(new_sel)
            elif mode == 'add':
                current = doc.selection()
                if current and not current.isNull():
                    current.add(new_sel)
                    doc.setSelection(current)
                else:
                    doc.setSelection(new_sel)
            elif mode == 'subtract':
                current = doc.selection()
                if current and not current.isNull():
                    current.subtract(new_sel)
                    doc.setSelection(current)

            doc.refreshProjection()
            return {'success': True,
                    'message': f'Selection set from mask ({mode})',
                    'data': {'mode': mode, 'width': doc_w, 'height': doc_h}}
        except Exception as e:
            return {'success': False, 'message': str(e)}

    @command(
        category='mask',
        help_text='Add a transparency mask to a layer from a base64-encoded PNG mask',
        args={
            '--mask_b64': {'type': 'str', 'required': True,
                           'help': 'Base64 PNG mask (white = visible, black = transparent)'},
            '--layer_name': {'type': 'str', 'default': '',
                             'help': 'Target layer name; empty = active layer'},
            '--invert': {'type': 'bool', 'default': False,
                         'help': 'Invert the mask (white = transparent, black = visible)'},
            '--threshold': {'type': 'int', 'default': 128,
                            'help': 'Pixel intensity threshold (0-255)'},
        },
    )
    def apply_transparency_mask_b64(self, mask_b64="", layer_name="",
                                    invert=False, threshold=128):
        try:
            doc = Krita.instance().activeDocument()
            if not doc:
                return {'success': False, 'message': 'No active document'}
            if not mask_b64:
                return {'success': False, 'message': 'mask_b64 required'}

            if layer_name:
                target = resolve_node(doc, layer_name)
                if not target:
                    return {'success': False,
                            'message': f'Layer "{layer_name}" not found'}
            else:
                target = doc.activeNode()
                if not target:
                    return {'success': False, 'message': 'No active layer'}

            doc_w, doc_h = doc.width(), doc.height()
            binary, err = self._decode_mask_to_binary(
                mask_b64, doc_w, doc_h, threshold,
            )
            if err:
                return {'success': False, 'message': err}
            if invert:
                binary = 255 - binary

            mask_name = f"{target.name()}_transparency"
            mask = doc.createTransparencyMask(mask_name)
            if not mask:
                return {'success': False,
                        'message': 'Failed to create transparency mask'}

            target.addChildNode(mask, None)
            mask.setPixelData(QByteArray(binary.tobytes()),
                              0, 0, doc_w, doc_h)

            doc.setActiveNode(mask)
            register_node(mask_name, mask)
            doc.refreshProjection()

            return {'success': True,
                    'message': f'Transparency mask applied to "{target.name()}"',
                    'data': {'layer_name': target.name(),
                             'mask_name': mask_name,
                             'inverted': invert}}
        except Exception as e:
            return {'success': False, 'message': str(e)}
