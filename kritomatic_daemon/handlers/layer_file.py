import os
import random
import string
import xml.etree.ElementTree as ET
from pathlib import Path
from PyQt5.QtWidgets import QApplication
from PyQt5.QtGui import QImage
from PyQt5.QtCore import Qt, QThread
from krita import Krita
from ..utils.refresh import refresh
from ..decorators import command


class LayerFileHandler:
    def execute(self, cmd_type, params):
        if cmd_type == 'create_file_layer':
            return self.create_file_layer(params)
        elif cmd_type == 'convert_to_file_layer':
            return self.convert_to_file_layer(params)
        elif cmd_type == 'embed_image_as_layer':
            return self.embed_image_as_layer(params)
        elif cmd_type == 'import_kra_as_group':
            return self.import_kra_as_group(params)
        elif cmd_type == 'paste_document_transform_as_layer':
            return self.paste_document_transform_as_layer(params)
        return {'success': False, 'message': f'Unknown file layer command: {cmd_type}'}

    @command(
        category='layer',
        help_text='Create a file layer with optional size and position',
        args={
            '--name': {'type': 'str', 'required': True, 'help': 'Layer name'},
            '--file_path': {'type': 'str', 'required': True, 'help': 'Absolute path to the image file'},
            '--width': {'type': 'int', 'required': False, 'help': 'Target width in pixels'},
            '--height': {'type': 'int', 'required': False, 'help': 'Target height in pixels'},
            '--x': {'type': 'float', 'default': 0, 'help': 'X position in pixels'},
            '--y': {'type': 'float', 'default': 0, 'help': 'Y position in pixels'}
        }
    )
    def create_file_layer(self, params):
        try:
            app = Krita.instance()
            doc = app.activeDocument()
            if not doc:
                return {'success': False, 'message': 'No active document'}

            name = params.get('name', 'File Layer')
            file_path = params.get('file_path', '')
            width = params.get('width', None)
            height = params.get('height', None)
            x = params.get('x', 0)
            y = params.get('y', 0)

            if not os.path.exists(file_path):
                return {'success': False, 'message': f'File not found: {file_path}'}

            scaling_method = 'None'
            scaling_filter = 'Bicubic'
            file_layer = doc.createFileLayer(name, file_path, scaling_method, scaling_filter)
            doc.rootNode().addChildNode(file_layer, None)

            QApplication.processEvents()

            if not (width or height or x != 0 or y != 0):
                refresh(doc)
                return {'success': True, 'message': f'Created file layer "{name}"',
                        'data': {'name': name, 'file_path': file_path}}

            qimg = QImage(file_path)
            if qimg.isNull():
                return {'success': False,
                        'message': f'Could not read image dimensions for {file_path}'}
            orig_width = qimg.width()
            orig_height = qimg.height()

            scale_x, scale_y = 1.0, 1.0
            if width and orig_width > 0:
                scale_x = width / orig_width
            if height and orig_height > 0:
                scale_y = height / orig_height
            if width and not height:
                scale_y = scale_x
            if height and not width:
                scale_x = scale_y

            transform_mask_name = f"{name}_transform"
            transform_mask = doc.createTransformMask(transform_mask_name)
            file_layer.addChildNode(transform_mask, None)
            QApplication.processEvents()

            xml_str = transform_mask.toXML()
            root = ET.fromstring(xml_str)
            for elem in root.findall('.//scaleX'):
                elem.set('value', str(scale_x))
            for elem in root.findall('.//scaleY'):
                elem.set('value', str(scale_y))
            for elem in root.findall('.//flattenedPerspectiveTransform'):
                elem.set('m31', str(x))
                elem.set('m32', str(y))
            for elem in root.findall('.//transformedCenter'):
                elem.set('x', str(x))
                elem.set('y', str(y))
            transform_mask.fromXML(ET.tostring(root, encoding='unicode'))
            QApplication.processEvents()

            doc.setActiveNode(transform_mask)
            refresh(doc)
            QApplication.processEvents()

            return {
                'success': True,
                'message': f'Created file layer "{name}" with transform',
                'data': {
                    'name': name, 'file_path': file_path,
                    'width': width, 'height': height, 'position': (x, y),
                    'scale': (scale_x, scale_y), 'transform_mask': transform_mask_name
                }
            }
        except Exception as e:
            return {'success': False, 'message': str(e)}

    @command(
        category='layer',
        help_text='Embed an image file directly into a paint layer (pixels baked in, no external reference)',
        args={
            '--name': {'type': 'str', 'required': True, 'help': 'Layer name'},
            '--file_path': {'type': 'str', 'required': True, 'help': 'Path to the image file'},
            '--width': {'type': 'int', 'required': False, 'help': 'Target width in pixels'},
            '--height': {'type': 'int', 'required': False, 'help': 'Target height in pixels'},
            '--x': {'type': 'float', 'default': 0, 'help': 'X position in pixels'},
            '--y': {'type': 'float', 'default': 0, 'help': 'Y position in pixels'}
        }
    )
    def embed_image_as_layer(self, params):
        """
        Read an image, scale it to (width, height), blit the pixels into
        a fresh paint layer at (x, y). The result is a self-contained
        document — nothing on disk is referenced at open time.

        The image is composited into a full-document-sized QImage
        before setPixelData, so the new layer's extent covers the
        whole canvas.  Writing pixel data at (0, 0) with the full
        document dimensions sidesteps the case where a new layer's
        bounds start at the origin and a paste landing further down
        the canvas would be silently discarded.
        """
        try:
            app = Krita.instance()
            doc = app.activeDocument()
            if not doc:
                return {'success': False, 'message': 'No active document'}

            name = params.get('name', 'Embedded')
            file_path = params.get('file_path', '')
            width = params.get('width', None)
            height = params.get('height', None)
            x = int(round(params.get('x', 0)))
            y = int(round(params.get('y', 0)))

            if not os.path.exists(file_path):
                return {'success': False, 'message': f'File not found: {file_path}'}

            img = QImage(file_path)
            if img.isNull():
                return {'success': False, 'message': f'Could not read image: {file_path}'}

            if width or height:
                tw = int(width) if width else img.width()
                th = int(height) if height else img.height()
                img = img.scaled(tw, th, Qt.IgnoreAspectRatio, Qt.SmoothTransformation)

            img = img.convertToFormat(QImage.Format_ARGB32)
            iw = img.width()
            ih = img.height()

            B_W = doc.width()
            B_H = doc.height()

            full = QImage(B_W, B_H, QImage.Format_ARGB32)
            full.fill(0)
            from PyQt5.QtGui import QPainter
            painter = QPainter(full)
            painter.drawImage(x, y, img)
            painter.end()
            full = full.convertToFormat(QImage.Format_ARGB32)
            fw = full.width()
            fh = full.height()
            ptr = full.constBits()
            ptr.setsize(full.byteCount())
            raw = bytes(ptr)

            layer = doc.createNode(name, "paintlayer")
            doc.rootNode().addChildNode(layer, None)
            layer.setPixelData(raw, 0, 0, fw, fh)

            doc.setActiveNode(layer)
            refresh(doc)

            return {
                'success': True,
                'message': f'Embedded image into "{name}" ({iw}x{ih}) at ({x}, {y})',
                'data': {'name': name, 'width': iw, 'height': ih, 'x': x, 'y': y}
            }
        except Exception as e:
            return {'success': False, 'message': str(e)}

    @command(
        category='layer',
        help_text='Import all layers of a source .kra into the active document as a group',
        args={
            '--name': {'type': 'str', 'required': True, 'help': 'Group layer name'},
            '--file_path': {'type': 'str', 'required': True, 'help': 'Path to source .kra'}
        }
    )
    def import_kra_as_group(self, params):
        try:
            app = Krita.instance()
            target_doc = app.activeDocument()
            if not target_doc:
                return {'success': False, 'message': 'No active document'}

            name = params.get('name', 'Imported')
            file_path = params.get('file_path', '')

            if not os.path.exists(file_path):
                return {'success': False, 'message': f'File not found: {file_path}'}

            src_doc = app.openDocument(file_path)
            if not src_doc:
                return {'success': False, 'message': f'Failed to open {file_path}'}

            src_w = src_doc.width()
            src_h = src_doc.height()
            layer_count = 0

            src_batch_was = src_doc.batchmode()
            src_doc.setBatchmode(True)
            try:
                group = target_doc.createGroupLayer(name)

                for child in src_doc.rootNode().childNodes():
                    dup = child.duplicate()
                    group.addChildNode(dup, None)
                    layer_count += 1

                target_doc.rootNode().addChildNode(group, None)
                target_doc.setActiveNode(group)
                refresh(target_doc)
            finally:
                src_doc.setBatchmode(src_batch_was)
                src_doc.close()

            return {
                'success': True,
                'message': f'Imported {os.path.basename(file_path)} as group "{name}" '
                           f'({layer_count} layer(s), source {src_w}x{src_h})',
                'data': {
                    'name': name,
                    'source_size': (src_w, src_h),
                    'layer_count': layer_count,
                }
            }
        except Exception as e:
            return {'success': False, 'message': str(e)}

    @command(
        category='layer',
        help_text='Convert a regular layer to a file layer (exports to .kra and re-imports)',
        args={
            '--layer_name': {'type': 'str', 'required': True, 'help': 'Name of the layer to convert'},
            '--output_path': {'type': 'str', 'required': False, 'help': 'Path to save the exported file (auto-generated if not provided)'}
        }
    )
    def convert_to_file_layer(self, params):
        try:
            app = Krita.instance()
            doc = app.activeDocument()
            if not doc:
                return {'success': False, 'message': 'No active document'}

            layer_name = params.get('layer_name', '')
            output_path = params.get('output_path', None)

            src_layer = doc.nodeByName(layer_name)
            if not src_layer:
                return {'success': False, 'message': f'Layer "{layer_name}" not found'}

            original_parent = src_layer.parentNode()
            children = original_parent.childNodes()
            original_position = None
            for i, child in enumerate(children):
                if child == src_layer:
                    original_position = i
                    break

            random_hash = ''.join(random.choices(string.ascii_lowercase + string.digits, k=8))

            export_dir = None
            if not output_path:
                doc_path = doc.fileName()
                if doc_path:
                    doc_dir = Path(doc_path).parent
                    doc_name = Path(doc_path).stem
                else:
                    doc_dir = Path.home()
                    doc_name = "untitled"

                export_dir = doc_dir / f"{doc_name}_layers"
                export_dir.mkdir(exist_ok=True)
                output_path = str(export_dir / f"{random_hash}.kra")
            elif not output_path.endswith('.kra'):
                output_path += '.kra'

            bounds = src_layer.bounds()
            width = bounds.width() if bounds.width() > 0 else doc.width()
            height = bounds.height() if bounds.height() > 0 else doc.height()

            temp_doc = app.createDocument(
                width, height, "__temp_export__",
                doc.colorModel(), doc.colorDepth(),
                doc.colorProfile(), doc.resolution()
            )

            duplicated_layer = src_layer.duplicate()
            temp_doc.rootNode().addChildNode(duplicated_layer, None)
            refresh(temp_doc)
            temp_doc.saveAs(output_path)
            temp_doc.close()

            src_layer.remove()

            group_layer = doc.createGroupLayer(layer_name)
            scaling_method = 'ToImageSize'
            scaling_filter = 'Bicubic'
            file_layer = doc.createFileLayer(random_hash, output_path, scaling_method, scaling_filter)
            group_layer.addChildNode(file_layer, None)

            current_children = original_parent.childNodes()
            if original_position is not None and original_position < len(current_children):
                target_sibling = current_children[original_position]
                original_parent.addChildNode(group_layer, target_sibling)
            else:
                original_parent.addChildNode(group_layer, None)

            doc.setActiveNode(group_layer)
            refresh(doc)

            return {
                'success': True,
                'message': f'Converted layer "{layer_name}" to group containing file layer',
                'data': {
                    'layer_name': layer_name, 'file_layer_name': random_hash,
                    'random_hash': random_hash, 'output_path': output_path,
                    'export_dir': str(export_dir) if export_dir else None,
                    'position': original_position
                }
            }
        except Exception as e:
            return {'success': False, 'message': str(e)}

    @command(
        category='layer',
        help_text='Apply an affine transform to one open document\'s composite '
                  'and paste the result into another as a new paint layer.  '
                  'Documents are identified by their .kra path.',
        args={
            '--name': {'type': 'str', 'required': True,
                       'help': 'Name for the new layer'},
            '--source_document': {'type': 'str', 'required': True,
                                  'help': 'Path of the .kra to copy from'},
            '--target_document': {'type': 'str', 'required': True,
                                  'help': 'Path of the .kra to paste into'},
            '--a': {'type': 'float', 'required': True,
                    'help': 'Affine matrix a (x\' = a*x + c*y + e)'},
            '--b': {'type': 'float', 'required': True,
                    'help': 'Affine matrix b (y\' = b*x + d*y + f)'},
            '--c': {'type': 'float', 'required': True,
                    'help': 'Affine matrix c'},
            '--d': {'type': 'float', 'required': True,
                    'help': 'Affine matrix d'},
            '--e': {'type': 'float', 'required': True,
                    'help': 'Affine matrix e'},
            '--f': {'type': 'float', 'required': True,
                    'help': 'Affine matrix f'},
        }
    )
    def paste_document_transform_as_layer(self, params):
        """Render the source document, apply an affine transform
        mapping its pixels into the target document's space, and add
        the result as a new paint layer.

        The transform is a 2x3 column-vector affine map:
            x' = a*x + c*y + e
            y' = b*x + d*y + f
        QTransform takes its elements in row-vector order, so the
        constructor call is QTransform(a, b, 0, c, d, 0, e, f, 1).

        The transformed source is drawn into a full-target-canvas
        QImage at the correct offset, and that image is written into
        a single new paint layer at (0, 0) with the target's full
        dimensions.  Doing the write at the origin with the full
        canvas extent means the layer's bounds cover every pixel of
        the paste, including one that lands near the bottom or right
        edge of the target; a write at a non-zero offset would be
        confined to the layer's initial (empty, origin-anchored)
        bounds and discarded.
        """
        import math
        from PyQt5.QtGui import QPainter, QTransform as QTx
        from PyQt5.QtCore import QPointF

        try:
            app = Krita.instance()
            name    = params.get('name', 'Pasted Region')
            src_key = params.get('source_document', '')
            tgt_key = params.get('target_document', '')

            print(f"[board/paste] enter: src={src_key!r} tgt={tgt_key!r}",
                  flush=True)

            def _resolve(candidate):
                try:
                    return os.path.realpath(os.path.expanduser(str(candidate)))
                except Exception:
                    return str(candidate)

            def _find(key):
                if not key:
                    return None
                target_norm = _resolve(key)
                for d in app.documents():
                    if d.name() == key:
                        return d
                    try:
                        fn = d.fileName()
                    except Exception:
                        fn = ''
                    if fn and _resolve(fn) == target_norm:
                        return d
                return None

            src_doc = _find(src_key)
            tgt_doc = _find(tgt_key)
            print(f"[board/paste] src resolved to "
                  f"{src_doc.fileName() if src_doc else None!r}, "
                  f"tgt resolved to "
                  f"{tgt_doc.fileName() if tgt_doc else None!r}",
                  flush=True)

            if not src_doc:
                return {'success': False,
                        'message': f'Source document not open: {src_key}'}
            if not tgt_doc:
                return {'success': False,
                        'message': f'Target document not open: {tgt_key}'}

            a = float(params.get('a', 1.0))
            b = float(params.get('b', 0.0))
            c = float(params.get('c', 0.0))
            d = float(params.get('d', 1.0))
            e = float(params.get('e', 0.0))
            f = float(params.get('f', 0.0))

            det = a * d - b * c
            if abs(det) < 1e-9:
                return {'success': False,
                        'message': f'Affine matrix is degenerate (det={det})'}

            A_W = src_doc.width()
            A_H = src_doc.height()
            B_W = tgt_doc.width()
            B_H = tgt_doc.height()
            print(f"[board/paste] source canvas {A_W}x{A_H}, "
                  f"target canvas {B_W}x{B_H}", flush=True)

            # Source composite.  projection() is ideal but can return
            # null off the main thread; thumbnail() works from any
            # thread and is scaled up to the true canvas size.
            src_img = None
            try:
                src_img = src_doc.projection()
            except Exception as exc:
                print(f"[board/paste] projection() raised: {exc}", flush=True)
                src_img = None

            if src_img is None or src_img.isNull():
                print(f"[board/paste] projection() null, "
                      f"falling back to thumbnail({A_W}, {A_H})", flush=True)
                src_img = src_doc.thumbnail(A_W, A_H)

            if src_img is None or src_img.isNull():
                return {'success': False,
                        'message': 'Source composite returned null'}

            if src_img.width() != A_W or src_img.height() != A_H:
                print(f"[board/paste] composite is "
                      f"{src_img.width()}x{src_img.height()}, "
                      f"scaling to {A_W}x{A_H}", flush=True)
                src_img = src_img.scaled(
                    A_W, A_H,
                    Qt.IgnoreAspectRatio,
                    Qt.SmoothTransformation,
                )
                if src_img.isNull():
                    return {'success': False,
                            'message': 'Rescaling source composite returned null'}

            # QTransform takes its elements in row-vector order.
            T = QTx(a, b, 0.0, c, d, 0.0, e, f, 1.0)

            # Where does the source rectangle land in the target?
            corners = [
                T.map(QPointF(0.0,         0.0)),
                T.map(QPointF(float(A_W),  0.0)),
                T.map(QPointF(float(A_W),  float(A_H))),
                T.map(QPointF(0.0,         float(A_H))),
            ]
            min_x = min(p.x() for p in corners)
            min_y = min(p.y() for p in corners)
            max_x = max(p.x() for p in corners)
            max_y = max(p.y() for p in corners)

            clip_x0 = max(0, int(math.floor(min_x)))
            clip_y0 = max(0, int(math.floor(min_y)))
            clip_x1 = min(B_W, int(math.ceil(max_x)))
            clip_y1 = min(B_H, int(math.ceil(max_y)))

            if clip_x1 <= clip_x0 or clip_y1 <= clip_y0:
                return {'success': False,
                        'message': 'Transformed source lies entirely '
                                   'outside the target canvas'}

            print(f"[board/paste] transformed bbox in target: "
                  f"({clip_x0}, {clip_y0}) {clip_x1 - clip_x0}x{clip_y1 - clip_y0}",
                  flush=True)

            # Draw the transformed source into a full-canvas image.
            # The transform places src_img pixel (px, py) at target
            # pixel T.map((px, py)); painter clipping handles the
            # parts that land outside the canvas.
            full = QImage(B_W, B_H, QImage.Format_ARGB32)
            full.fill(0)
            painter = QPainter(full)
            painter.setRenderHint(QPainter.SmoothPixmapTransform)
            painter.setTransform(T)
            painter.drawImage(0, 0, src_img)
            painter.end()

            full = full.convertToFormat(QImage.Format_ARGB32)
            fw = full.width()
            fh = full.height()
            ptr = full.constBits()
            ptr.setsize(full.byteCount())
            raw = bytes(ptr)
            if not raw:
                return {'success': False,
                        'message': 'Transformed pixel buffer came back empty'}

            layer = tgt_doc.createNode(name, "paintlayer")
            if layer is None:
                return {'success': False,
                        'message': f'createNode returned None for {name!r}'}
            tgt_doc.rootNode().addChildNode(layer, None)

            print(f"[board/paste] writing full {fw}x{fh} layer",
                  flush=True)

            layer.setPixelData(raw, 0, 0, fw, fh)

            try:
                prev_batch = tgt_doc.batchmode()
            except Exception:
                prev_batch = False
            try:
                if prev_batch:
                    tgt_doc.setBatchmode(False)
                tgt_doc.refreshProjection()
                try:
                    QThread.msleep(50)
                except Exception:
                    pass
                tgt_doc.refreshProjection()
                print("[board/paste] composited target document", flush=True)
            except Exception as exc:
                print(f"[board/paste] composite raised: {exc}", flush=True)
            finally:
                if prev_batch:
                    try:
                        tgt_doc.setBatchmode(True)
                    except Exception:
                        pass

            print(f"[board/paste] done: layer={layer.name()!r}", flush=True)

            return {
                'success': True,
                'message': f'Pasted transformed region from '
                           f'"{src_doc.name() or src_key}" into '
                           f'"{tgt_doc.name() or tgt_key}" as "{name}"',
                'data': {
                    'name':            name,
                    'source_document': src_doc.fileName() or src_key,
                    'target_document': tgt_doc.fileName() or tgt_key,
                    'matrix':          (a, b, c, d, e, f),
                    'paste_bbox':      (clip_x0, clip_y0,
                                        clip_x1 - clip_x0, clip_y1 - clip_y0),
                    'layer_size':      (fw, fh),
                },
            }
        except Exception as exc:
            import traceback as _tb
            _tb.print_exc()
            return {'success': False,
                    'message': f'paste_document_transform_as_layer raised: {exc}'}
