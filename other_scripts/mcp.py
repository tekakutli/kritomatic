#!/usr/bin/env python3
"""
mcp.py — Hand-Triggered Tool Router
===================================

PURPOSE
-------
This file is the *executor*. It holds the tool whitelist and the AST-
patching logic that runs a registered tool with overridden config
values. It does not describe what tools do, when to use them, or what
their parameters mean — that lives in INSTRUCT.md.

The pipeline is:

    human language  ->  (LLM)  ->  JSON  ->  [mcp.py]  ->  tool script runs

The JSON this router accepts is intentionally tiny:

    {
      "tool": "<tool_name>",
      "params": { "<VARIABLE_NAME>": <json value>, ... }
    }

Rules:
    * `tool` MUST match a key in TOOLS below.
    * Every key in `params` MUST appear in that tool's `params` list.
      Unknown keys are rejected outright to catch typos early.
    * Omitted params fall back to the defaults hard-coded in the tool
      script's CONFIGURATION block.
    * JSON values map to Python literals:
        string        -> str
        number        -> int or float
        true / false  -> bool
        null          -> None
        array         -> list
        object        -> dict
      These are inserted as *literal AST nodes*, never re-parsed as source,
      so a param value can never smuggle in executable code.
    * The OUTPUT_PDF param, if given without a file extension, gets
      ".pdf" appended automatically ("album" -> "album.pdf"). This is
      a convenience for the routing LLM, which emits OUTPUT_PDF
      verbatim; it is NOT license to guess at any other param.

--------------------------------------------------------------------------------
DIVISION OF LABOR
--------------------------------------------------------------------------------
This file and INSTRUCT.md have distinct jobs and MUST NOT duplicate each
other's content:

    mcp.py       (this file)   the executor:
                                 * which tool names exist
                                 * which file implements each tool
                                 * which function is the entry point
                                 * which top-level variables may be patched
                                 * how JSON maps to AST and gets executed

    INSTRUCT.md                the routing manual:
                                 * what each tool does (use_case, objective)
                                 * what each tool produces (outputs)
                                 * what each tool is NOT (not_for)
                                 * what each param means, its type, its
                                   default, and the human phrasings that
                                   should map to it
                                 * worked examples and translation rules

WHEN THE TWO DISAGREE, THIS FILE WINS AT RUNTIME. INSTRUCT.md is a
routing *suggestion* for the LLM; this file is *policy*. If a tool name
or a param name is not in TOOLS below, the router rejects it — no matter
what INSTRUCT.md claims.

--------------------------------------------------------------------------------
FOR THE AI READING THIS FILE
--------------------------------------------------------------------------------
If the human asks you to extend this router, then:

    1. DO NOT emit a diff, a patch, or a partial fragment.
    2. Output the ENTIRE file verbatim, from the `#!` line to the final
       newline, with your edits applied in place.
    3. Preserve this docstring, the TOOLS registry shape, and every
       helper below. Where you change behavior, add a comment explaining
       *why* so the next reader understands.
    4. PRESERVE FUNCTIONAL CONTENT, DO NOT PRUNE IT. Every existing
       comment, docstring, section header, and prose block that
       carries functional content — a rule, a rationale, a guard
       against a known failure — is load-bearing and must be left
       intact. Do not delete, merge, shorten, reword, or "clean up"
       any such passage unless you were explicitly asked to change
       it. If two passages seem redundant, they are almost certainly
       guarding two different failure modes; leave both. If a section
       looks stale or unused (for example, the SHELL INTEGRATION
       block below), leave it anyway and mention it in your reply —
       the human decides what is dead, not you. When in doubt, add
       rather than remove. The docstring is long on purpose: its
       length IS the documentation, and trimming functional content
       destroys information that the next reader — human or AI —
       will need. Narration is not functional content; it is governed
       by rule 5.
    5. SPEAK IN GENERAL TERMS; DO NOT ADD HISTORICAL NARRATION.
       Rules, comments, and docstrings in this file must read as
       though they had always been true. Do not record when an edit
       happened, what a file or rule used to contain, or how a rule
       was previously numbered or labelled. A reader arriving cold
       should not be able to tell from the prose that anything ever
       changed. This rule governs what *you* add. Existing narration
       is the human's to remove; leave it in place unless you are
       already rewriting that passage for another reason, in which
       case prefer the general phrasing.

STRUCTURAL CHANGES ARE TWO-FILE CHANGES. Adding a tool, adding a param,
or renaming either, requires editing BOTH this file (to whitelist the
name) AND INSTRUCT.md (to describe it for the routing LLM). Cosmetic
edits to INSTRUCT.md — rewording a spoken_as phrase, adding an example,
fixing prose — may be done alone. But whenever the *set* of tool names
or param names changes, both files must move together in the same pass.

To add a new tool:
    1. Drop its .py file into the same directory as this one.
    2. Add an entry to TOOLS below with exactly three keys:
         "file"   — filename, relative to this script's directory
         "entry"  — name of the function to call (usually "main")
         "params" — list of top-level variable NAMES in the tool's
                    source that may be overridden. Each name must
                    appear at module level in the tool file as a plain
                    `NAME = value` assignment; the AST patcher only
                    rewrites top-level assignments.
    3. Add a matching prose entry to INSTRUCT.md: description, use_case,
       objective, outputs, not_for, and per-param type/default/
       description/spoken_as.

NAMING CONVENTION: a tool's registry key SHOULD match its filename
without the ".py" extension. `imgs2pdf` points at `imgs2pdf.py`;
`labels2grid` points at `labels2grid.py`. When they diverge, prefer
renaming the key over renaming the file — the filename is what a human
types at the shell, the key is what the routing LLM emits, and keeping
them equal means a human reading the JSON can guess the file, and vice
versa.

--------------------------------------------------------------------------------
TOLERANCE POLICY
--------------------------------------------------------------------------------
The router is deliberately STRICT about semantics and only mildly
TOLERANT about presentation. The line between the two is:

    ALLOWED (presentation-level, normalized away before parsing):
        * A single markdown code fence wrapping the JSON
          (```json ... ``` or bare ``` ... ```).
        * Leading / trailing whitespace and newlines.
        * Appending ".pdf" to OUTPUT_PDF when the value has no file
          extension. Bounded, single-param, documented here.
        * Accepting the instruction JSON as a command-line argument
          (inline form) in addition to a file path or stdin. This is
          an *input channel*, not a semantic tolerance — the JSON
          itself is parsed and validated identically regardless of
          where it came from. See USAGE for detection rules.
        These are unambiguously correct-but-wrapped inputs. Unwrapping
        them loses no information about the routing LLM's intent.

    FORBIDDEN (semantic-level, fail loudly):
        * Fuzzy tool-name matching ("grid" -> "grid2pdf").
        * Param-name aliases ("COLS" -> "GRID_COLS").
        * Silently dropping unknown params.
        * Coercing types, guessing at units, "fixing" obvious typos.
        * Alternate top-level keys ("parameters" for "params",
          "tool_name" for "tool", etc.).
        * Any other case where the router would have to *guess* what
          the LLM meant rather than read what it wrote.

Why the asymmetry: every tolerated variant is a place where a future
bug can hide. If the routing LLM emits "parameters" instead of "params"
and the router accepts both, the drift becomes invisible — you can no
longer tell whether INSTRUCT.md is being followed or ignored. Rejecting
loudly keeps the manual honest.

If you are an AI editing this file and you feel tempted to add a
semantic fallback because a routing AI "keeps getting it wrong": STOP.
The correct fix is almost always to edit INSTRUCT.md — clarify the rule,
add a worked example, sharpen a spoken_as list. Loosening the executor
hides the problem; fixing the manual removes it. Only add tolerance
when the input is unambiguously correct but wrapped differently
(fences, whitespace), never when the input is ambiguously wrong but
guessable. If you do add a tolerance, add it to the ALLOWED list above
with a one-line rationale.

--------------------------------------------------------------------------------
USAGE
--------------------------------------------------------------------------------
    python3 mcp.py                # interactive: prompt on /dev/tty and
                                  # read lines until the input parses as
                                  # JSON, then dispatch it. This is the
                                  # no-argument default.
    python3 mcp.py path/to/instruction.json
                                  # read the instruction from a file
    python3 mcp.py '{"tool":"...","params":{...}}'
                                  # inline JSON: the argument itself is
                                  # the instruction. Detected when the
                                  # argument, after the same fence-strip
                                  # tolerance the file path uses, begins
                                  # with "{". No file or stdin round-
                                  # trip needed for one-liners. NOTE:
                                  # use SHELL single quotes around the
                                  # JSON — double quotes in the shell
                                  # would terminate at the first inner
                                  # " and split the JSON across argv.
    python3 mcp.py -              # read instruction JSON from stdin
    python3 mcp.py --self         # print this file (useful when editing)
    python3 mcp.py --help         # print this file's docstring

--------------------------------------------------------------------------------
SHELL INTEGRATION
--------------------------------------------------------------------------------
The human-facing entry point is a one-line shell alias that runs this
script. Interactive behavior — the `json> ` prompt, line accumulation,
the same fence-strip tolerance as _load_instruction, the blank-line
escape hatch, the 200-line cap, and the post-parse input drain — is
implemented in-process by `_interactive_mode` below. The shell side
therefore has nothing left to do but point at the script:

    alias mcp='python3 /path/to/mcp.py'

Adjust the path to the absolute location of this file, then drop the
alias into ~/.bashrc and `source ~/.bashrc`. Alternatively, `chmod +x`
this file and alias its path directly:

    alias mcp=/path/to/mcp.py

Behavior notes (enforced inside this file):

    * With no arguments, `mcp` prompts on /dev/tty with `json> ` and
      reads lines until the accumulated text parses as JSON. A blank
      line forces an early parse attempt — useful as an escape hatch
      if the input is truncated.
    * It applies the same fence-strip tolerance as _load_instruction,
      so a fenced paste is accepted at the prompt too.
    * The 200-line cap prevents a runaway paste from looping forever.
    * After a successful parse, pending input is drained so trailing
      prose (should the routing LLM ignore the "no prose" rule) does
      not leak into the next shell command.
    * With arguments, the alias forwards them verbatim, so
      `mcp cmd.json`, `mcp -`, `mcp --self`, `mcp --help`, and the
      inline form `mcp '{"tool":...}'` all work as documented above.
      The alias itself needs no change for the inline form — the JSON
      is just an argument like any other.

The alias is a convenience, not a contract. This file's interface is
its command-line arguments and stdin; the `mcp` alias is one particular
UX over that interface.

--------------------------------------------------------------------------------
"""

