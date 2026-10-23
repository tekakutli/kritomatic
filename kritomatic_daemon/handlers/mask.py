import base64
import uuid
import numpy as np
from PyQt6.QtGui import QImage
from PyQt6.QtCore import QByteArray, QBuffer, QIODevice, Qt

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

    # ------------------------------------------------------------------
    # Import masks from Krita into the playground's mask stack
    # ------------------------------------------------------------------
    #
    # Reads one or more Krita layers as grayscale PNG masks. The
    # playground uses this to pull masks that already exist in the
    # user's document — a hand-painted silhouette, a transparency mask
    # applied to a layer, a selection the user made before opening the
    # playground — into its own mask stack for refinement or export.
    #
    # Two kinds of layer are usable as a mask:
    #
    #   - mask-type layers (transparencymask, selectionmask): their
    #     grayscale pixel data IS the mask, taken directly.
    #
    #   - image-bearing layers (paint, group, file, vector, clone,
    #     fill, filter): their projection's alpha channel is the mask,
    #     so "what is opaque" becomes "what is selected".
    #
    # Which layers get read:
    #
    #   - If `layer_names` is given, exactly those, in order.
    #   - Otherwise, the currently selected layer(s) in Krita. Krita's
    #     Python API exposes multi-layer selection inconsistently
    #     across versions; this command tries four accessors in order
    #     and falls back to `Document.activeNode()`. The response
    #     message reflects which path succeeded so the caller can tell
    #     "I selected three masks and got three" from "multi-select
    #     detection wasn't available, only the active one came through".
    #
    # Dimension note: masks are returned at the document's full canvas
    # dimensions, matching what the playground image will be when the
    # user imported it from Krita. If the playground image has a
    # different size, the JS side scales masks at use time (the same
    # way masks from Segment All do), so this is safe but the caller
    # should warn about mismatches.

    @command(
        category='mask',
        help_text='Read one or more Krita layers as base64 PNG masks. '
                  'Used by the playground to import masks that already '
                  'exist in the document into its mask stack.',
        args={
            '--layer_names': {'type': 'str', 'required': False,
                              'help': 'Comma-separated layer names. Empty '
                                      '= use the selected layer(s) in '
                                      'Krita, falling back to the active '
                                      'layer if multi-selection detection '
                                      'is unavailable.'},
            '--include_paint_alpha': {'type': 'bool', 'default': True,
                                      'help': 'For paint/group layers, '
                                              'use the alpha channel as a '
                                              'mask. Set false to import '
                                              'only actual mask layers '
                                              '(transparency / selection).'},
        },
    )
    def import_krita_masks_b64(self, params):
        try:
            doc = Krita.instance().activeDocument()
            if not doc:
                return {'success': False, 'message': 'No active document'}

            names_raw = params.get('layer_names', '') or ''
            names = [p.strip() for p in str(names_raw).split(',') if p.strip()]
            include_paint_alpha = bool(params.get('include_paint_alpha', True))

            # Pick which nodes to read
            selection_path = 'layer_names'
            if names:
                nodes = []
                for name in names:
                    node = resolve_node(doc, name)
                    if node:
                        nodes.append(node)
            else:
                nodes, selection_path = self._get_selected_or_active_nodes(doc)

            if not nodes:
                return {'success': False,
                        'message': 'No layers selected and no layer names '
                                   'given.'}

            doc_w = int(doc.width())
            doc_h = int(doc.height())
            if doc_w <= 0 or doc_h <= 0:
                return {'success': False,
                        'message': f'Document has no drawable extent '
                                   f'({doc_w}x{doc_h}).'}

            masks = []
            skipped = []

            for node in nodes:
                name = node.name() or '(unnamed)'
                ntype = node.type() or ''
                try:
                    result = self._read_node_as_mask(
                        node, doc_w, doc_h, include_paint_alpha
                    )
                except Exception as e:
                    skipped.append({'name': name, 'reason': str(e)})
                    continue

                if result is None:
                    skipped.append({
                        'name': name,
                        'reason': f'layer type {ntype!r} cannot be read as '
                                  f'a mask'
                                  + ('' if include_paint_alpha
                                     else ' (paint-alpha disabled)'),
                    })
                    continue

                mask_b64, kind = result
                masks.append({
                    'name': name,
                    'type': ntype,
                    'source': kind,   # 'mask' for mask layers, 'alpha' for image layers
                    'width': doc_w,
                    'height': doc_h,
                    'mask_b64': mask_b64,
                })

            msg = f'Imported {len(masks)} mask(s)'
            if skipped:
                msg += f', {len(skipped)} skipped'
            if selection_path == 'active_only':
                msg += ' (multi-selection unavailable; imported active layer)'

            return {
                'success': True,
                'message': msg,
                'data': {
                    'masks': masks,
                    'skipped': skipped,
                    'selection_path': selection_path,
                },
            }
        except Exception as e:
            return {'success': False, 'message': str(e)}

    def _get_selected_or_active_nodes(self, doc):
        """Best-effort multi-layer selection detection.

        Krita's Python API exposes `activeNode()` reliably but not
        multi-layer selection consistently. Different Krita builds
        expose the selection state through different accessors. This
        method tries four candidates in order:

          1. View.selectedNodes()      — the view object's own list
          2. Document.selectedNodes()  — the document object's own list
          3. Node.isSelected() walk    — per-node selection predicate
          4. Fallback: activeNode()    — always available, single node

        Returns (nodes, path_label), where path_label is one of
        'view.selectedNodes', 'doc.selectedNodes', 'node.isSelected',
        or 'active_only'.
        """
        # 1. View.selectedNodes()
        try:
            view = Krita.instance().activeWindow().activeView()
            if view is not None and hasattr(view, 'selectedNodes'):
                try:
                    nodes = list(view.selectedNodes())
                    if nodes:
                        return nodes, 'view.selectedNodes'
                except Exception:
                    pass
        except Exception:
            pass

        # 2. Document.selectedNodes()
        try:
            if hasattr(doc, 'selectedNodes'):
                try:
                    nodes = list(doc.selectedNodes())
                    if nodes:
                        return nodes, 'doc.selectedNodes'
                except Exception:
                    pass
        except Exception:
            pass

        # 3. Node.isSelected() walk
        try:
            acc = []
            def walk(node):
                try:
                    if hasattr(node, 'isSelected') and node.isSelected():
                        acc.append(node)
                except Exception:
                    pass
                try:
                    for child in node.childNodes():
                        walk(child)
                except Exception:
                    pass
            walk(doc.rootNode())
            if acc:
                return acc, 'node.isSelected'
        except Exception:
            pass

        # 4. Fallback
        active = doc.activeNode()
        if active:
            return [active], 'active_only'
        return [], 'active_only'

    def _read_node_as_mask(self, node, doc_w, doc_h, include_paint_alpha):
        """Read one layer as a grayscale PNG mask.

        Returns (mask_b64, kind) where kind is 'mask' or 'alpha', or
        None if the layer cannot be read as a mask.

        mask-type layers (transparencymask, selectionmask):
            read pixelData directly. Krita returns grayscale bytes at
            full canvas extent, padded with 0 (unselected) outside the
            mask's own bounds, which matches the playground's
            full-canvas mask convention.

        image-bearing layers (paint, group, file, vector, clone, fill,
        filter):
            read projectionPixelData (RGBA), take every 4th byte (alpha
            channel) as the mask. Enabled by `include_paint_alpha`.
        """
        if node is None or doc_w <= 0 or doc_h <= 0:
            return None

        ntype = node.type() or ''
        mask_types = ('transparencymask', 'selectionmask')
        image_types = ('paintlayer', 'grouplayer', 'filelayer',
                       'vectorlayer', 'clonelayer', 'filllayer',
                       'filterlayer')

        if ntype in mask_types:
            try:
                raw = bytes(node.pixelData(0, 0, doc_w, doc_h))
            except Exception:
                return None
            if len(raw) != doc_w * doc_h:
                return None
            kind = 'mask'
            img = QImage(raw, doc_w, doc_h, doc_w,
                         QImage.Format.Format_Grayscale8).copy()
        elif ntype in image_types and include_paint_alpha:
            try:
                raw = bytes(node.projectionPixelData(0, 0, doc_w, doc_h))
            except Exception:
                return None
            if len(raw) != doc_w * doc_h * 4:
                return None
            # Every 4th byte starting at offset 3 is the alpha channel.
            # Building a bytes slice of length doc_w * doc_h matches what
            # Format_Grayscale8 expects as a full-canvas mask.
            alpha = raw[3::4]
            if len(alpha) != doc_w * doc_h:
                return None
            kind = 'alpha'
            img = QImage(alpha, doc_w, doc_h, doc_w,
                         QImage.Format.Format_Grayscale8).copy()
        else:
            return None

        if img is None or img.isNull():
            return None

        buf = QBuffer()
        buf.open(QIODevice.OpenModeFlag.WriteOnly)
        ok = img.save(buf, "PNG")
        png = bytes(buf.data())
        buf.close()
        if not ok or not png:
            return None

        return base64.b64encode(png).decode('ascii'), kind

    # ------------------------------------------------------------------
    # Mask-as-paint-layer
    # ------------------------------------------------------------------
    #
    # Creates a paint layer whose visible pixels are exactly the mask:
    # white where the mask is selected, fully transparent where it is
    # not. This is what the playground's "Export selected" / "Export
    # combined" buttons invoke when "To Krita" is checked, so that
    # clicking "Export" drops the masks straight into the document
    # rather than saving PNGs to the user's Downloads directory.
    #
    # Two placement modes, controlled by `above_layer`:
    #
    #   ""        (empty)   the new layer goes at the top of the
    #                        document root.
    #   "@active"           the new layer is placed directly above
    #                        the currently active layer. If no layer
    #                        is active, falls back to top.
    #   "<name>"            the new layer is placed directly above the
    #                        named layer. If the name doesn't resolve,
    #                        the command fails with a clear message
    #                        rather than silently misplacing the layer.
    #
    # Returns data.layer_name so a caller that batches multiple masks
    # (one layer each, as "Export selected" does) can chain follow-up
    # commands against each individual result.
    #
    # Feather and the other edge-cleanup args are honoured; a feathered
    # mask becomes a soft-edged white paint layer, which is directly
    # useful as a brush-on overlay without further processing.

    @command(
        category='mask',
        help_text='Create a paint layer whose pixels are the mask '
                  '(white on transparent), optionally placed above a '
                  'named layer or the active layer.',
        args={
            '--mask_b64': {'type': 'str', 'required': True,
                           'help': 'Base64 PNG mask (white = selected)'},
            '--name': {'type': 'str', 'default': '',
                       'help': 'Layer name; empty = auto-generated '
                               'Mask_<uuid>.'},
            '--above_layer': {'type': 'str', 'default': '',
                              'help': 'Place the new layer above this '
                                      'layer. Empty = top of stack. '
                                      '"@active" = above the active '
                                      'layer.'},
            '--threshold': {'type': 'int', 'default': 128,
                            'help': 'Pixel intensity threshold (0-255)'},
            '--invert': {'type': 'bool', 'default': False,
                         'help': 'Invert the mask before painting it'},
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
    def create_paint_layer_from_mask_b64(
        self,
        mask_b64="",
        name="",
        above_layer="",
        threshold=128,
        invert=False,
        expand_pixels=0,
        contract_pixels=0,
        smooth_iterations=0,
        feather_radius=0,
    ):
        try:
            doc = Krita.instance().activeDocument()
            if not doc:
                return {'success': False, 'message': 'No active document'}
            if not mask_b64:
                return {'success': False, 'message': 'mask_b64 required'}

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

            # RGBA layout: white everywhere, alpha = mask value. When
            # the mask is binary (no feather), each pixel is either
            # fully opaque white (selected) or fully transparent
            # (not selected). Feather produces intermediate alpha,
            # which is exactly the soft-edge paint layer we want.
            h, w = binary.shape
            rgba = np.zeros((h, w, 4), dtype=np.uint8)
            rgba[:, :, 0] = 255
            rgba[:, :, 1] = 255
            rgba[:, :, 2] = 255
            rgba[:, :, 3] = binary

            layer_name = name or f"Mask_{uuid.uuid4().hex[:8]}"
            new_layer = doc.createNode(layer_name, "paintlayer")
            if not new_layer:
                return {'success': False, 'message': 'Failed to create layer'}

            # Resolve placement. Three cases:
            #   ""        → top of stack
            #   "@active" → above the currently active layer (or top
            #               if no active layer)
            #   "<name>"  → above that layer; error if not found
            placement = 'top'
            above_node = None
            if above_layer == "@active":
                above_node = doc.activeNode()
                if above_node is not None:
                    placement = f'above_active ({above_node.name()})'
                else:
                    placement = 'top (no active layer)'
            elif above_layer:
                above_node = resolve_node(doc, above_layer)
                if above_node is None:
                    return {'success': False,
                            'message': f'above_layer "{above_layer}" not found'}
                placement = f'above "{above_layer}"'

            if above_node is not None:
                parent = above_node.parentNode()
                if parent is None:
                    return {'success': False,
                            'message': 'Reference layer has no parent'}
                parent.addChildNode(new_layer, above_node)
            else:
                doc.rootNode().addChildNode(new_layer, None)

            new_layer.setPixelData(QByteArray(rgba.tobytes()), 0, 0, w, h)

            doc.setActiveNode(new_layer)
            register_node(layer_name, new_layer)
            doc.refreshProjection()

            return {
                'success': True,
                'message': f'Created mask paint layer "{layer_name}" ({placement})',
                'data': {
                    'layer_name': layer_name,
                    'placement': placement,
                    'width': w,
                    'height': h,
                },
            }
        except Exception as e:
            return {'success': False, 'message': str(e)}
