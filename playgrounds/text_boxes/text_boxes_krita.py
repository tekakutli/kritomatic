"""
text_boxes_krita.py — Python-side bridge from this process to the
running Kritomatic daemon.

Five pieces are shared with the `board` playground and live in
`pgruntime.krita_api`: the `KritaClient` import bootstrap, the
per-command wrapper, `open_file`, `activate_document`, and
`pick_files`.  The wrappers below pin `log_prefix` to
`"text_boxes_krita"` so log lines from this playground stay tagged
consistently with the rest of the tree.

The two text_boxes-specific commands — `dump_all_text_shapes` and
`patch_layer_text` — stay here.  They follow the same
`err is not None` failure convention as `pgruntime.krita_api.call`;
see that module's docstring for why `data is None` is the wrong
test.

BUGS FIXED BY THIS PORT
=======================
The previous version of this module checked `if data is None:` in
four places to detect failure.  `_call` returns `(None, None)` on
success for commands whose daemon response has `data: null` (no
payload), so the check fired on *success* for `activate_document`,
producing `{success: false, message: "unknown"}` while Krita
actually activated the document.  `board_krita` already used the
correct `err is not None` check; text_boxes never received the fix.

The four call sites now follow the shared convention.  Payload
access uses `(data or {})` so a `None` payload on success does not
raise.

CALLER-VISIBLE EFFECT
=====================
`/krita/activate` now returns `{"success": true, "message":
"Activated <name>"}` on success instead of `{"success": false,
"message": "unknown"}`.  The current JS in `tbox_krita.py`'s
`activateItemDocument` fires the fetch and never inspects the body,
so no UI behaviour changes today.  Any future caller — a curl from
the shell, a script, a debugging session — now sees the correct
response.
"""

import json

from pgruntime import krita_api


# ---- shared client bootstrap, re-exported via local wrappers ----

def _call(cmd_type, **kwargs):
    return krita_api.call(cmd_type,
                          log_prefix="text_boxes_krita", **kwargs)


def open_file(path, add_view=True):
    return krita_api.open_file(path, add_view,
                               log_prefix="text_boxes_krita")


def activate_document(name):
    return krita_api.activate_document(name,
                                       log_prefix="text_boxes_krita")


def pick_files():
    return krita_api.pick_files(
        title="Add Krita documents to the text boxes",
        filetypes=[
            ("Krita documents", "*.kra *.krz"),
            ("All files",       "*.*"),
        ],
        log_prefix="text_boxes_krita",
    )


# ---- text_boxes-specific functionality -------------------------

def dump_text_shapes():
    """Pull every text shape across every open Krita document.

    Returns ``{success: True, shapes: [...]}`` on success or
    ``{success: False, message: ...}`` on failure.  See the module
    docstring, BUGS FIXED BY THIS PORT, for the failure-test
    convention.
    """
    try:
        data, err = _call("dump_all_text_shapes")
    except Exception as e:
        return {"success": False, "message": str(e)}
    if err is not None:
        return {"success": False, "message": err or "unknown"}
    return {"success": True, "shapes": (data or {}).get("shapes", [])}


def patch_text_shape(document_path, layer_name, text_index, patch):
    """Route a patch to one text shape via the daemon.

    `patch_layer_text` operates on the active document, so the
    target is activated first.  Activation failure is non-fatal: if
    the wrong document stays active, the patch call will fail its
    own layer lookup and report the real error.

    Returns ``{success: True, **<daemon payload>}`` on success or
    ``{success: False, message: ...}`` on failure.  The
    ``(data or {})`` spread handles `patch_layer_text` returning
    `data: null` on success without raising ``**None``.
    """
    try:
        # Pre-patch activation: a low-level call is sufficient here.
        # The higher-level wrapper exists for callers that care about
        # the normalised `{success, message}` response; this site
        # only cares about the side effect, and a failure is ignored
        # on purpose — the patch will report the real error (layer
        # not found) if activation did not take.
        if document_path:
            try:
                _call("activate_document", name=document_path)
            except Exception:
                pass

        data, err = _call(
            "patch_layer_text",
            layer_name = layer_name,
            text_index = int(text_index),
            patch      = json.dumps(patch),
        )
    except Exception as e:
        return {"success": False, "message": str(e)}
    if err is not None:
        return {"success": False, "message": err or "unknown"}
    return {"success": True, **(data or {})}