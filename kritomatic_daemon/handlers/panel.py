"""
Panel description handler.

Owns the panel's *content*: the machine-readable description that every
frontend (the Krita docker, the browser panel, the Emacs client)
renders. The description is a list of sections; each section is a
metadata strip, a form, or a set of action buttons. Each editable field
carries the daemon command that sets it, OR a `config_path` into the
v2 panel config (resources/params/crop/paste) — the docker collects the
config at Run time from its widgets and sends it atomically.

The description is composed from providers. A provider is a small
object with one method, `contribute(builder, ctx)`. Adding a new panel
feature that is not comfyui-related (document stats, batch history,
palette management) means writing a provider and appending it to the
list below. Every frontend picks it up on the next description push.

Two config models
=================
The panel carries two kinds of fields:

  * **Command fields** — carry a `set_command` that the docker sends
    on edit. Used for state that the daemon owns (run options, workflow
    picker). The daemon validates, applies, re-publishes.

  * **Config fields** — carry a `config_path` into the v2 config dict.
    The docker watches these for edits and, when one fires, builds the
    full config from all current widget values and sends it as a
    single `comfy_apply_config`. The daemon validates, stores the
    result in `_active_config`, and re-publishes. Every field edit
    therefore propagates to the daemon within one debounce window
    (~150 ms), and any other client reading `_active_config` — CLI's
    `panel run`, the inpaint playground, a second docker instance —
    sees the same values.

    This is the "set the values somewhere, have them respected
    everywhere" model. Config edits are not entangled with generation:
    editing `Hyper?` in the docker updates the daemon's canonical
    config immediately, and the next generation from ANY client uses
    the new value. The docker's Run button still sends
    `comfy_panel_run` (which re-applies the current widget state before
    generating), but that apply is now a redundant no-op on the happy
    path — auto-apply already pushed the same values — and a useful
    safety net if the user clicked Run within the debounce window
    before the apply had fired.

Config fields do NOT carry `no_sync`. Widgets sync from `_active_config`
on every panel re-publish, with the renderer's focus-check skipping
the field the user is actively editing. This is what makes external
changes (a CLI `comfy_apply_config`, an inpaint from another tab) show
up in the docker's widgets instead of silently drifting. An earlier
design used `no_sync: True` to prevent daemon re-publishes from
stomping local edits, but that design relied on "apply config only at
Run time" — which is exactly the model this change replaces.

Config defaults
===============
A config field's initial value has three sources, in precedence order:

  1. `_active_config` (set by `comfy_apply_config`) — the values last
     applied by any client.
  2. The workflow's own baked-in default:
     * resources: `{source: "layer", layer_name: <name minus @>}` —
       matches the CLI's `.toml` convention for the workspace-layer
       naming pattern.
     * params: the Primitive node's current `value`, recorded by
       `_extract_param_slots` as `default`.
  3. A kind-based fallback (only when the slot has no value at all —
     malformed workflow).

The point of (2) is that loading a workflow and immediately clicking
Run without touching any config field produces the same behaviour as
the CLI's `panel run <slug>`, which reads its values from
`[resources]` and `[params]` in the `.toml`. The previous
implementation defaulted every param to a kind-based value
(0, 0.0, False, "") and every resource to "active". That silently
substituted wrong values into every Primitive and every resource slot
on load, causing the crash observed on the first run of
NEWTekakutli45: `"normal, sketch, +grays"` defaulted to 0, node 240's
`ImpactSwitch(select=0)` (1-based indices) returned None,
`InpaintCropImproved` got None for image and raised.

The workflow picker
===================
The picker is a `<select>` dropdown populated from the on-disk
workflows directory (`data/comfy_panel/workflows/*.json`). Selecting a
value fires `currentTextChanged`, which the renderer commits as
`comfy_load --name <value>`. The daemon loads the graph, populates the
three slot tables, clears `_active_config`, and re-publishes state. The
docker rebuilds the panel from the new slot tables — resources and
params sections appear. Because the docker's `_on_panel` auto-applies
config on structure change (see `panel/docker.py`), the new widget
defaults are pushed to `_active_config` before the user ever sees
them, so the playground and any other reader get the workflow's
baked-in defaults rather than `None`.

The dropdown handles "pick a workflow and load it" as one gesture. No
typing. No separate Load button required for the normal case.

An explicit **Load** button sits in the actions row. It commits
`comfy_load --name <currently-selected-value>` again. This covers the
one case the dropdown cannot: selecting the *same* workflow to reload
it after you've edited the JSON on disk (a dropdown re-select of the
current value emits no signal, so no commit fires).

The daemon can only load **API-format** JSON. Files that are UI format
are rejected by `comfy_load` with a clear message. The CLI's
`panel run` converts UI→API on the fly via `comfy-cli`; the daemon has
no subprocess path to that tool and does not convert. If the workflows
directory contains a UI-format file, the flow is: run
`kritomatic comfyui panel run <slug>` once from the CLI (which
converts to `data/convert/<slug>.json` and applies that), after which
the daemon's `workflow` field points at the converted path and every
subsequent dropdown selection works because the workflows directory
now contains that converted file too (or the user is selecting a slug
that already points at an API-format source).

There is no "Apply config" button separate from Run. `comfy_panel_run`
(the Run action) does apply-and-generate atomically, and auto-apply
also fires on every field edit — so the applied config is always what
the docker's widgets show. The Run button's apply step is a redundant
confirmation of what auto-apply already did.

State source
============
The handler holds the last comfyui status it has seen. Two things
update it:

  * Bus events. `ComfyUIHandler` publishes a `state` event after every
    successful state-mutating command. The handler subscribes on
    `__init__`, so during a session it stays current.

  * An on-demand puller. The `CommandHandler` injects a callable that
    returns the current comfyui status directly. `panel_description`
    calls it before building, so a fresh client asking for the panel
    on a freshly-launched Krita gets a description built from live
    state, not from an empty cache.
"""

