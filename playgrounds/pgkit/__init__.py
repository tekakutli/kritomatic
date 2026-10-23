"""
pgkit — shared toolkit for the playgrounds.

Layered so each project can adopt what it needs:

    pgkit.styles            shared CSS
    pgkit.panel_core        flashStatus and the scrub-input installer
    pgkit.shell             installShellControls as an ES module
    pgkit.html_shell        page assembly (head, panel, canvas, tail)
    pgkit.modules           pgkit_modules() — the module_sources dict
                            for the kit's own JS
    pgkit.js_check          per-module syntax pre-flight
    pgkit.lint              static check for ES-module import/export
                            consistency across a playground
    pgkit.remote_client     browser-side WebSocket client (ES module)
    pgkit.remote_cli_client Python client for the same protocol;
                            the transport under remote_cli
    pgkit.ws                minimal WebSocket framing (Python)
    pgkit.remote_hub        server-side client registry and relay
    pgkit.server            HTTP + WebSocket server, and /modules/
                            serving

A playground wires these together with its own model, views, and
scene format.  See the module docstrings for the contracts.

ES MODULES
==========
Playground JS is served as ES modules.  Each project module is a
`(label, js_source)` pair inside a `_MODULE_SOURCES` dict in the
project's `*_html.py`.  The dict is passed to both `page()` (for
syntax checking) and `kit_serve()` (for serving at `/modules/<label>`).
A single boot module (also in the dict) does all the imports and the
boot calls.

Splitting each panel file into its own module is what makes
cross-file references loud instead of silent: a missing export is a
load error naming both files, not a ReferenceError hidden 100
kilobytes into a bundle.

PYTHON CLIENTS
==============
`pgkit.remote_cli_client` is the Python-side counterpart to
`pgkit.remote_client`.  It speaks the same wire protocol and is what
`remote_cli/` is built on.  Any Python script can import it to drive a
running playground — see the module docstring for the full contract.
"""

__version__ = "0.2.0"
PROTOCOL_VERSION = 1