import ast
import json
import re
import select
import sys
from pathlib import Path

# =============================================================================
# TOOL REGISTRY — the whitelist. No prose belongs here.
# =============================================================================
#
# Each entry has exactly three keys:
#
#     "file"    filename of the tool script, relative to this directory
#     "entry"   name of the function to invoke (usually "main")
#     "params"  list of top-level variable names in the tool file that
#               may be overridden by the instruction JSON. Any key in
#               the instruction's "params" that is NOT in this list will
#               be rejected before the tool runs.
#
# For the human-readable description of each tool and each param, see
# INSTRUCT.md. That file is the routing manual; this one is the gate.
#
# Example instruction for imgs2pdf (shape only — see INSTRUCT.md for
# what each param actually means):
#
#     {
#       "tool": "imgs2pdf",
#       "params": {
#         "INPUT_DIR": "/home/me/photos",
#         "OUTPUT_PDF": "/home/me/album.pdf",
#         "TWO_HORIZONTAL_PER_PAGE": true,
#         "SHOW_FILENAME_LABEL": false
#       }
#     }
# =============================================================================

TOOLS = {
    "captionize": {
        "file": "captionize.py",
        "entry": "main",
        "params": [
            "INPUT_IMAGE",
            "TEXT",
            "SHRINK_RATIO",
            "ROTATE_BEFORE_PROCESSING",
        ],
    },
    "imgs2pdf": {
        "file": "imgs2pdf.py",
        "entry": "main",
        "params": [
            "INPUT_DIR",
            "OUTPUT_PDF",
            "TWO_HORIZONTAL_PER_PAGE",
            "SHOW_FILENAME_LABEL",
        ],
    },
    "labels2grid": {
        "file": "labels2grid.py",
        "entry": "main",
        "params": [
            "LABELS",
            "SHOW_CELL_BOUNDARIES",
        ],
    },
    "grid2pdf": {
        "file": "grid2pdf.py",
        "entry": "main",
        "params": [
            "INPUT_IMAGE",
            "OUTPUT_PDF",
            "C",
            "R",
            "OVERLAP_DELTA",
        ],
    },
    "imgs2grid": {
        "file": "imgs2grid.py",
        "entry": "main",
        "params": [
            "INPUT_DIR",
            "OUTPUT_PDF",
            "SHOW_FILENAME",
            "NUMBER_IMAGES",
            "GRID_COLS",
            "GRID_ROWS",
        ],
    },
    # ── Add more tools here, following the three-key shape above. ──
}

