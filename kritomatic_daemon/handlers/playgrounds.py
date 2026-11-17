"""Playground registry with per-slot layer selectors.

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

No heartbeats.  Clean exits unregister explicitly (signal handler +
atexit).  Crashes and SIGKILL are caught by a background sweep thread
that runs every `_SWEEP_INTERVAL_S` seconds and probes each entry's
`/version` endpoint.

Daemon restart: the registry is in-memory and lives on the handler
singleton, so it vanishes when the daemon process exits.  Playgrounds
in the wild exit when the daemon dies (each playground's
daemon-watch thread) and the user restarts both manually.
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
            except Exception:
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
                out.append({
                    "id":            e["id"],
                    "name":          e["name"],
                    "url":           e["url"],
                    "port":          e["port"],
                    "layer_slots":   slots,
                    "registered_at": e["registered_at"],
                })
            return out

    # ------------------------------------------------------------------
    #  Commands
    # ------------------------------------------------------------------

    @command(
        category="playgrounds",
        help_text="Register a running playground with the daemon.",
        args={
            "--id":          {"type": "str",  "required": True},
            "--name":        {"type": "str",  "required": True},
            "--url":         {"type": "str",  "required": True},
            "--port":        {"type": "int",  "required": True},
            "--layer_slots": {"type": "json", "default": "[]"},
        },
        arg_map={"id": "playground_id"},
    )
    def playground_register(self, playground_id, name, url, port,
                            layer_slots=None):
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

        with self._lock:
            existing = self._registry.get(pid)
            # Preserve any existing selectors for slots that survive
            # the re-registration (same slot id).  New slots get the
            # default `{"source": "active", "layer_name": None}`;
            # removed slots are dropped.
            #
            # `managed_by_playground` is NOT preserved from the
            # existing record — it comes from the new registration
            # call's slot spec, always.  It is a claim about how the
            # playground drives the slot, not user state; the
            # playground is the authority on it and gets to change
            # its mind on re-register.
            new_slots: dict = {}
            for sid, new_spec in slot_dict.items():
                if existing is not None and sid in existing["layer_slots"]:
                    new_spec["selector"] = dict(
                        existing["layer_slots"][sid]["selector"]
                    )
                new_slots[sid] = new_spec

            if existing is not None:
                print(
                    f"[playgrounds] {pid!r} re-registered "
                    f"(was {existing['url']!r}, now {url!r}; "
                    f"{len(new_slots)} slot(s))",
                    flush=True,
                )
            self._registry[pid] = {
                "id":            pid,
                "name":          name,
                "url":           url,
                "port":          port,
                "layer_slots":   new_slots,
                "registered_at": time.time(),
            }

        return {
            "success": True,
            "message": f"registered {pid!r} at {url}",
            "data": {
                "id":    pid,
                "url":   url,
                "slots": list(new_slots.keys()),
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
