from krita import Krita

class LayerBasicHandler:
    def execute(self, cmd_type, params):
        if cmd_type == 'create_layer':
            return self.create_layer(params)
        elif cmd_type == 'list_layers':
            return self.list_layers()
        elif cmd_type == 'set_active_layer':
            return self.set_active_layer(params)
        elif cmd_type == 'rename_active_layer':
            return self.rename_active_layer(params)
        elif cmd_type == 'rename_layer_by_name':
            return self.rename_layer_by_name(params)
        elif cmd_type == 'move_layer_to_group':
            return self.move_layer_to_group(params)
        elif cmd_type == 'move_active_layer_to_group':
            return self.move_active_layer_to_group(params)
        return {'success': False, 'message': f'Unknown basic layer command: {cmd_type}'}

    def _get_prev_sibling(self, node):
        parent = node.parentNode()
        if not parent:
            return None
        prev_node = None
        for child in parent.childNodes():
            if child == node:
                return prev_node
            prev_node = child
        return prev_node

    def _insert_layer_at_position(self, new_layer, position_type, current_layer, reference_layer_name, doc):
        if position_type == 'above_current' and current_layer:
            current_layer.parentNode().addChildNode(new_layer, current_layer)
        elif position_type == 'below_current' and current_layer:
            parent = current_layer.parentNode()
            children = parent.childNodes()
            for i, child in enumerate(children):
                if child == current_layer:
                    if i == 0:
                        parent.setChildNodes([new_layer] + children)
                    else:
                        parent.addChildNode(new_layer, children[i - 1])
                    break
        elif position_type == 'above_named' and reference_layer_name:
            ref_layer = doc.nodeByName(reference_layer_name)
            if ref_layer:
                ref_layer.parentNode().addChildNode(new_layer, ref_layer)
            else:
                return False
        elif position_type == 'below_named' and reference_layer_name:
            ref_layer = doc.nodeByName(reference_layer_name)
            if not ref_layer:
                return False
            parent = ref_layer.parentNode()
            children = parent.childNodes()
            for i, child in enumerate(children):
                if child == ref_layer:
                    if i == 0:
                        parent.setChildNodes([new_layer] + children)
                    else:
                        parent.addChildNode(new_layer, children[i - 1])
                    break
        elif position_type == 'top':
            doc.rootNode().addChildNode(new_layer, None)
        elif position_type == 'bottom':
            top_level = doc.topLevelNodes()
            if top_level:
                doc.rootNode().setChildNodes([new_layer] + top_level)
            else:
                doc.rootNode().addChildNode(new_layer, None)
        else:
            doc.rootNode().addChildNode(new_layer, None)
        return True

    def create_layer(self, params):
        try:
            doc = Krita.instance().activeDocument()
            if not doc:
                return {'success': False, 'message': 'No active document'}

            name = params.get('name', 'New Layer')
            layer_type = params.get('layer_type', 'paintlayer')
            position = params.get('position', 'above_current')
            reference = params.get('reference', None)
            current = doc.activeNode()

            if layer_type == 'paintlayer':
                new_layer = doc.createNode(name, "paintlayer")
            elif layer_type == 'grouplayer':
                new_layer = doc.createGroupLayer(name)
            elif layer_type == 'selectionmask':
                new_layer = doc.createSelectionMask(name)
            elif layer_type == 'vectorlayer':
                new_layer = doc.createVectorLayer(name)
            elif layer_type == 'filterlayer':
                new_layer = doc.createFilterLayer(name, "", None)
            else:
                new_layer = doc.createNode(name, layer_type)

            if not self._insert_layer_at_position(new_layer, position, current, reference, doc):
                return {'success': False, 'message': f'Failed to insert layer'}

            doc.setActiveNode(new_layer)
            doc.refreshProjection()
            return {'success': True, 'message': f'Created {layer_type} "{name}"'}
        except Exception as e:
            return {'success': False, 'message': str(e)}

    def list_layers(self):
        try:
            doc = Krita.instance().activeDocument()
            if not doc:
                return {'success': False, 'message': 'No active document'}
            layers = []
            self._collect_layer_data(doc.rootNode(), layers, 0)
            return {'success': True, 'message': f'Found {len(layers)} layers', 'data': {'layers': layers}}
        except Exception as e:
            return {'success': False, 'message': str(e)}

    def _collect_layer_data(self, node, layers, indent):
        layer_info = {'name': node.name(), 'type': node.type(), 'indent': indent}
        layers.append(layer_info)
        if node.type() == "grouplayer":
            for child in node.childNodes():
                self._collect_layer_data(child, layers, indent + 1)

    def set_active_layer(self, params):
        try:
            doc = Krita.instance().activeDocument()
            if not doc:
                return {'success': False, 'message': 'No active document'}
            name = params.get('name', '')
            layer = doc.nodeByName(name)
            if not layer:
                return {'success': False, 'message': f'Layer "{name}" not found'}
            doc.setActiveNode(layer)
            doc.refreshProjection()
            return {'success': True, 'message': f'Active layer set to "{name}"'}
        except Exception as e:
            return {'success': False, 'message': str(e)}

    def rename_active_layer(self, params):
        try:
            doc = Krita.instance().activeDocument()
            if not doc:
                return {'success': False, 'message': 'No active document'}
            new_name = params.get('new_name', '')
            active = doc.activeNode()
            if not active:
                return {'success': False, 'message': 'No active layer'}
            old = active.name()
            active.setName(new_name)
            doc.refreshProjection()
            return {'success': True, 'message': f'Renamed from "{old}" to "{new_name}"'}
        except Exception as e:
            return {'success': False, 'message': str(e)}

    def rename_layer_by_name(self, params):
        try:
            doc = Krita.instance().activeDocument()
            if not doc:
                return {'success': False, 'message': 'No active document'}
            old_name = params.get('old_name', '')
            new_name = params.get('new_name', '')
            layer = doc.nodeByName(old_name)
            if not layer:
                return {'success': False, 'message': f'Layer "{old_name}" not found'}
            layer.setName(new_name)
            doc.refreshProjection()
            return {'success': True, 'message': f'Renamed "{old_name}" to "{new_name}"'}
        except Exception as e:
            return {'success': False, 'message': str(e)}

    def move_layer_to_group(self, params):
        try:
            doc = Krita.instance().activeDocument()
            if not doc:
                return {'success': False, 'message': 'No active document'}
            layer_name = params.get('layer_name', '')
            group_name = params.get('group_name', '')
            position = params.get('position', 'inside')

            to_move = doc.nodeByName(layer_name)
            if not to_move:
                return {'success': False, 'message': f'Layer "{layer_name}" not found'}

            target = doc.nodeByName(group_name)
            if not target:
                return {'success': False, 'message': f'Group "{group_name}" not found'}
            if target.type() != "grouplayer":
                return {'success': False, 'message': f'"{group_name}" is not a group'}

            old_parent = to_move.parentNode()
            moved = to_move.duplicate()

            if position == 'inside':
                target.addChildNode(moved, None)
            elif position == 'above':
                old_parent.addChildNode(moved, target)
            elif position == 'below':
                prev = self._get_prev_sibling(target)
                if prev:
                    old_parent.addChildNode(moved, prev)
                else:
                    old_parent.addChildNode(moved, None)

            to_move.remove()
            doc.refreshProjection()
            return {'success': True, 'message': f'Moved "{layer_name}" to "{group_name}"'}
        except Exception as e:
            return {'success': False, 'message': str(e)}

    def move_active_layer_to_group(self, params):
        try:
            doc = Krita.instance().activeDocument()
            if not doc:
                return {'success': False, 'message': 'No active document'}
            group_name = params.get('group_name', '')
            position = params.get('position', 'inside')
            active = doc.activeNode()
            if not active:
                return {'success': False, 'message': 'No active layer'}
            return self.move_layer_to_group({
                'layer_name': active.name(),
                'group_name': group_name,
                'position': position
            })
        except Exception as e:
            return {'success': False, 'message': str(e)}