from __future__ import annotations

import hashlib
import json
import threading
from typing import Callable, Optional

from ..decorators import command, AutoDispatchHandler
from .comfyui_types import _WORKFLOW_DIR


PANEL_ID = "kritomatic_main"
PANEL_TITLE = "Kritomatic"


# ============================================================================
#  Description builder
# ============================================================================

class PanelDescriptionBuilder:
    def __init__(self):
        self.sections: list = []

    def add_section(self, section: dict) -> None:
        self.sections.append(section)


# Fields whose presence affects the structure hash. `config_path` and
# `no_sync` are included: a field that gains or loses a config path is
# structurally different from one that does not, and the frontend needs
# a rebuild (not an in-place value sync) in that case. No built-in
# provider sets `no_sync` any more — config fields sync from
# `_active_config` on every re-publish, with focus-based skipping
# handled by the renderer. The key stays here so a future provider can
# still mark a field as "don't sync from daemon" without a hash-schema
# change.
_STRUCTURE_FIELD_KEYS = (
    "key", "type", "options", "visible_when",
    "set_command", "arg_key", "kind_hint",
    "config_path", "no_sync",
)


def _finalize_description(sections: list) -> dict:
    structure_src = []
    for s in sections:
        structure_src.append({
            "id": s.get("id"),
            "kind": s.get("kind"),
            "title": s.get("title"),
            "commit": s.get("commit"),
            "fields": [
                {k: f.get(k) for k in _STRUCTURE_FIELD_KEYS}
                for f in (s.get("fields") or [])
            ],
            "items": s.get("items"),
        })
    digest = hashlib.sha1(
        json.dumps(structure_src, sort_keys=True).encode("utf-8")
    ).hexdigest()[:12]
    return {
        "id": PANEL_ID,
        "title": PANEL_TITLE,
        "structure_id": digest,
        "sections": sections,
    }


# ============================================================================
#  Providers
# ============================================================================

