"""
Bundle validator — check a bundle against the live schema before running it.

The runner executes commands in order and does not roll back on failure, so
a bundle with a typo, a missing required arg, or an out-of-range enum value
takes the document partway through a sequence and stops. This module finds
those problems ahead of time, without touching the document.

What it checks:

  - top-level shape (commands is a list, each entry has a type)
  - every command's `type` exists in the daemon schema
  - every arg the bundle supplies is declared on that command
  - every required arg of the command is present
  - supplied values match the schema's declared type (int/float/bool/str)
  - supplied values are in the schema's `choices` list, when one is declared
  - include directives reference a batch that exists in the library

What it does NOT check (by design):

  - whether the sequence as a whole is coherent (e.g. create-then-fill)
  - whether the document is in a state for the commands to succeed
  - nested includes (validate each saved batch separately)
"""

from typing import Any, Dict, List, Optional

from .library import BatchLibrary


class BundleValidationError:
    """A single validation finding. Level is 'error' or 'warning'."""

    def __init__(self, index: Optional[int], command: Optional[str],
                 level: str, message: str):
        self.index = index
        self.command = command
        self.level = level
        self.message = message

    def __str__(self) -> str:
        if self.index is not None:
            head = f"[{self.index}]"
            if self.command:
                head += f" {self.command}"
            return f"{head}: {self.message}"
        return self.message

    def to_dict(self) -> Dict[str, Any]:
        return {
            'index': self.index,
            'command': self.command,
            'level': self.level,
            'message': self.message,
        }


def _strip_dashes(name: str) -> str:
    """Schema stores arg names as '--name'; bundles use 'name'."""
    return name.lstrip('-')


def _value_matches_type(value: Any, schema_type: str) -> bool:
    """
    True iff `value` is acceptable for the schema's declared type.

    Note the bool/int trap: in Python, `True` is an instance of `int`.
    We exclude bool where an int is expected (and vice versa) so that a
    bundle sending {"value": true} where an int is declared is caught.
    """
    if schema_type == 'int':
        return isinstance(value, int) and not isinstance(value, bool)
    if schema_type == 'float':
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if schema_type == 'bool':
        return isinstance(value, bool)
    if schema_type == 'str':
        return isinstance(value, str)
    # Unknown schema type: do not block on it.
    return True


def validate_bundle(
    bundle: Any,
    schema: Dict[str, Any],
    library: Optional[BatchLibrary] = None,
) -> List[BundleValidationError]:
    """
    Validate a bundle against a schema.

    Returns a list of findings. Empty means the bundle is clean.
    Findings with level 'error' mean the bundle will fail or misbehave
    at runtime. 'warning' means it will probably run but something is
    worth a second look.
    """
    findings: List[BundleValidationError] = []

    # ---------- top-level shape ----------
    if not isinstance(bundle, dict):
        findings.append(BundleValidationError(
            None, None, 'error',
            f"Bundle must be a JSON object, got {type(bundle).__name__}"))
        return findings

    if 'commands' not in bundle:
        findings.append(BundleValidationError(
            None, None, 'error', "Bundle is missing the 'commands' key"))
        return findings

    commands = bundle.get('commands')
    if not isinstance(commands, list):
        findings.append(BundleValidationError(
            None, None, 'error',
            f"'commands' must be a list, got {type(commands).__name__}"))
        return findings

    if 'id' in bundle and not isinstance(bundle['id'], str):
        findings.append(BundleValidationError(
            None, None, 'warning',
            f"Bundle 'id' is {type(bundle['id']).__name__}; "
            f"the daemon echoes it back as-is and strings are conventional"))

    # ---------- build command lookup from schema ----------
    # Schema keys are like 'brush_set_brush_size'. We care about the
    # command name alone, which is the same regardless of category —
    # only one handler is wired up per name.
    by_command: Dict[str, Dict[str, Any]] = {}
    for cmd_key, cmd_info in schema.items():
        cmd_name = cmd_info.get('command')
        if cmd_name:
            by_command.setdefault(cmd_name, cmd_info)

    # ---------- per-command checks ----------
    for i, cmd in enumerate(commands):
        if not isinstance(cmd, dict):
            findings.append(BundleValidationError(
                i, None, 'error',
                f"Command must be a JSON object, got {type(cmd).__name__}"))
            continue

        cmd_type = cmd.get('type')
        if not cmd_type:
            findings.append(BundleValidationError(
                i, None, 'error', "Command is missing the 'type' key"))
            continue

        # `include` is a synthetic directive handled by the executor, not
        # a command the daemon knows about.
        if cmd_type == 'include':
            _validate_include(i, cmd, library, findings)
            continue

        if cmd_type not in by_command:
            findings.append(BundleValidationError(
                i, cmd_type, 'error',
                f"Unknown command '{cmd_type}' (not in daemon schema)"))
            continue

        schema_args_raw = by_command[cmd_type].get('args', {}) or {}
        schema_args = {_strip_dashes(k): v for k, v in schema_args_raw.items()}

        provided = {k: v for k, v in cmd.items() if k != 'type'}

        # Unknown args
        for arg_name in provided:
            if arg_name not in schema_args:
                known = ', '.join(sorted(schema_args)) or 'none'
                findings.append(BundleValidationError(
                    i, cmd_type, 'error',
                    f"Unknown arg '{arg_name}' for '{cmd_type}' "
                    f"(declared args: {known})"))

        # Missing required args
        for arg_name, arg_info in schema_args.items():
            if arg_info.get('required') and arg_name not in provided:
                findings.append(BundleValidationError(
                    i, cmd_type, 'error',
                    f"Missing required arg '{arg_name}' for '{cmd_type}'"))

        # Type and choice checks on provided args
        for arg_name, value in provided.items():
            arg_info = schema_args.get(arg_name)
            if not arg_info:
                continue  # already reported as unknown
            schema_type = arg_info.get('type', 'str')

            if not _value_matches_type(value, schema_type):
                findings.append(BundleValidationError(
                    i, cmd_type, 'error',
                    f"Arg '{arg_name}' should be {schema_type}, "
                    f"got {type(value).__name__} ({value!r})"))
                continue

            choices = arg_info.get('choices')
            if choices and value not in choices:
                joined = ', '.join(str(c) for c in choices)
                findings.append(BundleValidationError(
                    i, cmd_type, 'error',
                    f"Arg '{arg_name}' value {value!r} is not in "
                    f"choices: {joined}"))

    return findings


def _validate_include(
    index: int,
    cmd: Dict[str, Any],
    library: Optional[BatchLibrary],
    findings: List[BundleValidationError],
) -> None:
    """Validate a single include directive in place."""
    batch_name = cmd.get('batch')
    if not batch_name:
        findings.append(BundleValidationError(
            index, 'include', 'error',
            "include directive is missing the 'batch' key"))
        return

    extra = set(cmd) - {'type', 'batch'}
    if extra:
        findings.append(BundleValidationError(
            index, 'include', 'warning',
            f"include directive has unsupported keys: "
            f"{', '.join(sorted(extra))}"))

    if library is None:
        return

    if not library.exists(batch_name):
        findings.append(BundleValidationError(
            index, 'include', 'error',
            f"Included batch '{batch_name}' not found in library"))
