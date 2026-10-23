"""
rc_script — JSON script execution and validation for remote_cli.

Script format
=============
A script is either a bare array of steps, or an object:

    {
      "name": "capture hair masks",
      "stop_on_error": true,
      "steps": [ ... ]
    }

Step operations
===============
    {"op": "set", "values": {name: value, ...}}
    {"op": "action", "kind": "action.run",
                     "payload": {...}, "timeout": 30.0}
    {"op": "select", "level": "masks", "name": "mask.0"}
    {"op": "deselect"}
    {"op": "sleep", "seconds": 1.0}
    {"op": "wait_value", "name": "...", "equals": ..., "timeout": 30.0}
    {"op": "wait_change", "fields": [...], "timeout": 30.0}
    {"op": "assert", "name": "...", "equals": ...,
                     "message": "..."}
    {"op": "refresh"}
    {"op": "raw", "message": {...}}
    {"op": "bundle", "set": {...}, "actions": [...],
                     "select": [level, name],
                     "wait_for_running": true, "timeout": 60.0}

`wait_value` and `assert` take exactly one predicate key:
    equals / not_equals / greater_than / less_than / contains / matches
"""

from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path

from pgkit.remote_cli_client import (
    RemoteCliError,
    RemoteCommandFailed,
    RemoteTimeout,
    _predicate_matches,
    parse_value,
)


_PRED_KEYS = ("equals", "not_equals", "greater_than",
              "less_than", "contains", "matches")


# ============================================================================
#  Loading
# ============================================================================

def load_script(path_or_data) -> dict:
    if isinstance(path_or_data, dict):
        data = path_or_data
    elif isinstance(path_or_data, list):
        data = {"steps": path_or_data}
    else:
        p = Path(str(path_or_data)).expanduser()
        if not p.is_file():
            raise ValueError(f"script file not found: {p}")
        data = json.loads(p.read_text(encoding="utf-8"))

    if isinstance(data, list):
        data = {"steps": data}
    if not isinstance(data, dict):
        raise ValueError("script must be an object or array")
    if "steps" not in data:
        raise ValueError("script missing 'steps'")
    if not isinstance(data["steps"], list):
        raise ValueError("'steps' must be an array")
    return data


def _extract_predicate(step: dict):
    keys = [k for k in _PRED_KEYS if k in step]
    if len(keys) != 1:
        raise ValueError(
            f"expected exactly one of {_PRED_KEYS}, "
            f"got {keys}"
        )
    k = keys[0]
    return k, step[k]


# ============================================================================
#  Validation
# ============================================================================

def validate(client, script: dict) -> list:
    """Return a list of (level, step_index, message) findings."""
    findings = []
    schema = client.schema() or {}
    entities = client.entities() or {}
    if not schema:
        findings.append(("warning", None,
                         "no schema; cannot validate field references"))
    for i, step in enumerate(script["steps"]):
        _validate_step(i, step, schema, entities, findings)
    return findings


def _validate_step(i, step, schema, entities, findings):
    if not isinstance(step, dict):
        findings.append(("error", i, "step must be an object"))
        return
    op = step.get("op")
    if not op:
        findings.append(("error", i, "missing 'op'"))
        return

    if op == "set":
        _validate_set(i, step, schema, findings, "")
    elif op == "action":
        _validate_action(i, step, schema, findings, "")
    elif op == "select":
        _validate_select(i, step, entities, findings)
    elif op == "deselect":
        pass
    elif op == "sleep":
        if "seconds" not in step:
            findings.append(("error", i, "sleep missing 'seconds'"))
    elif op == "wait_value":
        _validate_wait_value(i, step, schema, findings)
    elif op == "wait_change":
        if "fields" in step and not isinstance(step["fields"], list):
            findings.append(("error", i, "'fields' must be a list"))
    elif op == "assert":
        _validate_wait_value(i, step, schema, findings)
        if "message" in step and not isinstance(step["message"], str):
            findings.append(("error", i, "'message' must be a string"))
    elif op == "refresh":
        pass
    elif op == "raw":
        msg = step.get("message")
        if not isinstance(msg, dict):
            findings.append(("error", i, "raw needs 'message' (object)"))
    elif op == "bundle":
        if "set" in step:
            _validate_set(i, {"values": step["set"]},
                          schema, findings, "bundle.")
        if "actions" in step:
            if not isinstance(step["actions"], list):
                findings.append(("error", i, "'actions' must be a list"))
            else:
                for j, act in enumerate(step["actions"]):
                    _validate_action(i, act, schema, findings,
                                     f"bundle.actions[{j}].")
        if "select" in step:
            sel = step["select"]
            if not (isinstance(sel, (list, tuple)) and len(sel) == 2):
                findings.append(("error", i,
                                 "'select' must be [level, name]"))
            else:
                _validate_select(i, {"level": sel[0], "name": sel[1]},
                                 entities, findings)
    else:
        findings.append(("error", i, f"unknown op {op!r}"))


