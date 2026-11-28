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
    pgruntime.krita_api         higher-level `KritaClient` and
                                `BatchExecutor` wrappers for the
                                playgrounds that use
                                `src/kritomatic/`'s clients rather
                                than the raw TCP `daemon_client`.
                                Provides `call`, `open_file`,
                                `activate_document`, `pick_files`;
                                re-exports `KritaClient` and
                                `BatchExecutor`.

    pgruntime.kra_export        shared scaffolding for the two
                                playgrounds (cone, text_wall) that
                                export a `.kra` via the daemon's
                                `BatchExecutor`.  Provides the
                                shared constants, `coerce_transform`,
                                `normalize_band`, `common_header`,
                                `prepare_output_path`, and
                                `execute_bundle`.
    pgruntime.bootstrap         serve_playground — the launcher
                                pattern every <name>_playground.py
                                was implementing locally.  Handles
                                port-verify, daemon-watch, atexit,
                                signals, and registration flags.
    pgruntime.html              render_or_exit — the
                                JsSyntaxError-to-clean-exit wrapper
                                every <name>_html.py was duplicating.
    pgruntime.js_constants      CONSTANTS_JS — shared JS numeric
                                constants (view zoom bounds, sync
                                debounce, op log cap, pixel bbox).
    pgruntime.js_state_base     STATE_BASE_JS — makeStateBase()
                                factory covering every playground's
                                canvas/view/fit/transform/schedule
                                boilerplate, with `overlay` and
                                `rotation` options.
    pgruntime.js_pan_zoom       PAN_ZOOM_JS — installPanZoom().
    pgruntime.js_chrome         CHROME_JS — makeChromeText(), the
                                cached-chrome-text factory.
    pgruntime.js_panel_fields   PANEL_FIELDS_JS — syncImageInfo().
    pgruntime.js_state_sync     STATE_SYNC_JS — makeStateSync(),
                                debounced deduped state push with
                                success-only commit.
    pgruntime.js_autosync       AUTOSYNC_JS — makeAutosync(), the
                                layer-slot poller.
    pgruntime.hub_control       JS string shared by remote_panel and
                                workbench: `createHubControl(opts)`
                                (the hub-management WebSocket + API),
                                plus `openInWorkbench` / `openInNewTab`
                                / `openPlayground` / `launchAndOpen`

    pgruntime.hub_manage_ui     JS string shared by remote_panel and
                                workbench: the "Manage playgrounds"
                                modal (DOM, CSS, and rendering logic).
                                Consumes a `hubCtrl` from
                                `pgruntime.hub_control`.
"""

__version__ = "0.1.0"
