"""Playground registry with per-slot layer selectors and control params.

Playgrounds call `playground_register` on startup and
`playground_unregister` on clean exit.  The daemon keeps an in-memory
map keyed by playground id, and hands it to `comfy_status` so the
docker can render a "Playground Input" section.

Each registered playground declares one or more **layer slots** on
register.  A slot is an `@`-prefixed identifier plus optional hints:

    layer_slots = [
        {
            "id":                "@thelayout",
            "auto_select_layer": "thelayout",  # optional
        },
        {
            "id":                    "@thecontext",
            "managed_by_playground": True,     # optional
        },
    ]

The slot's `id` is what identifies the slot in the docker and web
panel; there is no separate display label.  A slot with an id of
`@thelayout` appears as `@thelayout` — resource-name style, same as
`@thecanvas` for resources.  This is the whole point: the id *is* the
name.

Each slot carries its own `{"source": str, "layer_name": str|null}`
selector.  The docker edits it via `playground_set_layer` (which
requires `slot_id` to target the right slot); the playground itself
reads it (via the daemon's `/active_state` HTTP route) and re-fetches
the selected layer whenever it changes.  The selector never touches
the workflow graph, `_active_config`, or resource substitution.

Multi-slot playgrounds are supported by construction: each slot gets
its own selector, its own docker row, and its own `slot_id` in
`playground_set_layer`.  Slots are addressed independently; changing
one does not affect the others.

MANAGED-BY-PLAYGROUND SLOTS
===========================
A slot may set `managed_by_playground: True` to declare that its
selector is driven entirely by the playground itself, not by the
user.  Such slots are still claimed from the Resources section (the
panel handler filters them out because the daemon is the sole source
of truth for the resource's value — see
`handlers/panel.py::_claimed_resource_names`), but they do not get a
row in the docker's Playgrounds section: there is no user-facing
state to edit.  Inpaint uses this for `@thecontext` and
`@theselection` — the playground derives both internally and exposes
no layer picker for either.  `@thecanvas` on the same playground is
not marked, so it does get a row.

The field defaults to False.  A re-register overwrites it from the
new call's slot spec — unlike `selector`, it is not user state, so
preserving it across restarts would be wrong; the playground is the
authority on which of its slots are managed.

PARAM SLOTS
===========
A playground may also declare one or more **parameter slots** on
register.  These are the workflow-graph Parameters that the
playground itself writes, mirroring the way layer slots claim
`@`-resources.  A param slot is just a string — the parameter title:

    param_slots = [
        "OUTPAINT_LEFT_PAD",
        "OUTPAINT_RIGHT_PAD",
        "OUTPAINT_TOP_PAD",
        "OUTPAINT_BOTTOM_PAD",
    ]

Claimed parameters disappear from the docker's Parameters section
(same rule as `@`-resources disappearing from Resources — see
`handlers/panel.py::_claimed_param_names`), and if every workflow
parameter is claimed the Parameters section is suppressed entirely.
The playground is the sole source of truth for the claimed values.

At generation time the daemon queries each playground's `/params`
route for current values, which are substituted into the workflow
graph just before submission.  That's the "pushed at generation
time" step: the playground writes the values on its end (from
whatever client-side state it tracks), and the daemon pulls them at
generation time.  A playground that has no `param_slots` declares
contributes nothing to this pass.

CONTROL PARAMS
==============
A playground may additionally declare one or more **control params**
on register.  Control params are *not* substituted into the workflow
graph — they are pipeline-routing hints that the daemon's own code
reads from `config["params"]`.  Unlike `param_slots` (whose values
are pulled fresh from the playground's HTTP `/params` at generation
time), control-param values live in the registry and are edited by
the docker.

A control param is a dict:

    control_params = [
        {
            "name":    "DESTINATION",
            "type":    "select",
            "options": ["krita", "playground"],
            "default": "playground",
        },
    ]

`type` is one of `"select"`, `"toggle"`, `"text"`, `"number"`,
`"integer"`.  Select requires `options`; toggle requires `default`
to be a bool.

Control params are **singletons**: at most one playground may claim a
given `name`.  Attempting to register a second claimant returns
`{"success": False, "message": "control param ... already claimed by ..."}`.
This is enforced at `playground_register` time, under the registry
lock, so two playgrounds registering concurrently cannot both win.

The outpaint playground uses `DESTINATION` to route its generation
between "publish the raw outpaint result to the playground" (default)
and "paste into Krita" (bounds check still applies).  See
`comfyui_generation.py`'s routing branch for the reader side.

No heartbeats.  Clean exits unregister explicitly (signal handler +
atexit).  Crashes and SIGKILL are caught by a background sweep thread
that runs every `_SWEEP_INTERVAL_S` seconds and probes each entry's
`/version` endpoint.

SWEEP: WHAT "DEAD" MEANS
========================
The sweep probes each registered entry's `/version` and removes the
entry only if the process appears to have gone away.  The
distinction between "gone away" and "route wrong" matters:

  · **Process gone** — connection refused, timeout, bad URL.  The
    sweep removes the entry.

  · **Process answered with a 4xx/5xx** — the process is alive; only
    its `/version` handler is missing or broken.  The sweep keeps
    the entry.  A playground that predates the pgkit built-in
    `/version` route (see `pgkit/server.py`'s Built-in routes
    section) would answer a probe with a 404, but should not be
    swept — it's still serving its own HTTP routes and driving its
    own slots.

Daemon restart: the registry is in-memory and lives on the handler
singleton, so it vanishes when the daemon process exits.  Playgrounds
in the wild exit when the daemon dies (each playground's
daemon-watch thread) and the user restarts both manually.  A
playground that stays up (inpaint, whose daemon-watch is limited to
shutting down the whole process) re-registers on its next autosync
poll via its own `/playground_reregister` route — see
`playgrounds/inpaint/pg_autosync.py`'s `_reregisterIfMissing`.
"""