# =============================================================================
# INTERNALS — you normally do not need to edit below this line.
# =============================================================================

# Matches a single markdown code fence wrapping its whole body:
#   ```json\n{...}\n```
#   ```\n{...}\n```
# The language tag (json, JSON, or absent) is optional. Anything else
# around the JSON — prose, multiple fences, partial wrapping — is NOT
# stripped, because recognizing it would require guessing. See the
# TOLERANCE POLICY section above for why.
_FENCE_RE = re.compile(
    r"^\s*```(?:json|JSON)?\s*\n?(?P<body>.*?)\n?```\s*$",
    re.DOTALL,
)


def _strip_fences(text):
    """Unwrap a single markdown code fence around `text`, if present.

    Bounded normalization only. If the text is not a cleanly fenced
    JSON blob, it is returned unchanged so that the JSON parser produces
    a loud, specific error rather than a silent misparse.
    """
    m = _FENCE_RE.match(text)
    if m:
        return m.group("body")
    return text


def _json_to_ast(value):
    """Convert a decoded JSON value into a *safe* Python AST literal node.

    This is the only place JSON enters the tool's source. Every value
    becomes a Constant / List / Dict — never a parsed expression — so an
    instruction like "__import__('os').system('rm -rf /')" stays a
    harmless string instead of executable code.
    """
    if value is None or isinstance(value, (bool, int, float, str)):
        return ast.Constant(value=value)
    if isinstance(value, list):
        return ast.List(elts=[_json_to_ast(v) for v in value], ctx=ast.Load())
    if isinstance(value, dict):
        return ast.Dict(
            keys=[ast.Constant(value=k) for k in value],
            values=[_json_to_ast(v) for v in value.values()],
        )
    raise TypeError(f"Unsupported JSON value: {value!r} ({type(value).__name__})")