class WorkflowPickerProvider:
    """A `<select>` dropdown populated from the on-disk workflows
    directory. Selecting a value fires `comfy_load --name <value>`.

    The available list is read directly off the filesystem by the
    provider (it runs in the daemon and has access to
    `_WORKFLOW_DIR`). The current selection is the daemon's
    `_active_workflow_name` if it appears in the list, otherwise the
    first item. If the directory is empty, the dropdown degrades to a
    single text field so the section still renders (and the docker's
    workflow-loaded flag remains False so Run stays disabled).
    """

    def contribute(self, builder, ctx):
        status = ctx.get("comfy_status") or {}
        current = status.get("workflow") or ""

        try:
            slugs = sorted(p.stem for p in _WORKFLOW_DIR.glob("*.json"))
        except OSError:
            slugs = []

        if not slugs:
            builder.add_section({
                "id": "workflow-picker",
                "kind": "form",
                "title": "Workflow",
                "fields": [
                    {
                        "key": "workflow-name",
                        "label": "Name",
                        "type": "text",
                        "value": "",
                        "set_command": {"type": "comfy_load"},
                        "arg_key": "name",
                    },
                ],
            })
            return

        selected = current if current in slugs else slugs[0]

        builder.add_section({
            "id": "workflow-picker",
            "kind": "form",
            "title": "Workflow",
            "fields": [
                {
                    "key": "workflow-name",
                    "label": "Name",
                    "type": "select",
                    "options": slugs,
                    "value": selected,
                    "set_command": {"type": "comfy_load"},
                    "arg_key": "name",
                },
            ],
        })


class WorkflowMetadataProvider:
    """Workflow identity, source, output-node count, active-batch
    count. Rendered only when a workflow is loaded."""

    def contribute(self, builder, ctx):
        status = ctx.get("comfy_status") or {}
        if not status.get("workflow"):
            return
        fields = [{
            "label": "Workflow",
            "value": status.get("workflow", ""),
            "emphasis": "strong",
        }]
        if status.get("workflow_source"):
            fields.append({
                "label": "Source",
                "value": status["workflow_source"],
                "emphasis": "dim",
            })
        output_nodes = status.get("workflow_output_nodes") or {}
        if output_nodes:
            fields.append({
                "label": "Output nodes",
                "value": str(len(output_nodes)),
                "emphasis": "dim",
            })
        active_batches = status.get("active_batches") or []
        if active_batches:
            fields.append({
                "label": "Active batches",
                "value": str(len(active_batches)),
                "emphasis": "dim",
            })
        builder.add_section({
            "id": "workflow",
            "kind": "metadata",
            "title": None,
            "fields": fields,
        })


class ResourcesProvider:
    """Per-@-name: a source select and a conditional layer_name text
    field. Both carry `config_path`; the docker auto-applies on every
    edit.

    Defaults for a slot with no `_active_config["resources"]` entry:
    `{source: "layer", layer_name: <name minus leading @>}`. This
    matches the "workspace layer named after the @-ref" convention
    used by the CLI's `.toml` files, so a user who loads a workflow
    and immediately clicks Run gets the same behaviour as
    `kritomatic comfyui panel run <slug>` would produce. Changing the
    dropdown to "active" or "global" overrides it.
    """

    def contribute(self, builder, ctx):
        status = ctx.get("comfy_status") or {}
        slots = status.get("workflow_resource_slots") or {}
        if not slots:
            return
        config = status.get("config") or {}
        cfg_resources = config.get("resources") or {}
        fields = []
        for name in sorted(slots.keys()):
            res_cfg = cfg_resources.get(name) or {}
            current_source = res_cfg.get("source") or "layer"
            current_layer = (
                res_cfg.get("layer_name") or name.lstrip("@")
            )
            fields.append({
                "key": f"cfg-resource.{name}.source",
                "label": f"{name} source",
                "type": "select",
                "options": ["active", "global", "layer"],
                "value": current_source,
                "config_path": ["resources", name, "source"],
            })
            fields.append({
                "key": f"cfg-resource.{name}.layer_name",
                "label": f"{name} layer",
                "type": "text",
                "value": current_layer,
                "config_path": ["resources", name, "layer_name"],
                "visible_when": {f"cfg-resource.{name}.source": "layer"},
            })
        builder.add_section({
            "id": "resources",
            "kind": "form",
            "title": "Resources",
            "fields": fields,
        })


def _param_widget_and_default(kind: str):
    if kind == "float":
        return "number", 0.0
    if kind == "int":
        return "integer", 0
    if kind == "boolean":
        return "toggle", False
    return "text", ""


