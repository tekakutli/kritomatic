"""
Refresh coalescing for batch execution.

Inside a batch, the daemon processes commands one after another, and
each one calls refresh(doc) — normally a doc.refreshProjection(), which
composites the layer stack.  A batch that creates N shapes calls it
5-6N times; without coalescing, the Nth shape pays for a composite of
5-6N layers, and each earlier composite is thrown away a moment later
when the next command runs.

This module defers refreshProjection during batch execution.  Handlers
call refresh(doc) instead of doc.refreshProjection().  Inside a batch
the call is recorded; at end_defer() the pending set is either flushed
or dropped, depending on FLUSH_ON_END.

A NOTE ON WHAT refreshProjection ACTUALLY DOES
==============================================
It is not "schedule a repaint".  Krita's own repaint machinery runs
continuously and incrementally as nodes are created and modified: each
change marks the document dirty and the main-thread event loop composes
only the affected regions a few dozen times a second.  That is the
scene the user watches build up during a batch, and it is unaffected by
anything in this module.

refreshProjection() is different.  It is a synchronous, blocking,
whole-stack composite performed on the calling thread — every layer
composited, every transform mask applied, every filter run, end to
end.  On a document that contains perspective transform masks it also
pays Krita's one-time perspective-pipeline initialisation, which
dominates the cost the first time it runs in a Krita session.

That synchronous call is what froze the GUI at the end of a batch:
the incremental repaints had already drawn the correct scene, and
the flush then blocked the UI recomputing it.

FLUSH_ON_END
============
False (the default, and the correct choice for interactive use)

    The flush is skipped.  The batch ends, batch_complete goes out,
    the client returns, and Krita keeps repainting through its normal
    incremental machinery.  Nothing in the interactive experience is
    lost: the canvas is current because Krita's own repaint queue is
    doing the work continuously, not because we forced a synchronous
    composite.

    The only thing given up is the *guarantee*: at the instant
    execute() returns, a handful of incremental repaints may still be
    queued.  In practice they drain in milliseconds.  For a human
    driving the batch through the CLI, this is not observable.

True

    Flush every pending document with a synchronous refreshProjection()
    at the end of the batch.  This is the setting for code that reads
    the composite immediately after the batch returns — a script that
    samples pixels, exports an image, or otherwise inspects the
    rendered result rather than the node tree.

    Do NOT enable this for interactive use.  It reintroduces the
    blocking composite that the deferred mode exists to avoid: the
    CLI will hang at the end of the batch while the whole stack is
    recomposited on the calling thread, and Krita's GUI will be frozen
    for the duration.  A human watching the canvas has already seen
    the correct scene via the incremental repaints; paying for a
    second, synchronous composite on top of that buys nothing they can
    perceive.

    The right way to use True is per-batch, from code that needs it:
    a script that will inspect the composite calls the daemon with
    this flag set for that one run, rather than flipping the module
    default and affecting every batch thereafter.

WHAT A HANDLER SHOULD DO
========================
Handlers call refresh(doc), never doc.refreshProjection() directly.
That way a batch can be flushed or not as a whole, and a handler that
genuinely needs a fresh composite mid-batch can call
doc.refreshProjection() itself — the deferral only intercepts the
refresh() helper, not direct calls.

Thread-local so concurrent batches on different sockets do not
interfere.  The daemon does not serialize batch execution, so
concurrent batches are already undefined for state reasons; this
module at least keeps their refresh bookkeeping separate.
"""

import threading

from krita import Krita


# See the module docstring, FLUSH_ON_END.  Leave False for interactive
# use: the GUI stays responsive throughout and after the batch, and
# Krita's own incremental repaints keep the canvas current.  Set True
# only from a code path that will read the composite immediately after
# the batch — the cost is a synchronous, GUI-freezing composite on
# exit, which is exactly what the deferred mode exists to avoid.
FLUSH_ON_END = False


_state = threading.local()


def _local():
    if not hasattr(_state, 'deferring'):
        _state.deferring = False
        _state.pending = {}
    return _state


def refresh(doc=None):
    """Refresh a document's projection, or record it for the end of
    the current batch if one is in progress.

    Handlers should call this instead of doc.refreshProjection().
    """
    if doc is None:
        doc = Krita.instance().activeDocument()
    if doc is None:
        return
    s = _local()
    if s.deferring:
        s.pending[id(doc)] = doc
    else:
        try:
            doc.refreshProjection()
        except Exception:
            pass


def begin_defer():
    """Enter batch execution: subsequent refresh() calls are queued."""
    s = _local()
    s.deferring = True
    s.pending.clear()


def end_defer():
    """Leave batch execution.

    With FLUSH_ON_END = False (the interactive default), the pending
    set is dropped without compositing: Krita's normal incremental
    repaints keep the canvas current, and the batch does not block on
    a synchronous whole-stack composite.  With FLUSH_ON_END = True,
    every pending document is composited once — the correct behaviour
    for a caller that will inspect the composite immediately after
    the batch, at the cost of a GUI freeze on exit.  See the module
    docstring.
    """
    s = _local()
    s.deferring = False
    pending = list(s.pending.values())
    s.pending.clear()

    if not FLUSH_ON_END:
        return

    for doc in pending:
        try:
            doc.refreshProjection()
        except Exception:
            pass