def _validate_set(i, step, schema, findings, prefix):
    values = step.get("values")
    if not isinstance(values, dict):
        findings.append(("error", i, f"{prefix}set missing 'values' (dict)"))
        return
    for name, value in values.items():
        if name not in schema:
            findings.append(("error", i,
                             f"{prefix}unknown field {name!r}"))
            continue
        meta = schema[name] or {}
        kind = meta.get("kind")
        if kind == "number":
            if not isinstance(value, (int, float)) \
                    or isinstance(value, bool):
                findings.append(
                    ("error", i,
                     f"{prefix}{name} should be number, got "
                     f"{type(value).__name__}")
                )
        elif kind == "bool":
            if not isinstance(value, bool):
                findings.append(
                    ("error", i,
                     f"{prefix}{name} should be bool, got "
                     f"{type(value).__name__}")
                )
        elif kind == "enum":
            opts = meta.get("options") or []
            if opts and value not in opts:
                findings.append(
                    ("error", i,
                     f"{prefix}{name} {value!r} not in {opts}")
                )


def _validate_action(i, step, schema, findings, prefix):
    kind = step.get("kind") or step.get("action")
    if not kind:
        findings.append(("error", i, f"{prefix}action missing 'kind'"))
        return
    if kind not in schema:
        findings.append(("error", i,
                         f"{prefix}unknown action {kind!r}"))
        return
    meta = schema[kind] or {}
    if meta.get("kind") != "action":
        findings.append(("error", i,
                         f"{prefix}{kind!r} is not an action"))
        return
    payload = step.get("payload") or {}
    if not isinstance(payload, dict):
        findings.append(("error", i,
                         f"{prefix}payload must be a dict"))
        return
    for req in meta.get("requires") or []:
        req_name = req.get("name")
        if req_name and req_name not in payload:
            findings.append(("error", i,
                             f"{prefix}{kind!r} requires "
                             f"{req_name!r}"))


def _validate_select(i, step, entities, findings):
    level = step.get("level")
    name = step.get("name")
    if not level or not name:
        findings.append(("error", i, "select missing 'level' or 'name'"))
        return
    if level not in entities:
        findings.append(("error", i, f"unknown level {level!r}"))
        return
    items = (entities[level] or {}).get("items") or []
    item_names = {it.get("name") for it in items}
    if name not in item_names:
        findings.append(
            ("error", i,
             f"{name!r} not in level {level!r}; "
             f"available: {sorted(n for n in item_names if n)}")
        )


def _validate_wait_value(i, step, schema, findings):
    name = step.get("name")
    if not name:
        findings.append(("error", i, "missing 'name'"))
        return
    if name not in schema:
        findings.append(("error", i, f"unknown field {name!r}"))
    try:
        _extract_predicate(step)
    except ValueError as e:
        findings.append(("error", i, str(e)))


# ============================================================================
#  Execution
# ============================================================================

