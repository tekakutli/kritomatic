from PyQt5.QtGui import QColor

class ColorUtils:
    @staticmethod
    def hex_to_rgb(hex_color):
        """Convert hex color (e.g., '#ff0000') to RGB tuple (0-255)"""
        hex_color = hex_color.lstrip('#')
        r = int(hex_color[0:2], 16)
        g = int(hex_color[2:4], 16)
        b = int(hex_color[4:6], 16)
        return (r, g, b)

    @staticmethod
    def hex_to_normalized(hex_color):
        """Convert hex color to normalized RGB (0.0-1.0)"""
        r, g, b = ColorUtils.hex_to_rgb(hex_color)
        return (r/255.0, g/255.0, b/255.0, 1.0)

    @staticmethod
    def rgb_to_hex(r, g, b):
        """Convert RGB to hex string"""
        return f"#{r:02x}{g:02x}{b:02x}"

    @staticmethod
    def extract_color_info(managed_color):
        """Extract readable color info from ManagedColor object"""
        try:
            if hasattr(managed_color, 'toQColor'):
                qcolor = managed_color.toQColor()
                return {
                    'hex': qcolor.name(),
                    'rgb': (qcolor.red(), qcolor.green(), qcolor.blue()),
                    'alpha': qcolor.alpha()
                }
            elif hasattr(managed_color, 'qcolor'):
                qcolor = managed_color.qcolor()
                return {
                    'hex': qcolor.name(),
                    'rgb': (qcolor.red(), qcolor.green(), qcolor.blue()),
                    'alpha': qcolor.alpha()
                }
            else:
                return {'raw': str(managed_color)}
        except:
            return {'raw': str(managed_color)}
