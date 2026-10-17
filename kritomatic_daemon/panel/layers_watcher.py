"""
layers_watcher.py — debounced, event-driven refresh trigger for the
                    docker's layer-alias dropdown.

Why this exists, and what Krita cannot do
=========================================
The docker's alias fields offer a dropdown of layer names, filtered by
kind (`image` vs `mask`). Keeping that list current requires knowing
when the layer tree changes.

Krita 6's Python API does not expose a signal that fires on layer
changes. A probe of the installed build shows:

    Notifier has: imageCreated, imageClosed, imageSaved,
                  applicationClosing, viewClosed, viewCreated,
                  windowCreated, windowIsBeingCreated,
                  configurationChanged.
    Document has: no modified / nodeAdded / nodeRemoved /
                  activeNodeChanged signal.
    Node has:     no sigChildAdded / sigChildRemoved / sigNameChanged /
                  sigVisibleChanged / sigOpacityChanged signal.

A layer add or remove performed in Krita's native layer panel is
therefore invisible to the daemon and the docker until somebody looks.

The alternative to a signal is polling. Polling is rejected by design:
it wakes the process on a schedule regardless of whether anything
happened, and the delay between a layer change and the list update is
bounded only by the poll interval. This module instead uses a set of
discrete, event-driven triggers, each of which fires on a specific
meaningful action:

  1. Notifier.imageCreated / imageClosed — a document appeared or
     disappeared, taking its layers with it.

  2. Panel events from the daemon — any state-mutating daemon command
     republishes the panel description; daemon-issued layer changes
     (notably the paste at the end of a comfy_generate run) come
     through here. Wired by the docker, not by this module.

  3. Docker focusInEvent — the user has engaged with the docker;
     refresh before they can click into an alias field. Wired by the
     docker.

  4. Layer combo showPopup — the user is about to open the dropdown;
     refresh immediately so the list is current at the moment they
     look. Wired by the docker via PanelRenderer.layerRefreshRequested.

  5. Event socket connected — the daemon just became reachable; catch
     anything missed during startup. Wired by the docker.

All five call `schedule()`. A burst (the daemon pasting five layers,
or the user tabbing quickly across several fields) collapses to one
fire because the timer is restarted on every call.

The one gap
===========
No trigger fires for a native-Krita layer change performed while no
other event happens. The specific case: the user has an alias field
focused, presses a Krita keyboard shortcut that adds a layer without
changing focus, then keeps typing. The list stays stale until the
next trigger — which is almost always the next popup open or the next
focus in, i.e. the next moment the user could actually consult it.
This is the accepted cost of not polling.

Debounce interval
=================
150 ms. Long enough to collapse any batch burst the daemon can produce
(the fastest state-mutating commands complete in tens of ms each, so a
`comfy_generate` paste of N layers is one burst), short enough to feel
immediate after a single manual layer add.
"""

from __future__ import annotations

from PyQt6.QtCore import QObject, QTimer, pyqtSignal


DEBOUNCE_MS = 150


class LayersWatcher(QObject):
    """Debounced trigger. Emits `layersChanged` at most once per
    DEBOUNCE_MS, on any of the events listed in the module docstring.
    The consumer (KritomaticPanel) wires `layersChanged` to
    `DaemonConnection.request_layers`, which does the actual fetch on
    a fresh socket.
    """

    layersChanged = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(DEBOUNCE_MS)
        self._timer.timeout.connect(self._fire)

        self._notifier = None
        self._attach_notifier()

    # -- notifier wiring --

    def _attach_notifier(self) -> None:
        try:
            from krita import Krita
            self._notifier = Krita.instance().notifier()
        except Exception:
            self._notifier = None
            return
        if self._notifier is None:
            return
        try:
            self._notifier.imageCreated.connect(self._on_document_opened)
        except Exception:
            pass
        try:
            self._notifier.imageClosed.connect(self._on_document_closed)
        except Exception:
            pass

    def _on_document_opened(self, _doc) -> None:
        self.schedule()

    def _on_document_closed(self, _doc) -> None:
        self.schedule()

    # -- debounced fire --

    def schedule(self) -> None:
        """(Re)start the debounce timer. Every trigger calls this."""
        self._timer.start()

    def _fire(self) -> None:
        self.layersChanged.emit()

    # -- lifecycle --

    def stop(self) -> None:
        """Stop the timer and disconnect from Notifier. Idempotent."""
        try:
            self._timer.stop()
        except Exception:
            pass
        if self._notifier is not None:
            try:
                self._notifier.imageCreated.disconnect(
                    self._on_document_opened)
            except Exception:
                pass
            try:
                self._notifier.imageClosed.disconnect(
                    self._on_document_closed)
            except Exception:
                pass
            self._notifier = None