def _plan_step(step: dict) -> str:
    op = step.get("op")
    if op == "set":
        return f"patch {json.dumps(step.get('values') or {}, default=str)}"
    if op == "action":
        p = step.get("payload")
        extra = f" payload={json.dumps(p, default=str)}" if p else ""
        return f"command {step.get('kind')!r}{extra}"
    if op == "select":
        return f"select {step.get('level')}/{step.get('name')}"
    if op == "deselect":
        return "deselect"
    if op == "sleep":
        return f"sleep {step.get('seconds')}s"
    if op == "wait_value":
        k, v = _extract_predicate(step)
        return (f"wait_value {step.get('name')!r} {k} {v!r} "
                f"(timeout {step.get('timeout', 10)}s)")
    if op == "wait_change":
        return (f"wait_change {step.get('fields', 'any')} "
                f"(timeout {step.get('timeout', 10)}s)")
    if op == "assert":
        k, v = _extract_predicate(step)
        return f"assert {step.get('name')!r} {k} {v!r}"
    if op == "refresh":
        return "state_request"
    if op == "raw":
        return f"raw {json.dumps(step.get('message') or {}, default=str)}"
    if op == "bundle":
        parts = []
        if step.get("set"):
            parts.append(f"patch {len(step['set'])} value(s)")
        for act in step.get("actions") or []:
            parts.append(f"command {act.get('kind')!r}")
        if step.get("select"):
            parts.append(f"select {step['select']}")
        parts.append("state_request")
        return "bundle [" + " → ".join(parts) + "]"
    return f"(unknown op {op!r})"


def _execute_step(client, step: dict,
                  default_timeout: float = 30.0) -> None:
    op = step.get("op")
    if op == "set":
        values = step.get("values")
        if not isinstance(values, dict):
            raise ValueError("set needs 'values' (dict)")
        client.patch_and_verify(values, timeout=step.get("timeout", 5.0))
        print(f"  · set {list(values.keys())}")
    elif op == "action":
        kind = step.get("kind") or step.get("action")
        if not kind:
            raise ValueError("action needs 'kind'")
        payload = step.get("payload") or {}
        timeout = float(step.get("timeout", default_timeout))
        result = client.command(kind, payload, timeout=timeout)
        print(f"  · action {kind}")
        if result.get("result") is not None:
            print(f"    result: {json.dumps(result['result'], default=str)}")
    elif op == "select":
        level = step.get("level")
        name = step.get("name")
        client.command("select", {"level": level, "name": name},
                       timeout=step.get("timeout", default_timeout))
        print(f"  · select {level}/{name}")
    elif op == "deselect":
        client.command("deselect", {},
                       timeout=step.get("timeout", default_timeout))
        print("  · deselect")
    elif op == "sleep":
        s = float(step.get("seconds", 1.0))
        time.sleep(s)
        print(f"  · sleep {s}s")
    elif op == "wait_value":
        name = step.get("name")
        k, expected = _extract_predicate(step)
        timeout = float(step.get("timeout", 10.0))
        client.wait_value(name,
                          lambda v: _predicate_matches(k, expected, v),
                          timeout=timeout)
        print(f"  · wait_value {name} {k} {expected!r} → ok")
    elif op == "wait_change":
        fields = step.get("fields")
        timeout = float(step.get("timeout", 10.0))
        changed = client.wait_change(fields=fields, timeout=timeout)
        print(f"  · wait_change → {changed}")
    elif op == "assert":
        name = step.get("name")
        k, expected = _extract_predicate(step)
        msg = step.get("message") or f"assertion failed: {name} {k} {expected!r}"
        current = client.values().get(name)
        if not _predicate_matches(k, expected, current):
            raise AssertionError(msg)
        print(f"  · assert {name} {k} {expected!r} → ok")
    elif op == "refresh":
        client.request_state_and_wait(timeout=step.get("timeout", 5.0))
        print("  · refresh")
    elif op == "raw":
        client.raw_send(step.get("message") or {})
        print(f"  · raw {json.dumps(step.get('message'), default=str)}")
    elif op == "bundle":
        _run_bundle(client, step, default_timeout)
    else:
        raise ValueError(f"unknown op {op!r}")


