"""
pgruntime — shared runtime for the playgrounds.

Layered so each playground can adopt what it needs, and deliberately
sitting *between* pgkit and the playgrounds:

    playgrounds/<name>/     domain logic.  Masks, prompts, embeddings,
                            viewers, workflows, edge-cleanup,
                            ComfyUI graph shapes.  Knows everything
                            about its own domain; knows nothing about
                            how other playgrounds work.

    pgruntime               playground runtime & daemon glue.  Canvas
                            + view idiom, autosync, pan/zoom mixin,
                            chrome-text cache, Krita daemon client,
                            registration, shared daemon routes, shared
                            daemon JS bridge.  Knows what a playground
                            *is*; knows nothing about masks, prompts,
                            workflows, or embeddings.

    pgkit                   transport & shell.  HTTP server, WS hub,
                            module serving, log mirroring, HTML shell,
                            JS syntax pre-flight.  Knows nothing about
                            playgrounds, Krita, or ComfyUI.

    kritomatic_shared       ComfyUI client, graph conversion, tooling.
                            Shared between the daemon and the
                            playgrounds, on a separate axis.

Each layer imports only from layers below it.  pgkit stays lean by
contract: this package must not import from pgkit, and pgkit must not
gain domain knowledge to accommodate pgruntime.  If a change to pgkit
seems necessary to make a pgruntime abstraction work, the abstraction
belongs one layer up.

PACKAGE CONTENTS
================
    pgruntime.daemon_client      TCP client to the kritomatic daemon
                                 (`is_reachable`, `send`, `send_batch`)
    pgruntime.daemon_registry    register / unregister with the daemon
                                 (playground registration + reregister)
    pgruntime.daemon_routes      shared /krita/* proxy route handlers,
                                 parameterized by the send function so
                                 pinned-document callers can inject
    pgruntime.js_daemon_client   DAEMON_CLIENT_JS: the browser-side
                                 HTTP bridge to the shared routes,
                                 exported as a `makeKritaClient` factory
    pgruntime.modules            pgruntime_modules() — the
                                 module_sources entries for pgruntime's
                                 own JS.  Blend with pgkit_modules() and
                                 a playground's own sources:

                                     from pgkit.modules import pgkit_modules
                                     from pgruntime.modules import pgruntime_modules

                                     _MODULE_SOURCES = {
                                         **pgkit_modules(),
                                         **pgruntime_modules(),
                                         "pg_state.js": STATE_JS,
                                         # ...
                                     }
    pgruntime.krita_api         higher-level `KritaClient` wrapper for
                                the two playgrounds (board, text_boxes)
                                that use `src/kritomatic/client.py`'s
                                client rather than the raw TCP
                                `daemon_client` the other seven use.
                                Provides `call`, `open_file`,
                                `activate_document`, `pick_files`,
                                and re-exports `KritaClient`.
"""

__version__ = "0.1.0"
