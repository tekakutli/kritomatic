"""
floor_plan_png.py — SVG → PNG conversion via cairosvg.

Optional.  If cairosvg is not installed the SVG still gets written;
this module just reports the absence and returns False.
"""

from floor_plan_i18n import T


def try_export(svg_path, png_path, scale=0.5, background="white"):
    try:
        import cairosvg
    except ImportError:
        print(T("pngSkipped"))
        return False
    try:
        cairosvg.svg2png(url=svg_path, write_to=png_path,
                         background_color=background, scale=scale)
        return True
    except Exception as e:
        print(T("pngFailed")(e))
        return False
