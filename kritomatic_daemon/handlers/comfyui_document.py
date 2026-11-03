"""Stable helpers for reading document identity and lifecycle.

The daemon is called from a socket, so the active document can
disappear or be replaced between the moment a batch is prepared and
the moment its results are pasted. These helpers resolve the live
document from a (fileName, name) key captured at prepare time, and
validate that its color model is one the paste pipeline can handle.

Pure functions; no state.
"""

from __future__ import annotations

from typing import Optional

from krita import Krita


def _public_run_record(r: dict) -> dict:
    return {k: v for k, v in r.items() if not k.startswith("_")}


def _get_live_active_document():
    doc = Krita.instance().activeDocument()
    if doc is not None:
        try:
            _ = doc.width()
            return doc
        except Exception:
            pass
    try:
        docs = list(Krita.instance().documents())
    except Exception:
        return None
    for d in docs:
        try:
            _ = d.width()
            return d
        except Exception:
            continue
    return None


def _capture_document_key(doc) -> tuple:
    try:
        return (doc.fileName() or "", doc.name() or "")
    except Exception:
        return ("", "")


def _find_document_by_key(key: tuple):
    if not key or not any(key):
        return None
    try:
        docs = list(Krita.instance().documents())
    except Exception:
        return None
    fn, name = key

    candidates = []
    for d in docs:
        try:
            if fn and d.fileName() == fn:
                candidates.append(d)
        except Exception:
            continue
    if not candidates and not fn:
        for d in docs:
            try:
                if name and d.name() == name:
                    candidates.append(d)
            except Exception:
                continue

    for d in candidates:
        try:
            _ = d.width()
            return d
        except Exception:
            continue
    return None


def _resolve_live_document_for_prepared(prepared_entry: dict):
    key = prepared_entry.get("document_key") or ("", "")
    fresh = _find_document_by_key(key)
    if fresh is not None:
        return fresh
    doc = prepared_entry.get("document")
    if doc is not None:
        try:
            _ = doc.width()
            return doc
        except Exception:
            pass
    return None


def _resolve_live_document_for_pending(pending):
    key = getattr(pending, "document_key", ("", ""))
    fresh = _find_document_by_key(key)
    if fresh is not None:
        return fresh
    doc = getattr(pending, "document", None)
    if doc is not None:
        try:
            _ = doc.width()
            return doc
        except Exception:
            pass
    return None


def _check_color_mode(doc) -> tuple:
    model = doc.colorModel()
    depth = doc.colorDepth()
    if model != "RGBA":
        return False, (
            f"Document color model is {model!r}; only RGBA is supported. "
            f"Convert via Image > Convert Image Color Space."
        )
    if depth != "U8":
        return False, (
            f"Document color depth is {depth!r}; only 8-bit integer is "
            f"supported. Convert via Image > Convert Image Color Space."
        )
    return True, None


def _document_is_open(doc) -> bool:
    try:
        for d in Krita.instance().documents():
            if d == doc:
                return True
    except Exception:
        return False
    return False


def _png_dimensions(png_bytes: bytes) -> tuple:
    if len(png_bytes) < 24 or png_bytes[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("Not a valid PNG")
    import struct
    w, h = struct.unpack(">II", png_bytes[16:24])
    return int(w), int(h)