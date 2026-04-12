from krita import *

class LayerUtils:
    @staticmethod
    def get_prev_sibling(node):
        """Get the previous sibling of a node (layer below visually)"""
        parent = node.parentNode()
        if not parent:
            return None
        prev_node = None
        for child in parent.childNodes():
            if child == node:
                return prev_node
            prev_node = child
        return prev_node

    @staticmethod
    def get_next_sibling(node):
        """Get the next sibling of a node (layer above visually)"""
        parent = node.parentNode()
        if not parent:
            return None
        found = False
        for child in parent.childNodes():
            if found:
                return child
            if child == node:
                found = True
        return None

    @staticmethod
    def insert_at_bottom(parent, new_layer):
        """Insert a layer at the visual bottom using setChildNodes"""
        children = parent.childNodes()
        if children:
            parent.setChildNodes([new_layer] + children)
        else:
            parent.addChildNode(new_layer, None)

    @staticmethod
    def insert_at_top(parent, new_layer):
        """Insert a layer at the visual top"""
        parent.addChildNode(new_layer, None)

    @staticmethod
    def insert_above(parent, new_layer, reference):
        """Insert a layer above the reference layer"""
        parent.addChildNode(new_layer, reference)

    @staticmethod
    def insert_below(parent, new_layer, reference):
        """Insert a layer below the reference layer using setChildNodes if needed"""
        children = parent.childNodes()
        for i, child in enumerate(children):
            if child == reference:
                if i == 0:
                    # Reference is at bottom, insert as new bottom
                    parent.setChildNodes([new_layer] + children)
                else:
                    parent.addChildNode(new_layer, children[i - 1])
                return True
        return False

    @staticmethod
    def collect_layer_data(node, layers, indent=0):
        """Recursively collect layer data for listing"""
        layer_info = {
            'name': node.name(),
            'type': node.type(),
            'indent': indent,
            'visible': node.visible() if hasattr(node, 'visible') else True
        }
        layers.append(layer_info)

        if node.type() == "grouplayer":
            for child in node.childNodes():
                LayerUtils.collect_layer_data(child, layers, indent + 1)
