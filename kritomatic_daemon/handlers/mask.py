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

    # ------------------------------------------------------------------
    # Edge cleanup — pure NumPy, no scipy/cv2 dependency
    # ------------------------------------------------------------------
    #
    # Morphological helpers used by every mask-consuming command below.
    # The daemon ships no scipy or cv2, so this file implements 8-connected
    # dilation/erosion via np.maximum.reduce / np.minimum.reduce on eight
    # shifted slices, and a separable box blur via the cumsum trick.
    #
    # The box blur is an approximation of a Gaussian — one pass produces
    # a box-shaped kernel; the eye barely distinguishes it from a true
    # Gaussian at the radii a mask feather uses. If a user needs the exact
    # Smart_Segments behaviour, two or three box blur passes stack into a
    # closer approximation; the current default of one pass matches the
    # visual character of the original at typical feather radii (1-20 px).

    @staticmethod
    def _dilate(mask, iterations):
        result = mask.copy()
        for _ in range(iterations):
            p = np.pad(result, 1, mode='constant', constant_values=0)
            result = np.maximum.reduce([
                p[:-2, 1:-1], p[1:-1, 1:-1], p[2:, 1:-1],
                p[1:-1, :-2], p[1:-1, 2:],
                p[:-2, :-2], p[:-2, 2:], p[2:, :-2], p[2:, 2:],
            ])
        return result

    @staticmethod
    def _erode(mask, iterations):
        result = mask.copy()
        for _ in range(iterations):
            p = np.pad(result, 1, mode='constant', constant_values=255)
            result = np.minimum.reduce([
                p[:-2, 1:-1], p[1:-1, 1:-1], p[2:, 1:-1],
                p[1:-1, :-2], p[1:-1, 2:],
                p[:-2, :-2], p[:-2, 2:], p[2:, :-2], p[2:, 2:],
            ])
        return result

    @staticmethod
    def _box_blur(mask, radius):
        """Separable box blur. O(1) per pixel regardless of radius."""
        if radius <= 0:
            return mask

        k = 2 * radius + 1
        arr = mask.astype(np.float64)
        h, w = arr.shape

        # Horizontal pass
        p = np.pad(arr, ((0, 0), (radius, radius)), mode='edge')
        cs = np.cumsum(p, axis=1)
        cs = np.concatenate([np.zeros((cs.shape[0], 1), dtype=cs.dtype),
                             cs], axis=1)
        rows = (cs[:, k:k + w] - cs[:, :w]) / k

        # Vertical pass
        p = np.pad(rows, ((radius, radius), (0, 0)), mode='edge')
        cs = np.cumsum(p, axis=0)
        cs = np.concatenate([np.zeros((1, cs.shape[1]), dtype=cs.dtype),
                             cs], axis=0)
        return (cs[k:k + h, :] - cs[:h, :]) / k

    def _apply_edge_cleanup(self, binary,
                            expand_pixels=0,
                            contract_pixels=0,
                            smooth_iterations=0,
                            feather_radius=0):
        """Apply morphological edge cleanup to a uint8 binary mask.

        Order matters:
            1. Expand (dilation)   — grow outward first so contract that
                                     follows trims noise introduced by
                                     the growth, not the original edge.
            2. Contract (erosion)  — trim inward after any growth.
            3. Smooth              — morphological open then close, one
                                     pair per iteration, removes islands
                                     and fills holes without changing the
                                     overall edge position.
            4. Feather             — box blur to soften the boundary,
                                     producing a grayscale edge in
                                     [0, 255] that later becomes the
                                     alpha of the selection.

        Returned array is always uint8. When feather is 0, values stay
        at 0/255 (hard edge). When feather > 0, values can be intermediate,
        which is what `Selection.setPixelData` and transparency masks
        interpret as partial alpha.
        """
        if binary is None:
            return None

        result = binary

        if expand_pixels and expand_pixels > 0:
            result = self._dilate(result, int(expand_pixels))

        if contract_pixels and contract_pixels > 0:
            result = self._erode(result, int(contract_pixels))

        if smooth_iterations and smooth_iterations > 0:
            for _ in range(int(smooth_iterations)):
                result = self._erode(result, 1)     # open: erode
                result = self._dilate(result, 1)    # open: dilate
                result = self._dilate(result, 1)    # close: dilate
                result = self._erode(result, 1)     # close: erode

        if feather_radius and feather_radius > 0:
            blurred = self._box_blur(result, int(feather_radius))
            result = np.clip(blurred, 0, 255).astype(np.uint8)

        return result.astype(np.uint8)

    # ------------------------------------------------------------------
    # Selection-mask commands (unchanged)
    # ------------------------------------------------------------------

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
                       'choices': ['replace', 'add', 'subtract', 'intersect'],
                       'help': 'How to combine with any existing selection'},
            '--threshold': {'type': 'int', 'default': 128,
                            'help': 'Pixel intensity threshold (0-255); '
                                    'pixels >= this value count as selected'},
            '--invert': {'type': 'bool', 'default': False,
                         'help': 'Invert the mask before applying'},
            '--expand_pixels': {'type': 'int', 'default': 0,
                                'help': 'Grow the mask outward by N pixels '
                                        '(dilation) before applying'},
            '--contract_pixels': {'type': 'int', 'default': 0,
                                  'help': 'Shrink the mask inward by N pixels '
                                          '(erosion) before applying'},
            '--smooth_iterations': {'type': 'int', 'default': 0,
                                    'help': 'Morphological open+close iterations; '
                                            'removes islands and fills holes'},
            '--feather_radius': {'type': 'int', 'default': 0,
                                 'help': 'Box-blur radius in pixels for '
                                         'soft-edged selections'},
        },
    )
    def set_selection_from_mask_b64(self, mask_b64="", mode="replace",
                                    threshold=128, invert=False,
                                    expand_pixels=0, contract_pixels=0,
                                    smooth_iterations=0, feather_radius=0):
        try:
            doc = Krita.instance().activeDocument()
            if not doc:
                return {'success': False, 'message': 'No active document'}
            if not mask_b64:
                return {'success': False, 'message': 'mask_b64 required'}
            if mode not in ('replace', 'add', 'subtract', 'intersect'):
                return {'success': False, 'message': f'Unknown mode: {mode!r}'}

            doc_w, doc_h = doc.width(), doc.height()
            binary, err = self._decode_mask_to_binary(
                mask_b64, doc_w, doc_h, threshold,
            )
            if err:
                return {'success': False, 'message': err}

            binary = self._apply_edge_cleanup(
                binary,
                expand_pixels=expand_pixels,
                contract_pixels=contract_pixels,
                smooth_iterations=smooth_iterations,
                feather_radius=feather_radius,
            )
            if invert:
                binary = 255 - binary

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
                else:
                    # Subtracting from an empty selection is a no-op; keep
                    # the document selection untouched rather than
                    # replacing it with the inverse of the mask.
                    pass
            elif mode == 'intersect':
                current = doc.selection()
                if current and not current.isNull():
                    current.intersect(new_sel)
                    doc.setSelection(current)
                else:
                    # No existing selection to intersect with; intersecting
                    # against "everything" leaves the mask unchanged, so
                    # replace the (empty) selection with the new mask.
                    doc.setSelection(new_sel)

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
            '--expand_pixels': {'type': 'int', 'default': 0,
                                'help': 'Grow the mask outward by N pixels'},
            '--contract_pixels': {'type': 'int', 'default': 0,
                                  'help': 'Shrink the mask inward by N pixels'},
            '--smooth_iterations': {'type': 'int', 'default': 0,
                                    'help': 'Morphological open+close iterations'},
            '--feather_radius': {'type': 'int', 'default': 0,
                                 'help': 'Box-blur radius in pixels'},
        },
    )
    def apply_transparency_mask_b64(self, mask_b64="", layer_name="",
                                    invert=False, threshold=128,
                                    expand_pixels=0, contract_pixels=0,
                                    smooth_iterations=0, feather_radius=0):
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

            binary = self._apply_edge_cleanup(
                binary,
                expand_pixels=expand_pixels,
                contract_pixels=contract_pixels,
                smooth_iterations=smooth_iterations,
                feather_radius=feather_radius,
            )
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

    # ------------------------------------------------------------------
    # Layer extraction — single purpose, no group / blend / opacity
    # ------------------------------------------------------------------
    #
    # `extract_mask_to_layer` does one thing: turn a masked region of a
    # source layer into a new layer. Group membership, blend mode, and
    # opacity are NOT parameters here — callers compose those with the
    # existing `move_layer_to_group`, `set_layer_blend_mode`, and
    # `set_layer_opacity` commands. This keeps the command single-purpose
    # and matches the BATCH_MANUAL design rule ("KEEP THE DAEMON SIMPLE").
    #
    # Two modes:
    #
    #   new_layer — read the composited pixels of the source layer inside
    #               its own bounds, multiply alpha by the mask fraction,
    #               write the result as a fresh paint layer placed
    #               directly above the source (not at the top of the
    #               document root — position matters for stack coherence).
    #               Non-destructive to the source.
    #
    #   duplicate — duplicate the source layer in place, then attach a
    #               transparency mask whose pixel data is the (possibly
    #               feathered / inverted / cleaned) binary mask. Same
    #               visual result but preserves any structure of the
    #               source (masks, filters) and inherits blend / opacity
    #               from the source automatically.
    #
    # Both return data.new_layer = the name of the created layer so a
    # caller can chain follow-up commands against it.

    @command(
        category='mask',
        help_text='Extract the masked region of a layer into a new layer, '
                  'or duplicate the layer with the mask applied as a '
                  'transparency mask. Compose with move_layer_to_group / '
                  'set_layer_blend_mode / set_layer_opacity for post-apply '
                  'styling.',
        args={
            '--mask_b64': {'type': 'str', 'required': True,
                           'help': 'Base64 PNG mask (white = keep, black = drop)'},
            '--layer_name': {'type': 'str', 'default': '',
                             'help': 'Source layer name; empty = active layer'},
            '--new_layer_name': {'type': 'str', 'default': '',
                                 'help': 'Name for the new layer; empty = '
                                         '"<source>_extracted"'},
            '--mode': {'type': 'str', 'default': 'new_layer',
                       'choices': ['new_layer', 'duplicate'],
                       'help': 'new_layer = write masked pixels into a '
                               'fresh paint layer above the source; '
                               'duplicate = copy the layer in place and '
                               'attach a transparency mask'},
            '--threshold': {'type': 'int', 'default': 128,
                            'help': 'Pixel intensity threshold (0-255)'},
            '--invert': {'type': 'bool', 'default': False,
                         'help': 'Invert the mask before extracting'},
            '--expand_pixels': {'type': 'int', 'default': 0,
                                'help': 'Grow the mask outward by N pixels'},
            '--contract_pixels': {'type': 'int', 'default': 0,
                                  'help': 'Shrink the mask inward by N pixels'},
            '--smooth_iterations': {'type': 'int', 'default': 0,
                                    'help': 'Morphological open+close iterations'},
            '--feather_radius': {'type': 'int', 'default': 0,
                                 'help': 'Box-blur radius in pixels'},
        },
    )
    def extract_mask_to_layer(self, mask_b64="", layer_name="",
                              new_layer_name="", mode="new_layer",
                              threshold=128, invert=False,
                              expand_pixels=0, contract_pixels=0,
                              smooth_iterations=0, feather_radius=0):
        try:
            doc = Krita.instance().activeDocument()
            if not doc:
                return {'success': False, 'message': 'No active document'}
            if not mask_b64:
                return {'success': False, 'message': 'mask_b64 required'}
            if mode not in ('new_layer', 'duplicate'):
                return {'success': False, 'message': f'Unknown mode: {mode!r}'}

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

            binary = self._apply_edge_cleanup(
                binary,
                expand_pixels=expand_pixels,
                contract_pixels=contract_pixels,
                smooth_iterations=smooth_iterations,
                feather_radius=feather_radius,
            )
            if invert:
                binary = 255 - binary

            final_name = new_layer_name or f"{target.name()}_extracted"

            # ---- duplicate mode ----
            if mode == 'duplicate':
                dup = target.duplicate()
                if not dup:
                    return {'success': False,
                            'message': 'Failed to duplicate source layer'}
                dup.setName(final_name)

                parent = target.parentNode()
                if parent is None:
                    return {'success': False,
                            'message': 'Source layer has no parent'}
                parent.addChildNode(dup, target)

                mask = doc.createTransparencyMask(f"{final_name}_mask")
                if not mask:
                    # Roll back the duplicate on mask failure
                    try:
                        dup.remove()
                    except Exception:
                        pass
                    return {'success': False,
                            'message': 'Failed to create transparency mask'}

                dup.addChildNode(mask, None)
                mask.setPixelData(QByteArray(binary.tobytes()),
                                  0, 0, doc_w, doc_h)

                doc.setActiveNode(dup)
                register_node(final_name, dup)
                register_node(f"{final_name}_mask", mask)
                doc.refreshProjection()

                return {'success': True,
                        'message': f'Duplicated "{target.name()}" into '
                                   f'"{final_name}" with transparency mask',
                        'data': {'source_layer': target.name(),
                                 'new_layer': final_name,
                                 'mask_name': f"{final_name}_mask",
                                 'mode': mode}}

            # ---- new_layer mode ----
            bounds = target.bounds()
            bx, by = int(bounds.x()), int(bounds.y())
            bw, bh = int(bounds.width()), int(bounds.height())

            if bw <= 0 or bh <= 0:
                return {'success': False,
                        'message': f'Source layer "{target.name()}" has no '
                                   f'content (bounds are {bw}x{bh})'}

            src_data = target.projectionPixelData(bx, by, bw, bh)
            if not src_data:
                return {'success': False,
                        'message': 'Could not read source layer pixels'}

            src_arr = np.frombuffer(bytes(src_data), dtype=np.uint8)
            expected = bw * bh * 4
            if len(src_arr) != expected:
                return {'success': False,
                        'message': f'Unexpected source pixel data size: '
                                   f'{len(src_arr)} (expected {expected})'}

            src_arr = src_arr.reshape((bh, bw, 4)).copy()

            # Crop the full-canvas mask to the source layer's bounds. If
            # the bounds extend past the canvas (rare but possible for
            # layers placed near the edge), zero-pad the cropped region.
            mask_crop = np.zeros((bh, bw), dtype=np.uint8)
            src_x0 = max(0, bx)
            src_y0 = max(0, by)
            src_x1 = min(doc_w, bx + bw)
            src_y1 = min(doc_h, by + bh)
            dst_x0 = src_x0 - bx
            dst_y0 = src_y0 - by
            dst_x1 = dst_x0 + (src_x1 - src_x0)
            dst_y1 = dst_y0 + (src_y1 - src_y0)
            if src_x1 > src_x0 and src_y1 > src_y0:
                mask_crop[dst_y0:dst_y1, dst_x0:dst_x1] = \
                    binary[src_y0:src_y1, src_x0:src_x1]

            # Multiply the alpha channel by the mask fraction (mask/255).
            # Feather produces intermediate fractions, which is exactly
            # the soft-alpha behaviour we want.
            alpha_new = (
                src_arr[:, :, 3].astype(np.float32) *
                (mask_crop.astype(np.float32) / 255.0)
            ).astype(np.uint8)
            src_arr[:, :, 3] = alpha_new

            new_layer = doc.createNode(final_name, "paintlayer")
            if not new_layer:
                return {'success': False,
                        'message': 'Failed to create new layer'}

            # Place the new layer directly above the source, not at the
            # top of the document. Matches the duplicate branch and keeps
            # stack coherence for any consumer that expects the extracted
            # layer adjacent to the source.
            parent = target.parentNode()
            if parent is None:
                return {'success': False,
                        'message': 'Source layer has no parent'}
            parent.addChildNode(new_layer, target)

            new_layer.setPixelData(QByteArray(src_arr.tobytes()),
                                   bx, by, bw, bh)

            doc.setActiveNode(new_layer)
            register_node(final_name, new_layer)
            doc.refreshProjection()

            return {'success': True,
                    'message': f'Extracted masked region of "{target.name()}" '
                               f'into "{final_name}"',
                    'data': {'source_layer': target.name(),
                             'new_layer': final_name,
                             'mode': mode,
                             'bounds': {'x': bx, 'y': by,
                                        'w': bw, 'h': bh}}}
        except Exception as e:
            return {'success': False, 'message': str(e)}