class ParamsProvider:
    """Per-title: a typed input matching the slot kind. The docker
    auto-applies on every edit.

    Default for a slot with no `_active_config["params"]` entry: the
    workflow node's current `value` (recorded by `_extract_param_slots`
    as `default`), falling back to a kind-based default only when the
    node had no `value` input at load time. This is what keeps the
    docker's widgets in sync with the workflow: a user who loads
    `NEWTekakutli45` and clicks Run without touching anything sends the
    same param values the CLI would send from the `.toml`. The old
    behaviour — defaulting to `0` / `0.0` / `False` regardless of the
    workflow — silently substituted wrong values into every Primitive,
    which is what caused the `ImpactSwitch: invalid select index` crash
    (select defaulted to 0, ImpactSwitch uses 1-based indices, returned
    None, InpaintCropImproved got None for image).
    """

    def contribute(self, builder, ctx):
        status = ctx.get("comfy_status") or {}
        slots = status.get("workflow_param_slots") or {}
        if not slots:
            return
        config = status.get("config") or {}
        cfg_params = config.get("params") or {}
        fields = []
        for title in sorted(slots.keys()):
            slot = slots[title]
            kind = slot.get("kind", "string")
            widget_type, kind_default = _param_widget_and_default(kind)
            workflow_default = slot.get("default")
            if workflow_default is None:
                workflow_default = kind_default
            current = cfg_params.get(title, workflow_default)
            fields.append({
                "key": f"cfg-param.{title}",
                "label": title,
                "type": widget_type,
                "value": current,
                "config_path": ["params", title],
            })
        builder.add_section({
            "id": "params",
            "kind": "form",
            "title": "Parameters",
            "fields": fields,
        })


class CropProvider:
    """Context and padding. Only relevant when a resource has
    source='global', but always rendered when any resource slot exists
    so the config shape is complete and `comfy_panel_run`'s crop arg
    is always well-formed."""

    def contribute(self, builder, ctx):
        status = ctx.get("comfy_status") or {}
        slots = status.get("workflow_resource_slots") or {}
        if not slots:
            return
        config = status.get("config") or {}
        crop = config.get("crop") or {}
        builder.add_section({
            "id": "crop",
            "kind": "form",
            "title": "Crop",
            "fields": [
                {
                    "key": "cfg-crop.context",
                    "label": "Context",
                    "type": "select",
                    "options": ["entire_image", "automatic", "mask_bounds"],
                    "value": crop.get("context") or "entire_image",
                    "config_path": ["crop", "context"],
                },
                {
                    "key": "cfg-crop.padding",
                    "label": "Padding",
                    "type": "integer",
                    "value": int(crop.get("padding") or 0),
                    "min": 0,
                    "config_path": ["crop", "padding"],
                },
            ],
        })


class PasteProvider:
    """Where and how to paste SaveImage outputs. Only rendered when
    the workflow has at least one output node."""

    def contribute(self, builder, ctx):
        status = ctx.get("comfy_status") or {}
        output_nodes = status.get("workflow_output_nodes") or {}
        if not output_nodes:
            return
        config = status.get("config") or {}
        paste = config.get("paste") or {}
        builder.add_section({
            "id": "paste",
            "kind": "form",
            "title": "Paste",
            "fields": [
                {
                    "key": "cfg-paste.x",
                    "label": "X",
                    "type": "integer",
                    "value": int(paste.get("x") or 0),
                    "config_path": ["paste", "x"],
                },
                {
                    "key": "cfg-paste.y",
                    "label": "Y",
                    "type": "integer",
                    "value": int(paste.get("y") or 0),
                    "config_path": ["paste", "y"],
                },
                {
                    "key": "cfg-paste.name",
                    "label": "Name",
                    "type": "text",
                    "value": paste.get("name") or "",
                    "config_path": ["paste", "name"],
                },
                {
                    "key": "cfg-paste.batch_mode",
                    "label": "Batch mode",
                    "type": "select",
                    "options": ["default", "layers", "animation"],
                    "value": paste.get("batch_mode") or "default",
                    "config_path": ["paste", "batch_mode"],
                },
                {
                    "key": "cfg-paste.resize_canvas",
                    "label": "Resize canvas",
                    "type": "toggle",
                    "value": bool(paste.get("resize_canvas")),
                    "config_path": ["paste", "resize_canvas"],
                },
            ],
        })


