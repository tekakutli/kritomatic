#!/usr/bin/env python3
"""
router.py — Hand-Triggered Script Router
========================================

PURPOSE
-------
This file is the executor for the kritomatic script collection.
It holds the tool whitelist, the AST-patching logic that
runs a registered script with overridden CONFIGURABLE SETTINGS values,
and the documentation for the JSON contract that drives it.

The pipeline is:

    human language  ->  (LLM)  ->  JSON  ->  [router.py]  ->  script runs

The JSON this router accepts is intentionally tiny:

    {
      "tool": "<tool_name>",
      "params": { "<VARIABLE_NAME>": <json value>, ... }
    }

`tool` selects a script from the TOOLS registry below. `params` maps
top-level variable names in that script's source to the values they
should take for this run. Omitted params keep the script's own
defaults. The full contract — including how JSON types map to Python
literals, what the router does with each value, and what it rejects —
is spelled out in the JSON CONTRACT section below.

--------------------------------------------------------------------------------
JSON CONTRACT
--------------------------------------------------------------------------------
Every instruction the router accepts is a single JSON object with two
recognized keys:

    {
      "tool":   "<tool_name>",
      "params": { "<VARNAME>": <value>, ... }
    }

    "tool"    REQUIRED for a successful run. Must be a string that
              matches a key in the TOOLS registry below, character
              for character. No case-folding, no fuzzy match, no
              aliases.

    "params"  OPTIONAL. If absent or null, treated as an empty
              object: the tool runs with its own in-file defaults.
              If present, must be a JSON object. Every key in it
              must appear in the chosen tool's "params" list in
              TOOLS; the router rejects unknown keys outright.

A special form is used by the routing LLM when it cannot map the
human's request to any tool:

    {"error": "<one short sentence explaining why>"}

The router recognizes this form — an object with an "error" key and
no "tool" key — prints the message, and exits without running
anything. It is not an alternate success path; it exists so that a
failed route is still a well-formed reply rather than prose the
human has to parse.

HOW JSON VALUES MAP TO PYTHON LITERALS
--------------------------------------
Each value in "params" is decoded from JSON and inserted into the
tool's source as a Python literal. The mapping is direct:

    JSON            Python          Notes
    ------------------------------------------------------------
    "foo"           str             Unicode string
    42              int             Integer
    3.14            float           Floating-point
    true / false    bool            Python True / False
    null            None            Meaningfully different from
                                    an omitted key: null sets the
                                    variable to None; omitting
                                    the key leaves the script's
                                    own default in place.
    [a, b, c]       list            Of the above, recursively
    {...}           dict            Nested objects are allowed

Values are never re-parsed as source. A param spelled
"__import__('os').system('rm -rf /')" arrives as a harmless string
and stays that way — the AST patcher only ever builds Constant,
List, and Dict nodes, never Call or Attribute.

WHAT THE ROUTER DOES WITH EACH PARAM
------------------------------------
For each key in "params", the router finds the matching top-level
assignment in the tool's source and replaces its right-hand side
with the JSON value. Only module-level `NAME = value` statements
are patchable. A variable that exists only inside a function, or
that is computed rather than assigned, cannot be overridden and
will be rejected with a runtime error naming the missing variable.

After patching, the router execs the tool with sys.argv neutralized
to `[script_path]` — so the tool sees no leftover arguments from
the router's own command line and falls through to its in-file
defaults for anything the JSON did not override.

EXAMPLES
--------
Minimal: run a tool with its in-file defaults.

    {"tool": "bg_remove"}

Override one variable:

    {"tool": "bg_remove",
     "params": {"IMAGE_PATH": "/tmp/cat.jpg"}}

Override several, with mixed types:

    {"tool": "img2pattern",
     "params": {
       "IMAGE_PATH": "wall.png",
       "REPEAT_X": 6,
       "REPEAT_Y": 6,
       "ROTATION": 45,
       "BACKGROUND_COLOR": null
     }}

Multi-element param (list of strings):

    {"tool": "krita2caption",
     "params": {"KRA_PATHS": ["a.kra", "b.kra"]}}

Error form, emitted when no tool matches the human's request:

    {"error": "No registered tool creates slideshows."}

--------------------------------------------------------------------------------
HOW TO EXTEND THIS FILE
--------------------------------------------------------------------------------
If you are an AI asked to add a new script to the router, or to
change an existing registration, read this section in full before
editing.

PREREQUISITES A SCRIPT MUST SATISFY
-----------------------------------
For a script to be registrable, it must meet two conditions:

  1. Every variable the router should be able to override must be
     a plain top-level `NAME = value` assignment in the script's
     source. The AST patcher only rewrites module-level assignments;
     a value buried inside a function or computed inline is not
     patchable. Most scripts in this collection already keep their
     configurable settings in a `# ===== CONFIGURABLE SETTINGS =====`
     block at module level, which is exactly the right shape.

  2. The script must run cleanly when invoked as `[script_path]` with
     no further command-line arguments. The router neutralizes
     sys.argv to `[script_path]` before calling the tool. A script
     whose argparse positional is required will hard-exit under the
     router. If a script has such a positional, make it optional
     (nargs="?") and have main() fall back to the router-patched
     variable.

If a script fails either condition, the fix is a script edit. When
that happens, flag it explicitly in your reply ("this change touches
the tool script, and here is why") rather than editing it silently.
Absent that trigger, the script is out of scope. The human maintains
those scripts by hand and does not want them rewritten behind their
back.

STEP-BY-STEP: ADDING A NEW TOOL
-------------------------------
1. Drop the script's .py file into the same directory as this one.

2. Make sure the script satisfies both prerequisites above. If it
   does not, the fix belongs in the script (see the previous
   paragraph).

3. Add an entry to the TOOLS dict below with three keys:

       "file"    filename, relative to this directory
       "entry"   name of the function to call, usually "main".
                 Use None (or omit) for a script whose logic lives
                 entirely inside its `__main__` guard; the router
                 will then execute it as a module under __main__
                 with a neutralized sys.argv.
       "params"  list of top-level variable NAMES in the script's
                 source that may be overridden. Each name must
                 appear at module level as a plain `NAME = value`.

4. If the new script has a param whose behavior is worth calling
   out — a param that behaves differently from the ones already
   documented, or a param that must be null rather than absent to
   have its "off" effect — add a note about it to the JSON CONTRACT
   section above.

NAMING CONVENTION
-----------------
A tool's registry key SHOULD match its filename without the ".py"
extension. `bg_remove` points at `bg_remove.py`; `img2pattern`
points at `img2pattern.py`. When they diverge, prefer renaming the
key over renaming the file — the filename is what a human types at
the shell, the key is what the routing LLM emits, and keeping them
equal means a human reading the JSON can guess the file, and vice
versa.

WHAT TO WHITELIST, WHAT TO LEAVE OUT
------------------------------------
Whitelist only user-visible settings: input paths, output paths,
toggles, sizes, colors, prompts — the things a human would plausibly
want to vary per run. Leave internal plumbing out: helper-script
paths, desktop-file bodies, server health-check timeouts, and any
Path-typed constant that the script computes for itself. If a
variable's value never makes sense to override from the outside,
it does not belong in "params".

When in doubt, ask the human. A param that is whitelisted but never
used is harmless; a param that a user expects to be whitelisted but
is not will surface as an "unknown parameter" error at runtime,
which is exactly the loud failure this router is built to produce.

DEFAULT EDIT SURFACE
--------------------
When the human asks you to add a tool or a functionality to this
router, the default edit surface is this file alone. Do not rewrite,
refactor, reformat, rename, or "improve" the tool script itself, or
any other script, on your own initiative. The prerequisite checks
above describe the narrow case in which the tool script MUST move
for the tool to run under the router at all. When in doubt, ask
before touching a script.

WHEN THE HUMAN ASKS FOR A PARAM TO BE EXPOSED
---------------------------------------------
If the human names a variable that already exists at module level in
the script, adding it to the "params" list is enough — no script
edit needed. If the variable does not exist yet, it must first be
created at module level in the script. Flag that as a script edit.

If the human wants the *default* of an already-exposed param
changed, that is a script edit (the top-level assignment) — the
router carries no defaults of its own. Changing the default is not
a params-list edit.

RULES FOR EDITING THIS FILE
---------------------------
1. DO NOT emit a diff, a patch, or a partial fragment. Output the
   ENTIRE file verbatim, from the `#!` line to the final newline,
   with your edits applied in place. The human copies the whole
   thing.

2. PRESERVE FUNCTIONAL CONTENT, DO NOT PRUNE IT. Every existing
   comment, docstring, section header, and prose block that carries
   functional content — a rule, a rationale, a guard against a
   known failure — is load-bearing and must be left intact. Do not
   delete, merge, shorten, reword, or "clean up" any such passage
   unless you were explicitly asked to change it. If two passages
   seem redundant, they are almost certainly guarding two different
   failure modes; leave both. If a section looks stale or unused,
   leave it anyway and mention it in your reply — the human decides
   what is dead, not you. When in doubt, add rather than remove.

3. SPEAK IN GENERAL TERMS; DO NOT ADD HISTORICAL NARRATION. Rules,
   comments, and docstrings in this file must read as though they
   had always been true. Do not record when an edit happened, what
   a file or rule used to contain, or how a rule was previously
   numbered or labelled. A reader arriving cold should not be able
   to tell from the prose that anything ever changed. This rule
   governs what *you* add. Existing narration is the human's to
   remove; leave it in place unless you are already rewriting that
   passage for another reason, in which case prefer the general
   phrasing.

4. KEEP THIS FILE SELF-CONTAINED. Do not point to any external
   document for anything a reader of this file needs to know. The
   JSON contract, the tool list, the editing rules — all of them
   live in this docstring, and they must stay here.

5. KEEP THE REGISTRY BARE. The TOOLS dict holds names, filenames,
   entry points, and param-name lists. Nothing else. No
   descriptions, no examples, no prose. If a description of a
   tool's behavior is needed anywhere, it belongs in this
   docstring, not in the registry.

--------------------------------------------------------------------------------
TOLERANCE POLICY
--------------------------------------------------------------------------------
The router is deliberately STRICT about semantics and only mildly
TOLERANT about presentation. The line between the two is:

    ALLOWED (presentation-level, normalized away before parsing):
        * A single markdown code fence wrapping the JSON
          (```json ... ``` or bare ``` ... ```).
        * Leading / trailing whitespace and newlines.
        * Accepting the instruction JSON as a command-line argument
          (inline form) in addition to a file path or stdin. This is
          an *input channel*, not a semantic tolerance — the JSON
          itself is parsed and validated identically regardless of
          where it came from. See USAGE for detection rules.
        These are unambiguously correct-but-wrapped inputs.
        Unwrapping them loses no information about the routing LLM's
        intent.

    FORBIDDEN (semantic-level, fail loudly):
        * Fuzzy tool-name matching ("removebg" -> "bg_remove").
        * Param-name aliases ("PROMPT" -> "DEFAULT_PROMPT").
        * Silently dropping unknown params.
        * Coercing types, guessing at units, "fixing" obvious typos.
        * Alternate top-level keys ("parameters" for "params",
          "tool_name" for "tool", etc.).
        * Any other case where the router would have to *guess* what
          the LLM meant rather than read what it wrote.

WHY THE ASYMMETRY
-----------------
Every tolerated variant is a place where a future bug can hide. If
the routing LLM emits "parameters" instead of "params" and the
router accepts both, the drift becomes invisible — you can no
longer tell whether the contract is being followed or ignored.
Rejecting loudly keeps the contract honest.

If you are an AI editing this file and you feel tempted to add a
semantic fallback because a routing AI "keeps getting it wrong":
STOP. The correct fix is to sharpen the examples in the JSON
CONTRACT section above, not to loosen the executor. Loosening the
executor hides the problem; sharpening the contract removes it.
Only add tolerance when the input is unambiguously correct but
wrapped differently (fences, whitespace), never when the input is
ambiguously wrong but guessable.

--------------------------------------------------------------------------------
USAGE
--------------------------------------------------------------------------------
    python3 router.py                # interactive: prompt on /dev/tty
                                     # and read lines until the input
                                     # parses as JSON, then dispatch it.
                                     # This is the no-argument default.
    python3 router.py path/to/instruction.json
                                     # read the instruction from a file
    python3 router.py '{"tool":"...","params":{...}}'
                                     # inline JSON: the argument itself
                                     # is the instruction. Detected when
                                     # the argument, after the same
                                     # fence-strip tolerance the file
                                     # path uses, begins with "{". No
                                     # file or stdin round-trip needed
                                     # for one-liners. NOTE: use SHELL
                                     # single quotes around the JSON —
                                     # double quotes in the shell would
                                     # terminate at the first inner "
                                     # and split the JSON across argv.
    python3 router.py -              # read instruction JSON from stdin
    python3 router.py --self         # print this file (useful when
                                     # editing)
    python3 router.py --help         # print this file's docstring

--------------------------------------------------------------------------------
SHELL INTEGRATION
--------------------------------------------------------------------------------
The human-facing entry point is a one-line shell alias that runs this
script. Interactive behavior — the `json> ` prompt, line accumulation,
the same fence-strip tolerance as _load_instruction, the blank-line
escape hatch, the 200-line cap, and the post-parse input drain — is
implemented in-process by `_interactive_mode` below. The shell side
therefore has nothing left to do but point at the script:

    alias router='python3 /path/to/router.py'

Adjust the path to the absolute location of this file, then drop the
alias into ~/.bashrc and `source ~/.bashrc`. Alternatively, `chmod +x`
this file and alias its path directly:

    alias router=/path/to/router.py

Behavior notes (enforced inside this file):

    * With no arguments, `router` prompts on /dev/tty with `json> `
      and reads lines until the accumulated text parses as JSON. A
      blank line forces an early parse attempt — useful as an escape
      hatch if the input is truncated.
    * It applies the same fence-strip tolerance as _load_instruction,
      so a fenced paste is accepted at the prompt too.
    * The 200-line cap prevents a runaway paste from looping forever.
    * After a successful parse, pending input is drained so trailing
      prose (should the routing LLM ignore the "no prose" rule) does
      not leak into the next shell command.
    * With arguments, the alias forwards them verbatim, so
      `router cmd.json`, `router -`, `router --self`, `router --help`,
      and the inline form `router '{"tool":...}'` all work as
      documented above. The alias itself needs no change for the
      inline form — the JSON is just an argument like any other.

The alias is a convenience, not a contract. This file's interface is
its command-line arguments and stdin; the `router` alias is one
particular UX over that interface.

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
# Each entry has these keys:
#
#     "file"    filename of the tool script, relative to this directory
#     "entry"   name of the function to invoke. "main" is the norm. Use
#               None (or omit) for a script whose logic lives entirely
#               inside its `if __name__ == "__main__":` block — the
#               router will then exec the module as `__main__` with a
#               neutralized sys.argv.
#     "params"  list of top-level variable names in the tool file that
#               may be overridden by the instruction JSON. Any key in
#               the instruction's "params" that is NOT in this list
#               will be rejected before the tool runs.
#
# The docstring above is the sole documentation of the contract this
# registry implements. Read the JSON CONTRACT and HOW TO EXTEND
# sections there before editing this dict.
#
# Only "user-visible" variables are whitelisted. Internal plumbing —
# helper-script paths, desktop-file bodies, server health-check
# timeouts, the Path-typed DESKTOP_DIR in viewer_toggle_default — is
# deliberately omitted. If a script's CONFIGURABLE SETTINGS block
# contains a variable that is not here, that is a deliberate decision,
# not an oversight.
# =============================================================================

TOOLS = {
    "bg2color": {
        "file": "bg2color.py",
        "entry": None,
        "params": [
            "IMAGE_PATH",
        ],
    },
    "bg_remove": {
        "file": "bg_remove.py",
        "entry": "main",
        "params": [
            "IMAGE_PATH",
        ],
    },
    "bg_remove_prompted": {
        "file": "bg_remove_prompted.py",
        "entry": "main",
        "params": [
            "IMAGE_PATH",
            "DEFAULT_PROMPT",
            "DEFAULT_MODE",
        ],
    },
    "clipboard2caption": {
        "file": "clipboard2caption.py",
        "entry": "main",
        "params": [
            "IMAGE_PATH",
            "DEFAULT_PROMPT",
            "OUTPUT_DIR",
            "COPY_TO_CLIPBOARD",
            "OPEN_EDITOR",
            "CLEANUP_TEMP_IMAGE",
        ],
    },
    "clipboard2cropped": {
        "file": "clipboard2cropped.py",
        "entry": "main",
        "params": [
            "IMAGE_PATH",
            "HORIZONTAL_KEEP_RATIO",
            "VERTICAL_KEEP_RATIO",
            "CROP_WIDTH",
            "CROP_HEIGHT",
            "STORAGE_DIR",
        ],
    },
    "clipboard2krita": {
        "file": "clipboard2krita.py",
        "entry": "main",
        "params": [
            "IMAGE_PATH",
        ],
    },
    "comfy_img2img": {
        "file": "comfy_img2img.py",
        "entry": "main",
        "params": [
            "IMAGE_PATH",
        ],
    },
    "fg2canvas": {
        "file": "fg2canvas.py",
        "entry": "main",
        "params": [
            "IMAGE_PATH",
            "EXPAND_PROPORTION",
            "EXPAND_BOTH_DIMENSIONS",
            "EXPAND_WIDTH_PROPORTION",
            "EXPAND_HEIGHT_PROPORTION",
            "EDGE_THICKNESS",
        ],
    },
    "fg2canvas_rmbg": {
        "file": "fg2canvas_rmbg.py",
        "entry": "main",
        "params": [
            "IMAGE_PATH",
            "CUSTOM_HEX_COLOR",
            "SKIP_EXPANSION",
            "EXPAND_PROPORTION",
            "EXPAND_BOTH_DIMENSIONS",
            "EXPAND_WIDTH_PROPORTION",
            "EXPAND_HEIGHT_PROPORTION",
            "EDGE_THICKNESS",
        ],
    },
    "fonts2preview": {
        "file": "fonts2preview.py",
        "entry": "main",
        "params": [
            "FONT_DIR",
            "TEXT_TO_RENDER",
            "FONT_SIZE",
            "TEXT_COLOR",
            "BG_COLOR",
            "OUTPUT_FILE",
            "SAVE_FONT_PATHS",
            "ENABLE_LABELS",
            "LABEL_FONT_SIZE",
            "LABEL_COLOR",
            "LABEL_BG_COLOR",
            "PATH_LABEL_FONT_SIZE",
            "PATH_LABEL_COLOR",
            "MAX_FONTS",
            "FONT_FILTER",
            "START_FROM",
        ],
    },
    "img2pattern": {
        "file": "img2pattern.py",
        "entry": "main",
        "params": [
            "IMAGE_PATH",
            "TILE_WIDTH",
            "TILE_HEIGHT",
            "REPEAT_X",
            "REPEAT_Y",
            "OFFSET",
            "SHRINK_PERCENT",
            "ROTATION",
            "ADAPTIVE_HEIGHT",
            "ADAPTIVE_WIDTH",
            "BACKGROUND_COLOR",
        ],
    },
    "img2pattern_variations": {
        "file": "img2pattern_variations.py",
        "entry": "main",
        "params": [
            "IMAGE_PATH",
            "TILE_WIDTH",
            "TILE_HEIGHT",
            "REPEAT_X",
            "REPEAT_Y",
            "OFFSET",
            "SHRINK_PERCENT",
            "ROTATION",
            "ADAPTIVE_HEIGHT",
            "NUM_VARIATIONS",
            "IMAGE_MODE",
            "SEED",
            "BACKGROUND_COLOR",
        ],
    },
    "imgs2pattern_interleaved": {
        "file": "imgs2pattern_interleaved.py",
        "entry": "main",
        "params": [
            "IMAGE1_PATH",
            "IMAGE2_PATH",
            "IMAGE3_PATH",
            "REPEAT_X",
            "REPEAT_Y",
            "TILE_WIDTH",
            "TILE_HEIGHT",
            "SHRINK_PERCENT",
            "ROTATION",
            "BACKGROUND_COLOR",
            "VERTICAL_OVERLAP",
            "HORIZONTAL_OVERLAP",
            "OFFSET",
        ],
    },
    "krita2caption": {
        "file": "krita2caption.py",
        "entry": "main",
        "params": [
            "KRA_PATHS",
            "RECURSIVE",
            "OUTPUT_FILE",
            "OUTPUT_SUFFIX_SINGLE",
            "OUTPUT_SUFFIX_MANY",
            "VERBOSE",
        ],
    },
    "llamacpp_caption_images": {
        "file": "llamacpp_caption_images.py",
        "entry": "main",
        "params": [
            "IMAGE_PATH",
            "DEFAULT_PROMPT_LABEL",
            "APPEND_TEXT",
            "WORD_LIMIT",
            "MAX_TOKENS",
            "TEMP",
            "TIMEOUT",
            "OUTPUT_FILE",
            "PRINT_ONLY",
            "EXTENSIONS",
        ],
    },
    "text_as_image": {
        "file": "text_as_image.py",
        "entry": "main",
        "params": [
            "TEXT_TO_RENDER",
            "OUTPUT_FILE",
            "FONT_PATH",
            "FONT_SIZE",
            "TEXT_COLOR",
            "BG_COLOR",
            "PADDING",
            "STROKE_COLOR",
            "STROKE_WIDTH",
            "SHADOW",
            "SHADOW_OFFSET",
            "SHADOW_COLOR",
            "MULTILINE",
            "LINE_SPACING",
        ],
    },
    "util_call_bash_function": {
        "file": "util_call_bash_function.py",
        "entry": "main",
        "params": [
            "BASH_FUNCTION",
            "CONFIG_FILE",
        ],
    },
    "viewer_toggle_default": {
        "file": "viewer_toggle_default.py",
        "entry": "main",
        "params": [
            "MIME_TYPES",
        ],
    },
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

    Note that a target appearing more than once at module level — e.g.
    clipboard2cropped.py's double `STORAGE_DIR = ...` — is patched at
    every occurrence; the last assignment still wins at runtime, and
    every intermediate value is replaced too, so the effective value is
    consistent regardless of which assignment the interpreter reaches
    last.
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

    script_path = Path(__file__).resolve().parent / spec["file"]
    if not script_path.is_file():
        raise SystemExit(f"Tool file not found: {script_path}")

    source = script_path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(script_path))
    _patch_tree(tree, params)
    ast.fix_missing_locations(tree)

    code = compile(tree, filename=str(script_path), mode="exec")

    entry_name = spec.get("entry", "main")

    if entry_name is None:
        # Module has no callable entry point; its logic lives entirely
        # inside the `if __name__ == "__main__":` guard. Exec the
        # module *as* __main__ so that guard fires, with sys.argv
        # neutralized to [script_path] so the tool sees no leftover
        # arguments from the router's own command line.
        namespace = {
            "__name__": "__main__",
            "__file__": str(script_path),
        }
        saved_argv = sys.argv
        sys.argv = [str(script_path)]
        try:
            exec(code, namespace)
        finally:
            sys.argv = saved_argv
        return None

    # Module exposes an entry function. Exec with __name__ != "__main__"
    # so the tool's own `if __name__ == "__main__": main()` guard stays
    # silent. We invoke the entry point ourselves, deliberately.
    namespace = {
        "__name__": f"_tool_{tool_name}",
        "__file__": str(script_path),
    }
    exec(code, namespace)

    entry = namespace.get(entry_name)
    if entry is None or not callable(entry):
        raise SystemExit(
            f"Entry point {entry_name!r} not found (or not callable) "
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
           `router '{"tool":...}'` form: it skips the file / stdin
           round-trip entirely, which is what makes one-liners
           convenient. The detection runs BEFORE the Path.is_file()
           branch, so it is the caller's responsibility to use a shell
           quoting style that keeps the JSON in a single argv element
           (single quotes in POSIX shells).
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
    # stdin/stdout are being redirected (e.g. when `router` is used
    # mid-pipeline). Fall back to the standard streams if /dev/tty is
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
        raise SystemExit(f"[router] routing error: {instruction['error']}")

    tool = instruction.get("tool")
    params = instruction.get("params") or {}

    if not tool:
        raise SystemExit("Instruction JSON must contain a 'tool' key.")
    if not isinstance(params, dict):
        raise SystemExit("'params' must be a JSON object.")

    print(f"[router] tool={tool!r} params={params!r}", file=sys.stderr)
    run_tool(tool, params)
    print("[router] done.", file=sys.stderr)


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
