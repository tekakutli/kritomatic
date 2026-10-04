"""
Helpers for discovering and matching Krita's QActions.

Krita exposes most of its UI surface as QActions, reachable by name via
Krita.instance().action(name). A single-name lookup is fragile — the
name is version-dependent and some builds register an action on the
menu only, not in the global registry. Enumerating everything Krita
will hand us, and matching by substring, is what makes discovery
robust.

Used by:
  - handlers/document.py, to find the rotate-image action.
  - handlers/introspect.py, to power the list_actions command.
"""

from krita import Krita


def enumerate_actions():
    """
    Collect every QAction Krita will hand us, deduplicated by
    objectName. Sources, in order:
      - Krita's global action registry
      - the active window's own Qt actions
      - two levels of the active window's menu bar

    Actions with an empty objectName are skipped: they cannot be
    matched by name and are almost always QWidget-internal.
    """
    app = Krita.instance()
    found = []
    seen = set()

    def _add(a):
        if a is None:
            return
        oname = a.objectName() or ''
        if not oname:
            return
        if oname in seen:
            return
        seen.add(oname)
        found.append(a)

    try:
        for a in app.actions():
            _add(a)
    except Exception:
        pass

    try:
        win = app.activeWindow()
        if win:
            qwin = win.qwindow()
            if qwin:
                for a in qwin.actions():
                    _add(a)
                mbar = qwin.menuBar()
                if mbar:
                    for menu_action in mbar.actions():
                        menu = menu_action.menu()
                        if not menu:
                            continue
                        for item in menu.actions():
                            sub = item.menu()
                            if sub:
                                for subitem in sub.actions():
                                    _add(subitem)
                            else:
                                _add(item)
    except Exception:
        pass

    return found


def find_rotate_action(app, direction):
    """
    Find an action that rotates the image (not the view or layer stack)
    in the requested direction, and return it. Caller triggers it.

    Matching strategy, in order:
      1. Canonical object names for this Krita version.
      2. Every enumerated action, matching its object name against
         per-direction keyword substrings.
      3. Every enumerated action, matching its displayed text.

    Distractors explicitly rejected: anything whose name or text
    mentions "canvas" or "viewport" (those rotate the viewport, not the
    document), and anything mentioning "layer" (rotateLayerCW90,
    rotateAllLayersCW90). Silently picking one of those would produce
    a plausible-looking but wrong result.
    """
    direction = direction.lower()

    canonical = {
        'cw':  ['rotateImageCW90', 'rotateImageCW',
                'rotate_image_cw', 'rotate_image_90_cw'],
        'ccw': ['rotateImageCCW90', 'rotateImageCCW',
                'rotate_image_ccw', 'rotate_image_90_ccw'],
        '180': ['rotateImage180', 'rotate_image_180'],
    }

    name_keywords = {
        'cw':  ['rotateimagecw', 'rotate_image_cw', 'rotatecw', 'rotate_cw',
                'rotate90', 'rotate_90'],
        'ccw': ['rotateimageccw', 'rotate_image_ccw', 'rotateccw',
                'rotate_ccw', 'rotate270', 'rotate_270'],
        '180': ['rotateimage180', 'rotate_image_180', 'rotate180',
                'rotate_180'],
    }

    text_hints = {
        'cw':  ['clockwise', '90° clockwise', '90 clockwise', 'to the right'],
        'ccw': ['counter-clockwise', 'counterclockwise',
                'counter clockwise', '90° counter', '90 counter',
                'to the left'],
        '180': ['180'],
    }

    def _is_canvas_action(a):
        blob = ((a.objectName() or '') + ' ' + (a.text() or '')).lower()
        return 'canvas' in blob or 'viewport' in blob

    def _is_layer_action(a):
        blob = ((a.objectName() or '') + ' ' + (a.text() or '')).lower()
        return 'layer' in blob

    # Fast path: canonical names.
    for name in canonical.get(direction, []):
        action = app.action(name)
        if action:
            return action

    actions = enumerate_actions()

    # Pass 2: substring match on object name.
    for action in actions:
        if _is_canvas_action(action) or _is_layer_action(action):
            continue
        oname = (action.objectName() or '').lower()
        for kw in name_keywords.get(direction, []):
            if kw in oname:
                return action

    # Pass 3: text match.
    for action in actions:
        if _is_canvas_action(action) or _is_layer_action(action):
            continue
        otext = (action.text() or '').lower()
        oname_l = (action.objectName() or '').lower()
        if 'rotate' not in otext and 'rotate' not in oname_l:
            continue
        for hint in text_hints.get(direction, []):
            if hint in otext:
                return action

    return None