def _patch_tree(tree, overrides):
    """Replace the value of each whitelisted top-level assignment.

    We only touch module-level `NAME = ...` statements whose NAME is in
    `overrides`. Anything more exotic (augmented assign, tuple unpack,
    assignments inside `if`/`try` blocks, etc.) is left untouched — if a
    parameter needs to be patchable it must be a plain top-level
    assignment in the tool file.
    """
    patched = set()
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if isinstance(target, ast.Name) and target.id in overrides:
                node.value = _json_to_ast(overrides[target.id])
                patched.add(target.id)

    missing = set(overrides) - patched
    if missing:
        raise SystemExit(
            f"These params were not found as top-level assignments in the "
            f"tool source: {sorted(missing)}. (Do they exist, and are they "
            f"declared with a plain `NAME = value` at module level?)"
        )


def _autocomplete_pdf_extension(params):
    """Append '.pdf' to OUTPUT_PDF if the value has no extension.

    Bounded convenience: only fires when the value is a non-empty string
    with no suffix. Does not touch any other param. See TOLERANCE POLICY
    for why this stays narrow.
    """
    out = dict(params)
    val = out.get("OUTPUT_PDF")
    if isinstance(val, str) and val and not Path(val).suffix:
        out["OUTPUT_PDF"] = val + ".pdf"
    return out