def _run_bundle(client, step, default_timeout):
    actions = step.get("actions") or []
    select = step.get("select")
    if select is not None and not (isinstance(select, (list, tuple))
                                   and len(select) == 2):
        raise ValueError("bundle.select must be [level, name]")
    timeout = float(step.get("timeout", default_timeout))
    wait = bool(step.get("wait_for_running", True))

    def on_progress(kind, data):
        if kind == "patch":
            print(f"  · set {list(data.get('values', {}).keys())}")
        elif kind == "action":
            print(f"  · action {data.get('kind')}")
            if data.get("warning"):
                print(f"    warning: {data['warning']}")
        elif kind == "select":
            print(f"  · select {data['level']}/{data['name']}")

    client.bundle(
        set=step.get("set"),
        actions=actions,
        select=tuple(select) if select else None,
        wait_for_running=wait,
        timeout=timeout,
        on_progress=on_progress,
    )
    print("  · refresh")


def run_script(client, script_path, *,
               stop_on_error: bool = True,
               plan_only: bool = False,
               verbose: bool = False,
               json_output: bool = False) -> int:
    try:
        script = load_script(script_path)
    except (OSError, json.JSONDecodeError, ValueError) as e:
        print(f"error loading script: {e}", file=sys.stderr)
        return 2

    steps = script["steps"]
    name = script.get("name") or "(unnamed)"

    if plan_only:
        if json_output:
            plan = [{"index": i, "plan": _plan_step(s)}
                    for i, s in enumerate(steps)]
            print(json.dumps({"name": name, "steps": plan}, indent=2))
        else:
            print(f"plan for {name}: {len(steps)} step(s)")
            for i, s in enumerate(steps):
                print(f"  {i+1}. {_plan_step(s)}")
        return 0

    # Validate first
    findings = validate(client, script)
    errors = [f for f in findings if f[0] == "error"]
    if errors:
        print(f"validation failed ({len(errors)} error(s)):",
              file=sys.stderr)
        for level, i, msg in findings:
            marker = "✗" if level == "error" else "⚠"
            where = f"step {i}" if i is not None else "script"
            print(f"  {marker} [{where}] {msg}", file=sys.stderr)
        return 1

    print(f"running {name}: {len(steps)} step(s)")
    start = time.monotonic()

    for i, step in enumerate(steps):
        op = step.get("op") if isinstance(step, dict) else "?"
        print(f"step {i+1}/{len(steps)} ({op})")
        try:
            _execute_step(client, step)
        except (RemoteCliError, AssertionError, ValueError) as e:
            print(f"  ✗ {e}", file=sys.stderr)
            if stop_on_error:
                elapsed = time.monotonic() - start
                print(f"stopped after {i+1}/{len(steps)} steps "
                      f"({elapsed:.2f}s)", file=sys.stderr)
                return 1
            else:
                print("  (continuing)", file=sys.stderr)

    elapsed = time.monotonic() - start
    print(f"done in {elapsed:.2f}s")
    return 0


def run_validate(client, script_path, json_output: bool) -> int:
    try:
        script = load_script(script_path)
    except (OSError, json.JSONDecodeError, ValueError) as e:
        print(f"error loading script: {e}", file=sys.stderr)
        return 2

    findings = validate(client, script)
    errors = [f for f in findings if f[0] == "error"]
    warnings = [f for f in findings if f[0] == "warning"]

    if json_output:
        print(json.dumps({
            "valid": not errors,
            "error_count": len(errors),
            "warning_count": len(warnings),
            "findings": [
                {"level": lvl, "step": i, "message": m}
                for lvl, i, m in findings
            ],
        }, indent=2))
        return 0 if not errors else 1

    print(f"script: {script_path} ({len(script['steps'])} step(s))")
    if not findings:
        print("✓ valid")
        return 0
    for level, i, msg in findings:
        marker = "✗" if level == "error" else "⚠"
        where = f"step {i}" if i is not None else "script"
        print(f"  {marker} [{where}] {msg}")
    print(f"\n{len(errors)} error(s), {len(warnings)} warning(s)")
    return 0 if not errors else 1