class RunOptionsProvider:
    """Timeout, count, seed. These auto-commit to the panel handler's
    `_run_options` state via `panel_set_run_options`; the docker reads
    them at Run time too and injects them into `comfy_panel_run`'s
    args, so either path is authoritative. The set_command exists to
    persist across panel rebuilds."""

    def contribute(self, builder, ctx):
        opts = (ctx.get("panel_state") or {}).get("run_options") or {}
        try:
            timeout = float(opts.get("timeout", 55.0))
        except (TypeError, ValueError):
            timeout = 55.0
        try:
            count = int(opts.get("count", 1))
        except (TypeError, ValueError):
            count = 1
        seed = opts.get("seed", "")
        builder.add_section({
            "id": "run-options",
            "kind": "form",
            "title": "Run options",
            "fields": [
                {
                    "key": "run-timeout",
                    "label": "Timeout (s)",
                    "type": "number",
                    "value": timeout,
                    "min": 1.0, "max": 3600.0,
                    "set_command": {
                        "type": "panel_set_run_options",
                        "name": "timeout",
                    },
                    "arg_key": "value",
                },
                {
                    "key": "run-count",
                    "label": "Count",
                    "type": "integer",
                    "value": count,
                    "min": 1, "max": 64,
                    "set_command": {
                        "type": "panel_set_run_options",
                        "name": "count",
                    },
                    "arg_key": "value",
                },
                {
                    "key": "run-seed",
                    "label": "Seed (blank = auto)",
                    "type": "text",
                    "value": str(seed) if seed not in (None, "") else "",
                    "set_command": {
                        "type": "panel_set_run_options",
                        "name": "seed",
                    },
                    "arg_key": "value",
                },
            ],
        })


class RunActionsProvider:
    """Load / Run / Cancel / Cancel All.

    Load: re-issues `comfy_load --name <current dropdown value>`.
    Covers the case the dropdown cannot: selecting the same workflow
    again to reload after on-disk edits (no signal fires on re-select
    of the current value).

    Run: sends `comfy_panel_run` with the config the docker assembles
    from its config fields. Because config fields auto-apply on every
    edit, the assembled config matches `_active_config` on the happy
    path; the apply is a redundant confirmation. It becomes useful if
    the user clicks Run within the auto-apply debounce window, where
    the widget state may be a hair ahead of `_active_config`.

    Cancel and Cancel All bypass the command queue via the docker's
    `send_cancel_now` path.
    """

    def contribute(self, builder, ctx):
        builder.add_section({
            "id": "run-actions",
            "kind": "actions",
            "title": None,
            "items": [
                {
                    "key": "load",
                    "label": "Load",
                    "command": {"type": "comfy_load"},
                    "collect": {"workflow-name": "name"},
                },
                {
                    "key": "run",
                    "label": "Run",
                    "style": "primary",
                    "command": {"type": "comfy_panel_run"},
                    "collect": {
                        "run-timeout": "timeout",
                        "run-count": "count",
                        "run-seed": "seed",
                    },
                },
                {
                    "key": "cancel",
                    "label": "Cancel",
                    "style": "danger",
                    "command": {"type": "comfy_cancel"},
                },
                {
                    "key": "cancel_all",
                    "label": "ALL",
                    "style": "danger",
                    "command": {"type": "comfy_cancel"},
                    "thin": True,
                    "attach_to": "cancel",
                    "cancel_all": True,
                },
            ],
        })


# ============================================================================
#  Panel handler
# ============================================================================

