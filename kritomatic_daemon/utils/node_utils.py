from krita import *

class NodeUtils:
    @staticmethod
    def find_node_by_name(root, name, node_type=None):
        """Find a node by name, optionally filtering by type"""
        if node_type is None or root.type() == node_type:
            if root.name() == name:
                return root

        for child in root.childNodes():
            result = NodeUtils.find_node_by_name(child, name, node_type)
            if result:
                return result
        return None

    @staticmethod
    def find_transform_mask(root, name):
        """Find a transform mask by name"""
        return NodeUtils.find_node_by_name(root, name, "transformmask")

    @staticmethod
    def find_layer(root, name):
        """Find a layer by name"""
        return NodeUtils.find_node_by_name(root, name)

    @staticmethod
    def get_all_nodes(root, nodes=None):
        """Get all nodes in the document recursively"""
        if nodes is None:
            nodes = []

        nodes.append(root)
        for child in root.childNodes():
            NodeUtils.get_all_nodes(child, nodes)

        return nodes

    @staticmethod
    def get_layer_path(node):
        """Get the full path of a layer (for debugging)"""
        path = [node.name()]
        parent = node.parentNode()
        while parent and parent.type() != "root":
            path.insert(0, parent.name())
            parent = parent.parentNode()
        return "/".join(path)