from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.request

from ..decorators import command, AutoDispatchHandler


_SWEEP_INTERVAL_S = 300.0
_SWEEP_PROBE_TIMEOUT_S = 1.0

_LAYER_SOURCES = frozenset({"active", "global", "layer"})

_CONTROL_PARAM_TYPES = frozenset({
    "select", "toggle", "text", "number", "integer",
})


def _probe_alive(url: str) -> bool:
    """POST `url + "/version"`.  True iff the process answered.

    Any HTTP response — including a 404 from a playground that has
    not registered its own `/version` — means the process is up.  Only
    transport-level failures (connection refused, timeout, bad URL,
    DNS) mean the process is dead.

    See the module docstring, SWEEP: WHAT "DEAD" MEANS.

    `HTTPError` is checked before `Exception` because it is a subclass
    of `URLError` (which is itself an `OSError`) — the specific
    exception must win, or the "any HTTP response means alive" rule
    would never fire.
    """
    try:
        req = urllib.request.Request(
            url + "/version",
            data=b"{}",
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(
            req, timeout=_SWEEP_PROBE_TIMEOUT_S,
        ) as r:
            r.read()
        return True
    except urllib.error.HTTPError:
        # 4xx/5xx — the process answered; only the route is missing
        # or wrong.  Not a liveness failure.
        return True
    except Exception:
        return False


def _normalize_slot_specs(raw_specs) -> tuple:
    """Validate and normalize a list of slot specs from a register call.

    Accepts either a Python list (direct socket call) or a JSON string
    (CLI).  Returns `(slots_dict, error)`, where `slots_dict` maps slot
    id -> normalized slot record on success, and `error` is a message
    on failure.

    Each slot is normalized to
    `{id, auto_select_layer, managed_by_playground, selector}`.  There
    is no display-label field: the id IS the display name.  See the
    module docstring, MANAGED-BY-PLAYGROUND SLOTS, for what
    `managed_by_playground` means.
    """
    if isinstance(raw_specs, str):
        try:
            raw_specs = json.loads(raw_specs)
        except json.JSONDecodeError as e:
            return None, f"layer_slots JSON error: {e}"
    if raw_specs is None:
        raw_specs = []
    if not isinstance(raw_specs, list):
        return None, "layer_slots must be a list of slot specs"

    out: dict = {}
    for i, spec in enumerate(raw_specs):
        if not isinstance(spec, dict):
            return None, f"layer_slots[{i}] must be an object"
        sid = spec.get("id")
        if not isinstance(sid, str) or not sid.strip():
            return None, f"layer_slots[{i}].id must be a non-empty string"
        sid = sid.strip()
        if not sid.startswith("@"):
            return None, (
                f"layer_slots[{i}].id must start with '@', got {sid!r}"
            )
        if sid in out:
            return None, f"duplicate slot id {sid!r}"
        auto = spec.get("auto_select_layer")
        auto = str(auto) if auto is not None else None
        out[sid] = {
            "id": sid,
            "auto_select_layer": auto,
            "managed_by_playground": bool(
                spec.get("managed_by_playground", False)
            ),
            "selector": {"source": "active", "layer_name": None},
        }
    return out, None


def _normalize_param_slot_specs(raw_specs) -> tuple:
    """Validate and normalize a list of param slot names.

    A param slot is simply a workflow-parameter title (a plain string
    — no `@` prefix, no selector, no per-slot metadata).  Accepts a
    Python list (direct socket call) or JSON string (CLI).  Returns
    `(params_dict, error)`, where `params_dict` maps name -> `{name}`
    on success.  See the module docstring, PARAM SLOTS.
    """
    if isinstance(raw_specs, str):
        try:
            raw_specs = json.loads(raw_specs)
        except json.JSONDecodeError as e:
            return None, f"param_slots JSON error: {e}"
    if raw_specs is None:
        raw_specs = []
    if not isinstance(raw_specs, list):
        return None, "param_slots must be a list of param names"

    out: dict = {}
    for i, name in enumerate(raw_specs):
        if not isinstance(name, str) or not name.strip():
            return None, (
                f"param_slots[{i}] must be a non-empty string"
            )
        name = name.strip()
        if name in out:
            return None, f"duplicate param name {name!r}"
        out[name] = {"name": name}
    return out, None


def _normalize_control_param_specs(raw_specs) -> tuple:
    """Validate and normalize a list of control-param specs.

    Accepts a Python list (direct socket call) or a JSON string
    (CLI).  Returns `(control_dict, error)`, where `control_dict` maps
    name -> `{name, type, options, default}`.  See the module
    docstring, CONTROL PARAMS.

    Values are NOT included here — the caller initializes `value` from
    `default` (or preserves an existing value on re-register).
    """
    if isinstance(raw_specs, str):
        try:
            raw_specs = json.loads(raw_specs)
        except json.JSONDecodeError as e:
            return None, f"control_params JSON error: {e}"
    if raw_specs is None:
        raw_specs = []
    if not isinstance(raw_specs, list):
        return None, "control_params must be a list of spec objects"

    out: dict = {}
    for i, spec in enumerate(raw_specs):
        if not isinstance(spec, dict):
            return None, f"control_params[{i}] must be an object"
        name = spec.get("name")
        if not isinstance(name, str) or not name.strip():
            return None, (
                f"control_params[{i}].name must be a non-empty string"
            )
        name = name.strip()
        if name in out:
            return None, f"duplicate control param name {name!r}"

        ctype = spec.get("type", "text")
        if not isinstance(ctype, str) or ctype not in _CONTROL_PARAM_TYPES:
            return None, (
                f"control_params[{i}].type={ctype!r} is not "
                f"supported; expected one of "
                f"{sorted(_CONTROL_PARAM_TYPES)}"
            )

        options = spec.get("options")
        default = spec.get("default")

        if ctype == "select":
            if not isinstance(options, list) or not options:
                return None, (
                    f"control_params[{i}].options must be a "
                    f"non-empty list for type='select'"
                )
            if default not in options:
                return None, (
                    f"control_params[{i}].default {default!r} is not "
                    f"in options {options!r}"
                )
            options = list(options)
        elif ctype == "toggle":
            if not isinstance(default, bool):
                return None, (
                    f"control_params[{i}].default must be a boolean "
                    f"for type='toggle'"
                )
            options = None
        else:
            options = None

        out[name] = {
            "name": name,
            "type": ctype,
            "options": options,
            "default": default,
        }

    return out, None


class PlaygroundCommands(AutoDispatchHandler):

    def __init__(self, parent):
        self.parent = parent
        self._registry: dict = {}
        self._lock = threading.Lock()

        super().__init__()

        self._stop_event = threading.Event()
        self._sweep_thread = threading.Thread(
            target=self._sweep_loop,
            name="playground-sweep",
            daemon=True,
        )
        self._sweep_thread.start()

    # ------------------------------------------------------------------
    #  Sweep
    # ------------------------------------------------------------------

    def _sweep_loop(self):
        while not self._stop_event.wait(_SWEEP_INTERVAL_S):
            self._sweep_once()

    def _sweep_once(self):
        with self._lock:
            snapshot = list(self._registry.items())
        dead = []
        for pid, entry in snapshot:
            url = (entry.get("url") or "").rstrip("/")
            if not url:
                dead.append(pid)
                continue
            if not _probe_alive(url):
                dead.append(pid)
        if dead:
            with self._lock:
                for pid in dead:
                    self._registry.pop(pid, None)
            print(
                f"[playgrounds] swept {len(dead)} dead entry/entries: "
                f"{dead}",
                flush=True,
            )

    # ------------------------------------------------------------------
    #  Public read
    # ------------------------------------------------------------------

    def list(self) -> list:
        """Snapshot for `comfy_status`.  Copy, not a reference.

        Every slot's `managed_by_playground` is emitted alongside
        `id`, `auto_select_layer`, and `selector`.  The docker's
        `_update_playgrounds` reads it to decide whether to render a
        row for the slot; the panel handler's
        `_claimed_resource_names` reads only `id`, so a slot that is
        managed still gets filtered out of Resources — the flag only
        affects the Playgrounds section.

        `param_slots` is a flat list of names — the panel handler's
        `_claimed_param_names` reads it to hide the corresponding
        fields in Parameters.  See the module docstring, PARAM SLOTS.

        `control_params` is a list of dicts, each carrying the full
        spec plus the current `value` (registry-owned state).  The
        docker's `_update_playgrounds` renders a row per control
        param and writes changes back via
        `playground_set_control_param`.  See the module docstring,
        CONTROL PARAMS.
        """
        with self._lock:
            out = []
            for e in self._registry.values():
                slots = []
                for slot in e["layer_slots"].values():
                    slots.append({
                        "id":                    slot["id"],
                        "auto_select_layer":     slot["auto_select_layer"],
                        "managed_by_playground": slot["managed_by_playground"],
                        "selector":              dict(slot["selector"]),
                    })
                ctrl = []
                for cp in e["control_params"].values():
                    ctrl.append({
                        "name":    cp["name"],
                        "type":    cp["type"],
                        "options": (list(cp["options"])
                                    if cp["options"] else None),
                        "default": cp["default"],
                        "value":   cp["value"],
                    })
                out.append({
                    "id":              e["id"],
                    "name":            e["name"],
                    "url":             e["url"],
                    "port":            e["port"],
                    "layer_slots":     slots,
                    "param_slots":     [
                        p["name"] for p in e["param_slots"].values()
                    ],
                    "control_params":  ctrl,
                    "registered_at":   e["registered_at"],
                })
            return out

    # ------------------------------------------------------------------
    #  Commands
    # ------------------------------------------------------------------

    @command(
        category="playgrounds",
        help_text="Register a running playground with the daemon.",
        args={
            "--id":             {"type": "str",  "required": True},
            "--name":           {"type": "str",  "required": True},
            "--url":            {"type": "str",  "required": True},
            "--port":           {"type": "int",  "required": True},
            "--layer_slots":    {"type": "json", "default": "[]"},
            "--param_slots":    {"type": "json", "default": "[]"},
            "--control_params": {"type": "json", "default": "[]"},
        },
        arg_map={"id": "playground_id"},
    )
    def playground_register(self, playground_id, name, url, port,
                            layer_slots=None, param_slots=None,
                            control_params=None):
        pid = str(playground_id or "").strip()
        name = str(name or "").strip()
        url = str(url or "").strip().rstrip("/")

        if not pid or not name or not url:
            return {"success": False,
                    "message": "id, name, url are required"}

        try:
            port = int(port)
        except (TypeError, ValueError):
            return {"success": False,
                    "message": f"port must be an integer, got {port!r}"}
        if port <= 0 or port > 65535:
            return {"success": False,
                    "message": f"port out of range: {port}"}

        slot_dict, err = _normalize_slot_specs(layer_slots)
        if err is not None:
            return {"success": False, "message": err}

        param_dict, err = _normalize_param_slot_specs(param_slots)
        if err is not None:
            return {"success": False, "message": err}

        control_dict, err = _normalize_control_param_specs(control_params)
        if err is not None:
            return {"success": False, "message": err}

        with self._lock:
            # Singleton enforcement: control-param names must be
            # claimed by at most one playground.  Checked under the
            # same lock that guards the write, so two concurrent
            # registers cannot both win.  Skipped for the calling
            # playground's own entry (re-register is allowed).
            for other_pid, other_entry in self._registry.items():
                if other_pid == pid:
                    continue
                other_ctrl = other_entry.get("control_params") or {}
                for cname in control_dict:
                    if cname in other_ctrl:
                        return {
                            "success": False,
                            "message": (
                                f"control param {cname!r} already "
                                f"claimed by playground {other_pid!r}; "
                                f"control params are singletons"
                            ),
                        }

            existing = self._registry.get(pid)

            # Preserve any existing selectors for slots that survive
            # the re-registration (same slot id).  New slots get the
            # default `{"source": "active", "layer_name": None}`;
            # removed slots are dropped.
            #
            # `managed_by_playground` is NOT preserved from the
            # existing record — it comes from the new registration
            # call's slot spec, always.
            #
            # `param_slots` is likewise not preserved — it is a fresh
            # claim from each register call, exactly like
            # `managed_by_playground`.
            new_slots: dict = {}
            for sid, new_spec in slot_dict.items():
                if existing is not None and sid in existing["layer_slots"]:
                    new_spec["selector"] = dict(
                        existing["layer_slots"][sid]["selector"]
                    )
                new_slots[sid] = new_spec

            # Control params: preserve the current `value` when the
            # new spec still admits it (same name, value in new
            # options for select, else default).  A re-register that
            # drops a control param drops its value with it.
            new_control: dict = {}
            for cname, new_spec in control_dict.items():
                cur_val = None
                if (existing is not None
                        and cname in (existing.get("control_params") or {})):
                    cur_val = existing["control_params"][cname].get("value")
                opts = new_spec.get("options") or []
                if cur_val is not None and (not opts or cur_val in opts):
                    new_spec["value"] = cur_val
                else:
                    new_spec["value"] = new_spec["default"]
                new_control[cname] = new_spec

            if existing is not None:
                print(
                    f"[playgrounds] {pid!r} re-registered "
                    f"(was {existing['url']!r}, now {url!r}; "
                    f"{len(new_slots)} slot(s), "
                    f"{len(param_dict)} param(s), "
                    f"{len(new_control)} control param(s))",
                    flush=True,
                )
            self._registry[pid] = {
                "id":              pid,
                "name":            name,
                "url":             url,
                "port":            port,
                "layer_slots":     new_slots,
                "param_slots":     param_dict,
                "control_params":  new_control,
                "registered_at":   time.time(),
            }

        return {
            "success": True,
            "message": f"registered {pid!r} at {url}",
            "data": {
                "id":       pid,
                "url":      url,
                "slots":    list(new_slots.keys()),
                "params":   list(param_dict.keys()),
                "controls": list(new_control.keys()),
            },
        }

    @command(
        category="playgrounds",
        help_text="Unregister a playground.",
        args={"--id": {"type": "str", "required": True}},
        arg_map={"id": "playground_id"},
    )
    def playground_unregister(self, playground_id):
        pid = str(playground_id or "").strip()
        if not pid:
            return {"success": False, "message": "id is required"}
        with self._lock:
            existed = self._registry.pop(pid, None)
        if existed is not None:
            print(f"[playgrounds] unregistered {pid!r}", flush=True)
        return {
            "success": True,
            "message": (f"unregistered {pid!r}" if existed
                        else f"{pid!r} was not registered"),
            "data": {"id": pid, "existed": existed is not None},
        }

    @command(
        category="playgrounds",
        help_text="Set a layer-slot's selector value.",
        args={
            "--id":         {"type": "str", "required": True},
            "--slot_id":    {"type": "str", "required": True},
            "--source":     {"type": "str", "required": True},
            "--layer_name": {"type": "str", "default": ""},
        },
        arg_map={"id": "playground_id"},
    )
    def playground_set_layer(self, playground_id, slot_id, source,
                             layer_name=""):
        pid = str(playground_id or "").strip()
        sid = str(slot_id or "").strip()
        source = str(source or "").strip()
        layer_name = str(layer_name or "").strip()

        if not pid:
            return {"success": False, "message": "id is required"}
        if not sid:
            return {"success": False, "message": "slot_id is required"}
        if source not in _LAYER_SOURCES:
            return {"success": False,
                    "message": f"source must be one of "
                               f"{sorted(_LAYER_SOURCES)}, got {source!r}"}
        if source == "layer" and not layer_name:
            return {"success": False,
                    "message": "layer_name is required when source='layer'"}
        if source != "layer":
            layer_name = ""

        with self._lock:
            entry = self._registry.get(pid)
            if entry is None:
                return {"success": False,
                        "message": f"playground {pid!r} is not registered"}
            slot = entry["layer_slots"].get(sid)
            if slot is None:
                return {"success": False,
                        "message": (
                            f"playground {pid!r} has no slot "
                            f"{sid!r}; slots: "
                            f"{sorted(entry['layer_slots'])}"
                        )}
            slot["selector"] = {
                "source": source,
                "layer_name": (layer_name if source == "layer" else None),
            }
            new_selector = dict(slot["selector"])

        print(
            f"[playgrounds] {pid!r}{sid} → "
            f"{new_selector['source']}"
            f"{(':' + new_selector['layer_name']) if new_selector['layer_name'] else ''}",
            flush=True,
        )
        return {
            "success": True,
            "message": "selector updated",
            "data": {
                "id":       pid,
                "slot_id":  sid,
                "selector": new_selector,
            },
        }

    @command(
        category="playgrounds",
        help_text="Set a control param's value (daemon-owned).",
        args={
            "--id":    {"type": "str", "required": True},
            "--name":  {"type": "str", "required": True},
            "--value": {"type": "str", "required": True},
        },
        arg_map={"id": "playground_id"},
    )
    def playground_set_control_param(self, playground_id, name, value):
        """Update a registered control param's current value.

        Control params are daemon-owned: the registry is the source
        of truth, and `comfyui_generation._fetch_playground_params`
        merges `value` into the pipeline's `config["params"]` at
        generation time.  The playground's own HTTP `/params` route
        does not carry control params — only `param_slots` values
        flow through HTTP.

        Coercion follows `type`: select requires `value` to be one of
        `options`; toggle parses to bool; number/integer parse
        numerically; text passes through as a string.
        """
        pid = str(playground_id or "").strip()
        cname = str(name or "").strip()
        raw_value = value

        if not pid:
            return {"success": False, "message": "id is required"}
        if not cname:
            return {"success": False, "message": "name is required"}

        with self._lock:
            entry = self._registry.get(pid)
            if entry is None:
                return {"success": False,
                        "message": f"playground {pid!r} is not registered"}
            cp = (entry.get("control_params") or {}).get(cname)
            if cp is None:
                return {"success": False,
                        "message": (
                            f"playground {pid!r} has no control param "
                            f"{cname!r}; claimed: "
                            f"{sorted((entry.get('control_params') or {}).keys())}"
                        )}

            ctype = cp.get("type", "text")
            if ctype == "select":
                if raw_value not in (cp.get("options") or []):
                    return {"success": False,
                            "message": (
                                f"value {raw_value!r} not in options "
                                f"{cp.get('options')!r}"
                            )}
                new_value = raw_value
            elif ctype == "toggle":
                if isinstance(raw_value, bool):
                    new_value = raw_value
                else:
                    new_value = str(raw_value).lower() in (
                        "true", "1", "yes", "on",
                    )
            elif ctype == "number":
                try:
                    new_value = float(raw_value)
                except (TypeError, ValueError):
                    return {"success": False,
                            "message": (
                                f"value {raw_value!r} is not a number"
                            )}
            elif ctype == "integer":
                try:
                    new_value = int(str(raw_value), 10)
                except (TypeError, ValueError):
                    return {"success": False,
                            "message": (
                                f"value {raw_value!r} is not an integer"
                            )}
            else:
                new_value = "" if raw_value is None else str(raw_value)

            cp["value"] = new_value

        print(
            f"[playgrounds] {pid!r}.{cname} → {new_value!r}",
            flush=True,
        )
        try:
            self.parent._publish_state_change()
        except Exception:
            pass
        return {
            "success": True,
            "message": f"{cname} = {new_value!r}",
            "data": {"id": pid, "name": cname, "value": new_value},
        }