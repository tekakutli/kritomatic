"""
pgkit — shared toolkit for the playgrounds.

Layered so each project can adopt what it needs:

    pgkit.styles            shared CSS
    pgkit.panel_core        flashStatus and the scrub-input installer
    pgkit.html_shell        page assembly (head, panel, canvas, tail)
    pgkit.math              common JS math (color, affine, homography)
    pgkit.schema            schema runtime (JS): field descriptors
    pgkit.render            schema → panel DOM and bindings (JS)
    pgkit.remote_client     browser-side WebSocket client (JS)
    pgkit.ws                minimal WebSocket framing (Python)
    pgkit.remote_hub        server-side client registry and relay
    pgkit.server            HTTP + WebSocket server

A playground wires these together with its own model, views, and
scene format.  See the module docstrings for the contracts.
"""

__version__ = "0.1.0"
PROTOCOL_VERSION = 1
