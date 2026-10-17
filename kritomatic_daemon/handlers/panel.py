"""
Panel description handler.

Owns the panel's *content*: the machine-readable description that every
frontend (the Krita docker, the browser panel, the Emacs client)
renders. The description is a list of sections; each section is a
metadata strip, a form, or a set of action buttons. Each editable field
carries the daemon command that sets it.

The description is composed from providers. A provider is a small
object with one method, `contribute(builder, ctx)`. Adding a new panel
feature that is not comfyui-related (document stats, batch history,
palette management) means writing a provider and appending it to the
list below. Every frontend picks it up on the next description push.

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

Between these two paths, `panel_description` always returns something
current. Bus events keep the pushed descriptions fresh; the puller
covers the cold-start case.
"""

from __future__ import annotations

import hashlib
import json
import threading
from typing import Callable, Optional

from ..decorators import command, AutoDispatchHandler


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


_STRUCTURE_FIELD_KEYS = (
    "key", "type", "options", "visible_when",
    "set_command", "arg_key", "kind_hint",
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

class WorkflowMetadataProvider:
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
        if status.get("workflow_signature"):
            fields.append({
                "label": "Signature",
                "value": status["workflow_signature"],
                "emphasis": "dim",
            })
        if status.get("workflow_exclusive"):
            fields.append({
                "label": "File lock",
                "value": "on",
                "emphasis": "dim",
            })
        if status.get("pinned_publisher"):
            fields.append({
                "label": "Pinned publisher",
                "value": status["pinned_publisher"],
                "emphasis": "dim",
            })
        builder.add_section({
            "id": "workflow",
            "kind": "metadata",
            "title": None,
            "fields": fields,
        })


class CanvasProvider:
    def contribute(self, builder, ctx):
        status = ctx.get("comfy_status") or {}
        canvas_info = status.get("workflow_canvas_info") or {}
        if not canvas_info.get("has_canvas"):
            return
        builder.add_section({
            "id": "canvas",
            "kind": "form",
            "title": "Canvas",
            "commit": {
                "type": "comfy_bind_canvas",
                "arg_map": {
                    "canvas-source": "source",
                    "canvas-layer": "layer_name",
                },
            },
            "fields": [
                {
                    "key": "canvas-source",
                    "label": "Source",
                    "type": "radio",
                    "options": ["active", "layer"],
                    "value": status.get("canvas_source") or "active",
                },
                {
                    "key": "canvas-layer",
                    "label": "Layer name",
                    "type": "text",
                    "value": status.get("canvas_layer") or "",
                    "visible_when": {"canvas-source": "layer"},
                },
            ],
        })


class SelectionProvider:
    def contribute(self, builder, ctx):
        status = ctx.get("comfy_status") or {}
        canvas_info = status.get("workflow_canvas_info") or {}
        if not canvas_info.get("has_selection"):
            return
        builder.add_section({
            "id": "selection",
            "kind": "form",
            "title": "Selection",
            "commit": {
                "type": "comfy_bind_selection",
                "arg_map": {
                    "selection-source": "source",
                    "selection-layer": "layer_name",
                    "selection-context": "context",
                    "selection-padding": "padding",
                },
            },
            "fields": [
                {
                    "key": "selection-source",
                    "label": "Source",
                    "type": "select",
                    "options": ["global", "none", "layer"],
                    "value": status.get("selection_source") or "global",
                },
                {
                    "key": "selection-layer",
                    "label": "Layer name",
                    "type": "text",
                    "value": status.get("selection_layer") or "",
                    "visible_when": {"selection-source": "layer"},
                },
                {
                    "key": "selection-context",
                    "label": "Context",
                    "type": "select",
                    "options": ["entire_image", "automatic", "mask_bounds"],
                    "value": status.get("selection_context") or "entire_image",
                },
                {
                    "key": "selection-padding",
                    "label": "Padding",
                    "type": "integer",
                    "value": int(status.get("selection_padding") or 0),
                    "min": 0,
                },
            ],
        })


class LayerAliasesProvider:
    def contribute(self, builder, ctx):
        status = ctx.get("comfy_status") or {}
        reqs = status.get("workflow_layer_requirements") or {}
        mask_layers = list(reqs.get("mask_layers") or [])
        image_layers = list(reqs.get("image_layers") or [])
        if not mask_layers and not image_layers:
            return
        aliases = status.get("layer_aliases") or {}
        fields = []
        for name in mask_layers:
            fields.append({
                "key": f"alias:{name}",
                "label": name,
                "type": "text",
                "value": aliases.get(name, name),
                "kind_hint": "mask",
                "set_command": {
                    "type": "comfy_bind_layer_alias",
                    "logical_name": name,
                },
                "arg_key": "actual_name",
            })
        for name in image_layers:
            fields.append({
                "key": f"alias:{name}",
                "label": name,
                "type": "text",
                "value": aliases.get(name, name),
                "kind_hint": "image",
                "set_command": {
                    "type": "comfy_bind_layer_alias",
                    "logical_name": name,
                },
                "arg_key": "actual_name",
            })
        builder.add_section({
            "id": "layer-aliases",
            "kind": "form",
            "title": "Layer aliases",
            "fields": fields,
        })


class ParametersProvider:
    def contribute(self, builder, ctx):
        status = ctx.get("comfy_status") or {}
        params_def = status.get("workflow_parameters") or []
        if not params_def:
            return
        current = status.get("params") or {}
        fields = []
        for p in params_def:
            name = p.get("name")
            if not name:
                continue
            ptype = p.get("type", "auto")
            default = p.get("default", "")

            # `overridden` means "the current value differs from the
            # workflow's declared default". The simpler test that was
            # used before — `name in current` — is not the same thing:
            # comfy_set_param stores every value the user has ever set
            # in `_params`, and while comfy_load clears `_params`, a
            # browser publish does not. So a value carried over from a
            # previous workflow can appear in `_params` and be marked
            # overridden even though it equals this workflow's default.
            #
            # Comparison is by string so that cross-type matches count.
            # comfy_set_param stores raw strings for non-toggle types,
            # so `current[name]` for a number param is the string
            # "0.7", while the default is the number 0.7; str() on both
            # sides makes them compare equal. Toggles arrive as bools
            # on both sides (comfy_set_param coerces "true"/"false" to
            # True/False), so their string forms also match.
            raw_current = current.get(name, default)
            overridden = (
                name in current
                and str(raw_current) != str(default)
            )
            value = raw_current

            field_type = _param_field_type(ptype)
            if field_type == "toggle":
                value = _coerce_bool(value)

            fields.append({
                "key": f"param:{name}",
                "label": name,
                "type": field_type,
                "value": value,
                "default": default,
                "overridden": overridden,
                "set_command": {
                    "type": "comfy_set_param",
                    "name": name,
                },
                "arg_key": "value",
            })
        builder.add_section({
            "id": "params",
            "kind": "form",
            "title": "Parameters",
            "fields": fields,
        })


def _param_field_type(ptype: str) -> str:
    if ptype == "toggle":
        return "toggle"
    if ptype == "number":
        return "number"
    if ptype == "number (integer)":
        return "integer"
    if ptype in ("prompt (positive)", "prompt (negative)"):
        return "textarea"
    return "text"


def _coerce_bool(value) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in ("true", "1", "yes", "on")
    return bool(value)


class RunOptionsProvider:
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
        output_name = opts.get("output_name", "")
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
                {
                    "key": "run-output-name",
                    "label": "Output name",
                    "type": "text",
                    "value": str(output_name) if output_name else "",
                    "set_command": {
                        "type": "panel_set_run_options",
                        "name": "output_name",
                    },
                    "arg_key": "value",
                },
            ],
        })


class RunActionsProvider:
    def contribute(self, builder, ctx):
        builder.add_section({
            "id": "run-actions",
            "kind": "actions",
            "title": None,
            "items": [
                {
                    "key": "run",
                    "label": "Run",
                    "style": "primary",
                    "command": {"type": "comfy_generate"},
                    "collect": {
                        "run-timeout": "timeout",
                        "run-count": "count",
                        "run-seed": "seed",
                        "run-output-name": "output_name",
                    },
                },
                {
                    "key": "cancel",
                    "label": "Cancel",
                    "style": "danger",
                    "command": {"type": "comfy_cancel"},
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
            WorkflowMetadataProvider(),
            CanvasProvider(),
            SelectionProvider(),
            LayerAliasesProvider(),
            ParametersProvider(),
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
        """Ask the injected puller for the current status, or None.

        Returns the data dict on success, None on failure or absence.
        Failure is not an error: without a puller, the handler falls
        back to whatever the bus has delivered.
        """
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