def run_tool(tool_name, params):
    if tool_name not in TOOLS:
        raise SystemExit(
            f"Unknown tool: {tool_name!r}. Known tools: {sorted(TOOLS)}"
        )

    spec = TOOLS[tool_name]

    unknown = set(params) - set(spec["params"])
    if unknown:
        raise SystemExit(
            f"Unknown parameter(s) for {tool_name!r}: {sorted(unknown)}. "
            f"Allowed: {sorted(spec['params'])}"
        )

    params = _autocomplete_pdf_extension(params)

    script_path = Path(__file__).resolve().parent / spec["file"]
    if not script_path.is_file():
        raise SystemExit(f"Tool file not found: {script_path}")

    source = script_path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(script_path))
    _patch_tree(tree, params)
    ast.fix_missing_locations(tree)

    code = compile(tree, filename=str(script_path), mode="exec")

    # Exec with __name__ != "__main__" so the tool's own
    # `if __name__ == "__main__": main()` guard stays silent. We invoke
    # the entry point ourselves, deliberately.
    namespace = {
        "__name__": f"_tool_{tool_name}",
        "__file__": str(script_path),
    }
    exec(code, namespace)

    entry = namespace.get(spec["entry"])
    if entry is None or not callable(entry):
        raise SystemExit(
            f"Entry point {spec['entry']!r} not found (or not callable) "
            f"in {spec['file']}"
        )

    # Neutralize sys.argv so the tool doesn't accidentally pick up OUR
    # command-line arguments. With argv == [script], tools that fall
    # through to their in-file defaults use the values we patched in.
    saved_argv = sys.argv
    sys.argv = [str(script_path)]
    try:
        return entry()
    finally:
        sys.argv = saved_argv


def _load_instruction(arg):
    """Read the instruction text and parse it as JSON.

    Presentation-level tolerance is applied here (see TOLERANCE POLICY
    in the module docstring): a single wrapping markdown fence and
    surrounding whitespace are stripped before parsing. Everything else
    is passed through to json.loads unchanged, so malformed input fails
    with a specific message rather than being silently repaired.

    INPUT SOURCES, in detection order:
        1. The literal "-"  — read the instruction from stdin.
        2. Inline JSON       — the argument itself is the instruction
           text. Detected when the argument, after the same fence-strip
           tolerance used below, begins with "{". This is the
           `mcp '{"tool":...}'` form: it skips the file / stdin round-
           trip entirely, which is what makes one-liners convenient.
           The detection runs BEFORE the Path.is_file() branch, so it
           is the caller's responsibility to use a shell quoting style
           that keeps the JSON in a single argv element (single quotes
           in POSIX shells).
        3. A filesystem path  — read the instruction from that file.
    """
    if arg == "-":
        raw = sys.stdin.read()
    else:
        # Inline-JSON detection. A JSON object always begins with "{"
        # once whitespace / fence-strip tolerance has been applied. A
        # filesystem path beginning with "{" is possible in principle
        # but vanishingly rare; if it ever occurs, the "-" stdin form
        # or an explicit re-invocation from a directory where the path
        # does not start with "{" is the escape hatch. Checking inline
        # first is what lets the common one-liner form skip the disk.
        if _strip_fences(arg).strip().startswith("{"):
            raw = arg
        else:
            path = Path(arg)
            if not path.is_file():
                raise SystemExit(f"Instruction file not found: {path}")
            raw = path.read_text(encoding="utf-8")

    cleaned = _strip_fences(raw).strip()

    try:
        return json.loads(cleaned)
    except json.JSONDecodeError as e:
        raise SystemExit(
            f"Instruction is not valid JSON: {e}\n"
            f"--- received ---\n{cleaned}\n--- end ---"
        )


def _try_parse(text):
    """Best-effort parse check for the interactive prompt.

    Returns True if `text` (after fence-strip tolerance) is a complete,
    valid JSON value. Never raises — this is a *liveness* signal for the
    interactive read loop, not a validator; the authoritative parse
    happens once the user has finished typing, and errors are reported
    there with the same diagnostics as _load_instruction.
    """
    try:
        json.loads(_strip_fences(text).strip())
    except (json.JSONDecodeError, ValueError):
        return False
    return True


