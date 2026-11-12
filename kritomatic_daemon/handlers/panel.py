"""
Panel description handler.

Owns the panel's *content*: the machine-readable description that every
frontend (the Krita docker, the browser panel, the Emacs client)
renders. The description is a list of sections; each section is a
metadata strip, a form, a workflow-bar, or a set of action buttons.

Two config models
=================
  * **Command fields** — carry a `set_command`; the docker sends the
    command on edit. Used for state the daemon owns (run options,
    workflow picker).

  * **Config fields** — carry a `config_path` into the v2 config dict
    (resources/params/crop/paste). The docker watches these for edits
    and pushes a full `comfy_apply_config` on a 150 ms debounce.

Workflow bar
============
The `workflow-bar` section kind renders the workflow picker and the
Load / Save-config buttons on one row. Kept as its own section kind
rather than a form+actions pair so the renderer can layout them
together (picker stretches, buttons hug the right edge).

Autoload on comfy_load
======================
`comfy_load` autoloads `panel_configs/<slug>.json` if it exists. The
providers here read `_active_config` to populate field values, so
autoloaded values appear in the panel with no extra work on this side.

Slot extraction failure
=======================
When `comfy_load` could not extract slots (duplicate @-name or
`_meta.title`), the panel has no fields to render, but it is not
broken — the graph is loaded.  `WorkflowMetadataProvider` renders a
red "Slot error" line so the user sees *why* the form is empty
instead of a silently blank panel.

Composite (row) fields
======================
A field with `type: "row"` is a composite: one row containing N
sub-widgets laid out horizontally. Each sub-widget carries its own
`key`, `config_path`, and (optionally) `visible_when` / `stretch` /
`kind_hint`. Both renderers register the sub-widgets as ordinary
leaf fields keyed by their own key; the composite key itself is only
the row's label.

Used by ResourcesProvider so each resource renders as
    @thecanvas    [ global/active/layer ▼ ]   [ thecanvas________ ]
on one line, with the layer-name input shown only when the dropdown
is "layer".

Layer alias kind hints
======================
Each resource slot has a `kind` from `comfyui_inspect`'s resource
extractor: `"image"` for `LoadImage`, `"mask"` for `LoadImageMask`.
`ResourcesProvider` propagates that as `kind_hint` on the layer-name
sub-field, which tells the docker to render an editable
`_LayerComboBox` populated with layers of that kind (image-bearing
vs transparency/selection masks) and ordered by the recent-alias
list. Fields without `kind_hint` continue to render as plain
`QLineEdit`s.
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


_STRUCTURE_FIELD_KEYS = (
    "key", "type", "options", "visible_when",
    "set_command", "arg_key", "kind_hint",
    "config_path", "no_sync",
)

# Sub-fields contribute to the structure hash only by their *shape*
# (key, type, options, config_path, visibility rule, stretch,
# kind_hint) — never by their current value. Otherwise a value change
# would produce a new structure_id and defeat the sync-in-place
# optimization in both renderers.
#
# `kind_hint` is *shape*, not value: whether a text field is rendered
# as a plain QLineEdit or as a _LayerComboBox is decided at rebuild
# time from this hint, and switching a field between the two at
# runtime requires the tree to be rebuilt (a plain QLineEdit cannot
# become an editable combo in place). So a `kind_hint` change must
# change structure_id.
_STRUCTURE_SUBFIELD_KEYS = (
    "key", "type", "options", "visible_when",
    "config_path", "kind_hint", "stretch",
)


def _field_structure(f: dict) -> dict:
    out = {k: f.get(k) for k in _STRUCTURE_FIELD_KEYS}
    subs = f.get("sub_fields")
    if isinstance(subs, list):
        out["sub_fields"] = [
            {k: sf.get(k) for k in _STRUCTURE_SUBFIELD_KEYS}
            for sf in subs if isinstance(sf, dict)
        ]
    return out


def _finalize_description(sections: list) -> dict:
    structure_src = []
    for s in sections:
        structure_src.append({
            "id": s.get("id"),
            "kind": s.get("kind"),
            "title": s.get("title"),
            "commit": s.get("commit"),
            "fields": [
                _field_structure(f)
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
    """The workflow picker + Load + Save-config buttons, on one row.

    Replaces the previous form section that only carried the picker.
    Load and Save-config moved here from `run-actions` because they
    operate on the current workflow's config file, not on execution
    state.
    """

    def contribute(self, builder, ctx):
        status = ctx.get("comfy_status") or {}
        current = status.get("workflow") or ""

        try:
            slugs = sorted(p.stem for p in _WORKFLOW_DIR.glob("*.json"))
        except OSError:
            slugs = []

        if not slugs:
            picker_field = {
                "key": "workflow-name",
                "label": "Name",
                "type": "text",
                "value": "",
                "set_command": {"type": "comfy_load"},
                "arg_key": "name",
            }
        else:
            selected = current if current in slugs else slugs[0]
            picker_field = {
                "key": "workflow-name",
                "label": "Name",
                "type": "select",
                "options": slugs,
                "value": selected,
                "set_command": {"type": "comfy_load"},
                "arg_key": "name",
            }

        builder.add_section({
            "id": "workflow-picker",
            "kind": "workflow-bar",
            "title": "Workflow",
            "fields": [picker_field],
            "items": [
                {
                    "key": "load",
                    "label": "Load",
                    "command": {"type": "comfy_load"},
                    "collect": {"workflow-name": "name"},
                },
                {
                    "key": "save_config",
                    "label": "Save config",
                    "command": {"type": "comfy_save_config"},
                },
            ],
        })


class WorkflowMetadataProvider:
    """Workflow identity, source, output-node count, slot error,
    active-batch count. Rendered only when a workflow is loaded."""

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
        if status.get("workflow_slot_error"):
            fields.append({
                "label": "Slot error",
                "value": status["workflow_slot_error"],
                "emphasis": "danger",
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
    """Per-@-name: a single composite row with the source dropdown and
    a conditional layer_name field.

    The composite's `key` is only the row's label. Its sub-widgets
    carry the real `config_path`s and are wired independently — the
    docker auto-applies on every edit to either sub-widget.

    The layer_name sub-field carries two hints that matter to the
    docker:

      * `kind_hint` — copied from the workflow slot's `kind`. Value is
        "image" (LoadImage) or "mask" (LoadImageMask). The docker
        uses this to decide whether the field renders as a plain
        `QLineEdit` or as an editable `_LayerComboBox` populated
        with layers of the matching kind.

      * `visible_when` — points at the sibling `source` sub-widget,
        so the field collapses when the dropdown is `active` or
        `global` and reappears on `layer`. Both renderers register
        sub-widgets as ordinary leaf fields keyed by their own key,
        so all per-field plumbing (read_widget_value, apply_value,
        sync_values, build_config_from_widgets, focus and in-flight
        tracking, set_layers) works unchanged.
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
            slot = slots.get(name) or {}
            # The extractor records `kind` as "image" or "mask"
            # depending on whether the node was LoadImage or
            # LoadImageMask. That's exactly what the docker's
            # _LayerComboBox needs to pick the right bucket. Default
            # to "image" if the slot dict is somehow missing the
            # field — a plain text input is the safe fallback.
            slot_kind = slot.get("kind") or "image"
            res_cfg = cfg_resources.get(name) or {}
            current_source = res_cfg.get("source") or "layer"
            current_layer = (
                res_cfg.get("layer_name") or name.lstrip("@")
            )
            fields.append({
                "key": f"cfg-resource.{name}",
                "label": name,
                "type": "row",
                "sub_fields": [
                    {
                        "key": f"cfg-resource.{name}.source",
                        "type": "select",
                        "options": ["active", "global", "layer"],
                        "value": current_source,
                        "config_path": ["resources", name, "source"],
                        "stretch": 0,
                    },
                    {
                        "key": f"cfg-resource.{name}.layer_name",
                        "type": "text",
                        "value": current_layer,
                        "config_path": ["resources", name, "layer_name"],
                        "visible_when": {
                            f"cfg-resource.{name}.source": "layer",
                        },
                        "kind_hint": slot_kind,
                        "stretch": 1,
                    },
                ],
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
    """Context and padding."""

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
    """Where and how to paste SaveImage outputs."""

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
    """Timeout, count, seed. Auto-commit via `panel_set_run_options`."""

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
    """Run / Cancel / Cancel-All.

    Load moved to `WorkflowPickerProvider`'s `workflow-bar` section
    (it operates on the config file, not on execution state).
    """

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

    def set_comfy_status_puller(self, puller: Callable[[], dict]) -> None:
        self._comfy_status_puller = puller

    def _pull_current_comfy_status(self) -> Optional[dict]:
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

    def _build_description_from_state(self) -> dict:
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

    @command(
        category='panel',
        help_text='Return the current panel description.',
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
        help_text='Set a panel run option.',
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
        help_text='Rebuild and republish the panel description.',
        args={},
    )
    def panel_rebuild(self, params):
        self._republish()
        return {'success': True, 'message': 'panel description republished'}