class PanelHandler(AutoDispatchHandler):

    def __init__(self):
        super().__init__()
        self._lock = threading.Lock()
        self._run_options = {
            "timeout": 55.0,
            "count": 1,
            "seed": "",
            "output_name": "",
        }
        self._last_comfy_status: dict = {}
        self._latest_description: dict = _finalize_description([])
        self._comfy_status_puller: Optional[Callable[[], dict]] = None
        self._providers = [
            WorkflowPickerProvider(),
            WorkflowMetadataProvider(),
            ResourcesProvider(),
            ParamsProvider(),
            CropProvider(),
            PasteProvider(),
            RunOptionsProvider(),
            RunActionsProvider(),
        ]
        self._register_listener()

    # -- state wiring --

    def set_comfy_status_puller(self, puller: Callable[[], dict]) -> None:
        """Inject a callable that returns the current comfyui status.

        Called by CommandHandler after constructing the handler tree.
        The puller is used by `panel_description` to guarantee that a
        cold-start client gets a description built from live state, not
        from an empty bus-event cache.
        """
        self._comfy_status_puller = puller

    def _pull_current_comfy_status(self) -> Optional[dict]:
        """Ask the injected puller for the current status, or None."""
        if self._comfy_status_puller is None:
            return None
        try:
            result = self._comfy_status_puller()
        except Exception:
            return None
        if not isinstance(result, dict):
            return None
        if not result.get('success'):
            return None
        data = result.get('data')
        return data if isinstance(data, dict) else None

    def _register_listener(self):
        try:
            from ..server.event_bus import get_event_bus
            get_event_bus().add_listener(self._on_bus_event)
        except Exception:
            pass

    def _on_bus_event(self, event_type, data):
        if event_type != "state":
            return
        try:
            with self._lock:
                self._last_comfy_status = dict(data or {})
            self._republish()
        except Exception:
            pass

    # -- description building --

    def _build_description_from_state(self) -> dict:
        """Build a description from the current `_last_comfy_status`."""
        builder = PanelDescriptionBuilder()
        with self._lock:
            comfy_status = dict(self._last_comfy_status)
            panel_state = {"run_options": dict(self._run_options)}
        ctx = {
            "comfy_status": comfy_status,
            "panel_state": panel_state,
        }
        for provider in self._providers:
            try:
                provider.contribute(builder, ctx)
            except Exception as e:
                builder.add_section({
                    "id": f"provider-error-{len(builder.sections)}",
                    "kind": "metadata",
                    "title": None,
                    "fields": [{
                        "label": "Provider error",
                        "value": f"{type(provider).__name__}: {e}",
                        "emphasis": "danger",
                    }],
                })
        return _finalize_description(builder.sections)

    def _republish(self):
        try:
            description = self._build_description_from_state()
            with self._lock:
                self._latest_description = description
            from ..server.event_bus import get_event_bus
            get_event_bus().publish("panel", description)
        except Exception:
            pass

    # -- commands --

    @command(
        category='panel',
        help_text='Return the current panel description (sections, '
                  'fields, actions). Pulls current comfyui state on '
                  'demand if the bus has not delivered any yet.',
        args={},
    )
    def panel_description(self, params):
        pulled = self._pull_current_comfy_status()
        if pulled is not None:
            with self._lock:
                self._last_comfy_status = dict(pulled)
        description = self._build_description_from_state()
        with self._lock:
            self._latest_description = description
        return {
            'success': True,
            'message': 'panel description',
            'data': description,
        }

    @command(
        category='panel',
        help_text='Report the panel handler state (run options).',
        args={},
    )
    def panel_status(self, params):
        with self._lock:
            return {
                'success': True,
                'message': 'panel handler state',
                'data': {
                    'run_options': dict(self._run_options),
                },
            }

    @command(
        category='panel',
        help_text='Set a panel run option. Valid names: timeout, count, '
                  'seed, output_name.',
        args={
            '--name': {
                'type': 'str', 'required': True,
                'choices': ['timeout', 'count', 'seed', 'output_name'],
                'help': 'Option name',
            },
            '--value': {
                'type': 'str', 'required': True,
                'help': 'New value, as a string',
            },
        },
    )
    def panel_set_run_options(self, params):
        name = params.get('name')
        value = params.get('value')
        if name not in ('timeout', 'count', 'seed', 'output_name'):
            return {'success': False,
                    'message': f'unknown option: {name}'}
        with self._lock:
            if name == 'timeout':
                try:
                    coerced = float(value)
                except (TypeError, ValueError):
                    return {'success': False,
                            'message': f'timeout must be a number, got {value!r}'}
            elif name == 'count':
                try:
                    coerced = int(value)
                except (TypeError, ValueError):
                    return {'success': False,
                            'message': f'count must be an integer, got {value!r}'}
                if coerced < 1:
                    return {'success': False,
                            'message': 'count must be >= 1'}
            else:
                coerced = '' if value is None else str(value)
            self._run_options[name] = coerced
        self._republish()
        return {
            'success': True,
            'message': f'{name} = {coerced!r}',
            'data': {'name': name, 'value': coerced},
        }

    @command(
        category='panel',
        help_text='Rebuild and republish the panel description with a '
                  'fresh structure id.',
        args={},
    )
    def panel_rebuild(self, params):
        self._republish()
        return {'success': True, 'message': 'panel description republished'}