def _interactive_mode():
    """Prompt on /dev/tty and read lines until the accumulated text
    parses as instruction JSON, then dispatch it.

    Implements the `json> ` prompt, the blank-line escape hatch, the
    same fence-strip tolerance as _load_instruction, the 200-line cap,
    and the post-parse input drain in-process, so the human needs only
    a one-line shell alias. See the SHELL INTEGRATION section of the
    module docstring for the alias.

    Returns an exit status (0 on success, 1 on user-side abort). If the
    instruction itself is well-formed but names an unknown tool or
    unknown param, the usual SystemExit from run_tool propagates.
    """
    # Prefer /dev/tty: the prompt must appear on the terminal even when
    # stdin/stdout are being redirected (e.g. when `mcp` is used mid-
    # pipeline). Fall back to the standard streams if /dev/tty is
    # unavailable for any reason.
    try:
        tty_in = open("/dev/tty", "r")
    except OSError:
        tty_in = sys.stdin
    try:
        tty_out = open("/dev/tty", "w")
    except OSError:
        tty_out = sys.stdout

    try:
        lines = []
        tty_out.write("json> ")
        tty_out.flush()

        while True:
            line = tty_in.readline()
            if line == "":
                # EOF on the tty. Take whatever we have as final.
                break
            line = line.rstrip("\n")
            lines.append(line)
            accumulated = "\n".join(lines)

            # Blank line = "I'm done, parse what I have."
            if line == "" and accumulated.strip():
                break

            # Same tolerance as _load_instruction: strip one wrapping
            # fence, then ask whether the remainder is valid JSON.
            if _try_parse(accumulated):
                break

            # Bail after 200 lines — something is very wrong.
            if len(lines) > 200:
                tty_out.write(
                    f"\n[aborted: {len(lines)} lines without parseable JSON]\n"
                )
                tty_out.flush()
                return 1

        accumulated = "\n".join(lines)
        if not accumulated.strip():
            # Nothing typed, or only whitespace. Silent abort.
            return 1

        # Drain any pending input (trailing prose after valid JSON) so
        # it does not leak into the next shell command, using select
        # for the timeout.
        try:
            while select.select([tty_in], [], [], 0.05)[0]:
                if not tty_in.readline():
                    break
        except (OSError, ValueError):
            # select() can raise on non-selectable streams (e.g. the
            # stdin fallback on some platforms). The drain is a nicety,
            # not a correctness requirement — skip it quietly.
            pass

        tty_out.write("\n")
        tty_out.flush()

        cleaned = _strip_fences(accumulated).strip()
        try:
            instruction = json.loads(cleaned)
        except json.JSONDecodeError as e:
            sys.stderr.write(
                f"Instruction is not valid JSON: {e}\n"
                f"--- received ---\n{cleaned}\n--- end ---\n"
            )
            return 1

        _dispatch(instruction)
        return 0
    finally:
        if tty_in is not sys.stdin:
            tty_in.close()
        if tty_out is not sys.stdout:
            tty_out.close()


def _dispatch(instruction):
    """Validate a parsed instruction object and run the named tool.

    Exists so both the file/stdin path and the interactive prompt path
    share a single implementation of the checks, the trace line, and
    the success message.
    """
    if not isinstance(instruction, dict):
        raise SystemExit("Instruction JSON must be an object.")

    if "error" in instruction and "tool" not in instruction:
        raise SystemExit(f"[mcp] routing error: {instruction['error']}")

    tool = instruction.get("tool")
    params = instruction.get("params") or {}

    if not tool:
        raise SystemExit("Instruction JSON must contain a 'tool' key.")
    if not isinstance(params, dict):
        raise SystemExit("'params' must be a JSON object.")

    print(f"[mcp] tool={tool!r} params={params!r}", file=sys.stderr)
    run_tool(tool, params)
    print("[mcp] done.", file=sys.stderr)


def main():
    if len(sys.argv) < 2:
        # No arguments -> interactive prompt. The loop lives in-process.
        # See the SHELL INTEGRATION section of the module docstring for
        # the one-line alias.
        sys.exit(_interactive_mode())

    arg = sys.argv[1]

    if arg in ("-h", "--help"):
        print(__doc__)
        return

    if arg == "--self":
        print(Path(__file__).read_text(encoding="utf-8"))
        return

    instruction = _load_instruction(arg)
    _dispatch(instruction)


if __name__ == "__main__":
    main()
