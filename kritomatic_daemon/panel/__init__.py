"""
Docker panel package.

The docker is a Qt widget tree that renders a *panel description* pushed
by the daemon (see handlers/panel.py). It knows nothing about comfyui;
it knows field types and command names.

This package is on reload_daemon's keep-list. Live QDockWidget instances
are bound to the classes here, so the modules must not be dropped.
"""

from __future__ import annotations


def _dock_position_right():
    """Return the DockRight position constant, tolerating both Krita 5
    and Krita 6.

    Krita 5 (PyQt5) exposes the enum as a bare class attribute on
    `DockWidgetFactoryBase`; Krita 6 (PyQt6) namespaces it under
    `DockPosition`. The behaviour is identical, only the access path
    changed.
    """
    from krita import DockWidgetFactoryBase
    try:
        return DockWidgetFactoryBase.DockPosition.DockRight
    except AttributeError:
        pass
    try:
        return DockWidgetFactoryBase.DockRight
    except AttributeError:
        pass
    raise AttributeError(
        "could not find DockRight on DockWidgetFactoryBase; "
        "this Krita build exposes the dock position enum differently"
    )


def register_dock_widget() -> None:
    """Register the panel docker factory with Krita.

    Must run exactly once per Krita session. Called from
    kritomatic_daemon/__init__.py, which is imported once and never
    reloaded.
    """
    try:
        from krita import Krita, DockWidgetFactory
    except ImportError:
        return

    from .docker import KritomaticPanel

    factory = DockWidgetFactory(
        "KritomaticPanel",
        _dock_position_right(),
        KritomaticPanel,
    )
    Krita.instance().addDockWidgetFactory(factory)
    print("✓ Kritomatic panel docker factory registered")
