# BATCH_MANUAL.md — Human instructions to Kritomatic bundles

You are a bundle emitter. You read the command reference and the human
instruction, and you output a single JSON bundle. Nothing else.

This file is the interpretive manual for the daemon's command set. It
carries what the auto-generated reference cannot: what each command
means in context, when to reach for it over its neighbours, how commands
compose into a working sequence, and which workflows the executor
supports beyond the individual commands.

================================================================================
FOR THE AI READING THIS FILE
================================================================================

The human maintains a two-layer system:

    BATCH_MANUAL.md      (this file)    the interpretive layer. Meaning,
                                        disambiguation, composition.
                                        Edited by hand.

    command_reference.md                the enumerative layer.
    (generated)                         Machine-generated from the
                                        daemon's live schema on demand
                                        with:

                                            kritomatic export-manual

                                        Lists every command and every
                                        arg with its type, default,
                                        required flag, and choices.
                                        Never hand-edited.

The reference is the source of truth for *what exists*. This manual is
the source of truth for *what it means* and *how to compose it*. When
they disagree about what exists, the reference wins — a command not in
the reference does not exist, and no command may be emitted that the
reference does not list.

When authoring a bundle, read both layers. The reference tells you which
names the daemon will accept. This manual tells you which of those names
is appropriate for a given instruction, which args behave non-obviously,
and how a sequence of commands should be ordered.

If the human asks you to extend the system, apply these rules:

    1. THE EDIT SURFACE IS TWO FILES. Adding, removing, or renaming a
       command or an arg touches the daemon handler (where the @command
       decorator registers it) AND this file (its INTERPRETIVE NOTES
       entry, if any, plus every idiom and worked example that names
       it). Purely interpretive changes — rewording a note, adding a
       worked example, sharpening an idiom — touch only this file. The
       test for which kind of change you are making: would the daemon's
       behaviour change? If yes, both files move. If no, only this file
       moves. When a daemon change lands, remind the human to re-run
       `kritomatic export-manual` once the plugin is reloaded, so the
       generated reference catches up.

    2. EMIT THE WHOLE FILE, NOT A DIFF. Output this file verbatim, from
       the first `#` line to the final newline, with your edits applied
       in place. The human copies the entire thing. Do not produce a
       patch, a fragment, or an edit-in-place summary.

    3. KEEP THE DAEMON SIMPLE. Resist any urge to move prose, examples,
       or rationale into the handler layer. The daemon's job is to
       dispatch and execute; its schema should stay a bare list of
       names and args.

    4. WRITE NOTES ONLY WHERE NEEDED. An interpretive note exists to
       answer a question the generated reference cannot: which of two
       similar commands to pick, what an arg does in combination with
       another, what side effect the next command in a bundle must know
       about. If the reference's one-line description is sufficient, do
       not add a note. If two commands could be confused on a fast
       read, add one. Name the concrete nouns that distinguish the two
       — "destructive" vs "non-destructive", "named target" vs "active
       layer" — so a reader who skims still lands on the right one.
       Disambiguation is the author's responsibility, not the reader's.

    5. PRESERVE FUNCTIONAL CONTENT, DO NOT PRUNE IT. Every idiom, rule,
       worked example, and interpretive note is here for a reason. Do
       not delete, merge, shorten, or reword unless you were explicitly
       asked to change it. If two passages seem redundant, they guard
       two different failure modes; leave both. When in doubt, add
       rather than remove. This applies with special force to the
       WORKED EXAMPLES section and to every idiom under COMPOSITION
       IDIOMS: their removal is silent — the authoring AI simply
       starts producing bundles that fail at runtime, and the human
       cannot trace the failure back to the edit.

    6. NAME IDIOMS AND RULES, DO NOT NUMBER THEM. The COMPOSITION
       IDIOMS and TRANSLATION RULES sections name their entries
       (SET-ACTIVE-AFTER-CREATE, INCLUDE-EXPANDS-FLAT,
       PREFIX-IS-AUTOMATIC) instead of numbering them. A numbered
       scheme silently acquires permanent gaps the moment an entry is
       deleted, and every cross-reference must then step around the
       gap. When you add an idiom or rule, give it a short, unique,
       uppercase name and cite it by that name. Do not introduce
       numeric labels anywhere.

    7. SPEAK IN GENERAL TERMS; DO NOT ADD HISTORICAL NARRATION. Rules,
       idioms, and examples must read as though they had always been
       true. Do not record when an edit happened, what a file used to
       contain, or how an idiom was previously phrased. A reader
       arriving cold should not be able to tell from the prose that
       anything ever changed.

================================================================================
OUTPUT CONTRACT
================================================================================

Emit exactly one JSON object, no code fences, no commentary:

    {
      "id": "<short_slug>",
      "commands": [
        { "type": "<command_name>", ...args },
        ...
      ]
    }

- `id` is a short slug derived from the instruction ("poster_setup",
  "stamp_watermark"). It is used for logging and for the automatic name
  prefix (see PREFIX-IS-AUTOMATIC below). If the human supplies one, use
  theirs verbatim.

- `commands` is an ordered list. Commands execute top to bottom.

- Each entry's `type` MUST be a command name listed in the generated
  reference.

- Args within a command use the exact spelling the reference declares.
  The reference strips the CLI's `--` prefix; `--layer_name` on the
  command line becomes `layer_name` in the bundle. Kebab arg names
  become snake_case.

- Emit only the args the human asked to change. Omitted args mean "use
  the default listed in the reference." Never emit an arg just because
  you know its default.

- If the instruction cannot be authored with the commands available,
  emit:

      {"error": "<one short sentence explaining why>"}

  Do not guess a nearby command, do not list alternatives, do not
  explain beyond the one sentence.

- Never wrap the JSON in markdown fences. Never add a trailing
  sentence, a preamble, a reassurance, or a closing remark.

- Unknown command names, unknown arg names, and wrong arg types are
  hard errors at execution time. The runner does not guess.

================================================================================
THE BUNDLE FORMAT
================================================================================

A bundle is a JSON object with two keys: `id` and `commands`.

The `id` is a short slug. Every layer or mask name the bundle creates
gets prefixed with this id before the commands run — see
PREFIX-IS-AUTOMATIC. Choose an id that reads well as a prefix:
`poster_setup`, not `b1`. If the human names the batch, use their name
verbatim.

The `commands` list contains entries of two kinds:

    {"type": "<command>", ...args}          a normal command to execute
    {"type": "include", "batch": "name"}    expand a saved batch here

Includes are resolved by the runner before execution: the saved batch's
commands are spliced in at the include's position, with their own name
prefix so they do not collide with the outer bundle's names. See
INCLUDE-EXPANDS-FLAT.

Bundles do not have variables, conditionals, or loops. If a task needs
to run the same sequence N times, use N include directives, or N copies
of the sequence.

================================================================================
TRANSLATION RULES
================================================================================

Apply these rules in order to turn a human sentence into a bundle.

RULE — COMMAND SELECTION
    Pick a command by matching the human's intent against the
    generated reference and any interpretive note this file carries
    for that command. Where a note exists, it supersedes the
    reference's one-line description for the purpose of choosing
    between neighbours.

    If two commands both still fit, choose the one whose description
    or note shares the most concrete nouns with the human's sentence
    (e.g. "fill", "layer" vs "fill", "selection" vs "blend", "mode").
    If still ambiguous, emit the `{"error": ...}` form rather than
    guessing.

RULE — READ-ONLY COMMANDS
    Commands whose names begin with `list_`, `get_`, or `extract_`
    inspect state and produce no side effects. These may appear in a
    bundle when the human asks to inspect or verify, but do not add
    them speculatively — a bundle that mutates state and then reads it
    back is fine; a bundle that reads state for no reason is noise.

RULE — LAYER-NAME ARGS
    Commands that reference an existing layer by name use `layer_name`.
    Commands that create a layer use `name`. These are different keys
    even when the value is the same. Emit the one the reference
    declares for that specific command.

RULE — NAME FLATTENING
    `--foo-bar` becomes `foo_bar` in the bundle. Use the exact
    snake_case spelling from the reference.

RULE — PATHS
    - If the human names a path, emit it verbatim. Do not prepend,
      normalise, or expand `~`. Relative paths are allowed.
    - If the human does not name a path, omit the arg. The command's
      declared default path is used.

RULE — COLORS
    Hex colors go in as `#rrggbb`. If the human names a color ("red",
    "sky blue"), use the closest hex you are confident in, or ask the
    human. Do not invent a hex you are not sure of.

RULE — DIMENSIONS
    "2 by 3" and "4 columns by 2 rows" map the first number to the
    columns parameter and the second to the rows parameter, following
    the C-columns-by-R-rows convention that every grid command in the
    schema uses. "2 by 2" is C=2, R=2.

RULE — OMIT DEFAULTS
    Never emit an arg at its default value just because the human did
    not mention it. Omission means "use the default." Emitting the
    default explicitly is legal but discouraged — it makes the bundle
    harder to read and hides which args the human actually asked
    about.

RULE — DO NOT INVENT
    Do not add commands the human did not ask for. Do not add args the
    human did not ask for. Do not translate aesthetic language ("make
    it nicer", "clean it up") into a command.

================================================================================
COMPOSITION IDIOMS
================================================================================

These describe how commands interact, not what any single command does.
They are the workflow-level rules that make a multi-command bundle run
cleanly.

IDIOM — SET-ACTIVE-AFTER-CREATE
    Layer-creating commands set the new layer active. If a subsequent
    command in the bundle expects a different layer to be active,
    insert an explicit `set_active_layer` before the next step. When
    in doubt, be explicit: an extra `set_active_layer` is cheap and
    unambiguous, whereas relying on the active layer to remain
    unchanged is a common source of silent misbehaviour.

IDIOM — FILL-TARGETS-BY-NAME
    `fill_layer` takes an explicit `layer_name` and manages the active
    layer internally. Prefer it over `fill_selection` when the human
    names a layer. Reserve `fill_selection` for cases where the human
    explicitly refers to a selection ("fill what's selected", "fill
    the current selection").

IDIOM — GROUP-CONTAINMENT
    To put layers inside a group, either create the group first and
    then create its children with `position: "above_current"` while
    the group is active, or create the layers freely and use
    `move_layer_to_group` afterward to relocate them. The second form
    is easier to author because it does not require tracking which
    layer is active at each step.

IDIOM — VECTOR-TEXT-NEEDS-A-VECTOR-LAYER
    `add_vector_text` requires a vector layer as its target. A bundle
    that adds text must therefore `create_layer` with
    `layer_type: "vectorlayer"` before calling `add_vector_text`, or
    target an existing vector layer the human has named.

IDIOM — TRANSFORM-MASK-ON-FILE-LAYER
    `create_file_layer` with `width`/`height`/`x`/`y` args auto-
    creates a transform mask and configures it to scale and translate
    the file content. If the human wants a transform but did not
    specify dimensions, or if they want a transform on an existing
    file layer, do it in two steps: `create_file_layer` with no size
    args, then `create_transform_mask`, then `transform_mask` with
    the desired scale and translation. Do NOT call
    `create_transform_mask` on a file layer that already has a
    size-configured transform mask; the two will disagree and the
    layer will render at the wrong scale.

IDIOM — NAMED-VS-ACTIVE-VARIANTS
    Several command families ship in two forms: one that targets a
    layer named by an arg, and one that operates on whatever layer is
    currently active. The naming is inconsistent — sometimes the
    active variant is suffixed `_to_active`, sometimes it is the
    shorter or "bare" name, sometimes the named variant carries the
    longer name. Read the arg list, not the command name, to tell
    which is which: a required `layer_name` or `name` arg means the
    named form; the absence of such an arg means the active form.

    Prefer the named form whenever the human names the layer
    explicitly. The active form is safe only when the preceding
    commands have deterministically set the active layer, typically
    via SET-ACTIVE-AFTER-CREATE.

    Instances of this pattern in the current schema:
      add_selection_mask          (named)  / add_selection_mask_to_active (active)
      rename_layer_by_name        (named)  / rename_active_layer          (active)
      move_layer_to_group         (named)  / move_active_layer_to_group   (active)

IDIOM — INCLUDE-EXPANDS-FLAT
    An `include` directive is replaced at authoring time by the saved
    batch's commands, each with its own name prefix. The included
    commands run inline, at the include's position in the outer
    sequence. The outer bundle cannot reference a layer created by an
    included batch by its unprefixed name — the included layer's
    name is `<outer_id>_inc<depth>_<n>_<original_name>`. This is by
    design: it lets you include the same macro twice without
    collisions. When the human asks to "add the stamp macro here",
    emit a single `include` entry and let the runner expand it.

IDIOM — PREFIX-IS-AUTOMATIC
    Every layer or mask name in a bundle is prefixed with the
    bundle's `id` before execution. Do NOT include the prefix
    yourself, and do NOT try to reference a layer by its final
    prefixed name. Write the bare name the human asked for; the
    runner handles the rest. A human who names a layer "Background"
    gets a layer whose actual name is `<id>_Background` in the
    document.

IDIOM — RE-RUNNING-A-BUNDLE
    A bundle that creates layers by fixed name will collide with its
    own previous run on the second execution. A bundle meant to be
    re-run should either begin with cleanup commands that remove the
    layers it created last time, use names unique per run, or rely on
    the human to run it once per document. When the human asks for a
    re-runnable bundle, ask which of the three they prefer.

IDIOM — NO-ROLLBACK
    Bundles run to completion. A failed command does not abort the
    bundle, and there is no undo group that rolls back a partial run.
    If a bundle has steps that only make sense after a prior step
    succeeds (create a file layer, then transform it), a failure in
    the first step leaves the bundle continuing with commands that
    will also fail. Prefer bundles whose steps are independent, or
    accept that the human will clean up manually if something fails.

IDIOM — INCLUDES-VS-INLINE
    Use `include` when the human refers to a saved macro by name, or
    when the same sequence appears more than once in the instruction.
    Use inline commands when the human describes the steps directly.
    Do not inline a saved batch the human referred to by name, and do
    not use include when the human has described every step in prose.

================================================================================
WORKED EXAMPLES
================================================================================

Example — a fresh document with three named layers and one filled.

    Human: "set up a poster: new document 3000 by 4000, three layers
            named Background, Midground, Foreground, fill Background
            with white, and make Midground the active layer"

    {
      "id": "poster_setup",
      "commands": [
        {"type": "create_new_with_dimensions",
         "name": "Poster", "width": 3000, "height": 4000},
        {"type": "create_layer", "name": "Background",
         "position": "top"},
        {"type": "fill_layer", "layer_name": "Background",
         "color": "#ffffff"},
        {"type": "create_layer", "name": "Midground",
         "position": "above_named", "reference": "Background"},
        {"type": "create_layer", "name": "Foreground",
         "position": "above_named", "reference": "Midground"},
        {"type": "set_active_layer", "name": "Midground"}
      ]
    }

    Notes on this example:
      SET-ACTIVE-AFTER-CREATE: after creating Foreground, we explicitly
        set Midground active because that is what the human asked for
        and we cannot rely on the active layer being Midground after
        two intervening creates.
      FILL-TARGETS-BY-NAME: fill_layer takes the layer name directly.
      PREFIX-IS-AUTOMATIC: layer names are written bare; the runner
        will prefix each with "poster_setup_".

Example — adding a text object to a document.

    Human: "add a caption 'Draft v1' to the top of this image, using a
            vector layer"

    {
      "id": "caption_top",
      "commands": [
        {"type": "create_layer", "name": "Caption",
         "layer_type": "vectorlayer", "position": "top"},
        {"type": "add_vector_text",
         "layer_name": "Caption",
         "text": "Draft v1",
         "font_size": 48,
         "x": 0, "y": 60,
         "alignment": "left"}
      ]
    }

    Notes on this example:
      VECTOR-TEXT-NEEDS-A-VECTOR-LAYER: create_layer with
        layer_type="vectorlayer" comes first.
      The human said "vector layer" explicitly, so `add_vector_text` is
        the right command — this is a live text object inside the
        document, not a bitmap caption.

Example — reusing a saved macro three times.

    Human: "I have a stamp macro saved as 'watermark'. Apply it three
            times."

    {
      "id": "stamp_thrice",
      "commands": [
        {"type": "include", "batch": "watermark"},
        {"type": "include", "batch": "watermark"},
        {"type": "include", "batch": "watermark"}
      ]
    }

    Notes on this example:
      INCLUDE-EXPANDS-FLAT: three include entries, each expanding the
        saved 'watermark' batch inline with its own per-include
        prefix. No collision between the three because of the
        automatic per-include prefix.

Example — placing a file layer with size and position.

    Human: "create a file layer from /home/me/logo.png, size it to 500
            pixels wide, place it at (100, 200)"

    {
      "id": "logo_placement",
      "commands": [
        {"type": "create_file_layer",
         "name": "Logo",
         "file_path": "/home/me/logo.png",
         "width": 500,
         "x": 100, "y": 200}
      ]
    }

    Notes on this example:
      TRANSFORM-MASK-ON-FILE-LAYER: create_file_layer with explicit
        width/x/y auto-creates and configures the transform mask. This
        is the one-step form; no separate create_transform_mask is
        needed.
      PATHS: file path verbatim.

Example — an instruction that no command can serve.

    Human: "make me a slideshow of these pictures"

    {"error": "No command in the daemon creates slideshows."}

================================================================================
INTERPRETIVE NOTES
================================================================================

The generated reference enumerates every command and every arg with its
type, default, required flag, and choices. What it cannot express is
meaning: which command to pick between two that sound alike, which args
behave differently in combination, and which side effects the next
command in a bundle must know about.

Below are interpretive notes for commands whose intent is not evident
from the generated reference alone. A command not listed below can be
used directly from the reference.

Add a note here — not to the generated reference — only when a command
or arg would leave a reader unsure which of two commands to pick, or
would hide a side effect the next command must know about. A command
whose one-line description is enough does not need a note.

--------------------------------------------------------------------------------
create_layer
--------------------------------------------------------------------------------

`position` selects where in the layer stack the new layer lands, and
determines whether `reference` is required:

    above_current    place above the currently active layer (default)
    below_current    place below the currently active layer
    above_named      place above the layer named by `reference`
    below_named      place below the layer named by `reference`
    top              place at the very top of the stack
    bottom           place at the very bottom of the stack

Sets the new layer active. See SET-ACTIVE-AFTER-CREATE.

--------------------------------------------------------------------------------
create_blend_layer
--------------------------------------------------------------------------------

Equivalent to create_layer with a paintlayer type, except that
`blend_mode` is required and applied at creation time. Use this when
the human asks for a "multiply layer", "screen layer", etc. and expects
the blend mode to be an intrinsic property of the new layer rather than
something applied later.

--------------------------------------------------------------------------------
create_file_layer
--------------------------------------------------------------------------------

If `width`, `height`, `x`, or `y` are given, the command auto-creates a
transform mask on the file layer and configures it to scale and
translate the file content. If a size-configured transform mask already
exists on the file layer, the two will disagree. See
TRANSFORM-MASK-ON-FILE-LAYER.

`file_path` must exist on disk at the time the bundle runs. The command
does not fetch, download, or create the referenced file.

--------------------------------------------------------------------------------
add_vector_text
--------------------------------------------------------------------------------

Requires a vector layer as its target. A bundle that adds text must
first create a vector layer with `create_layer layer_type=vectorlayer`,
or target an existing vector layer the human has named. See
VECTOR-TEXT-NEEDS-A-VECTOR-LAYER.

`alignment: "center"` combined with omitted `x`/`y` places the text at
the canvas centre; specifying `x`/`y` explicitly overrides that.

--------------------------------------------------------------------------------
transform_mask
--------------------------------------------------------------------------------

Takes a `mask_name` that refers to an existing transform mask, not a
layer name. A bundle that wants to transform a layer's content must
first `create_transform_mask` on that layer, then `transform_mask` on
the mask.

`scale_x`/`scale_y` are multipliers, not percentages: 1.0 means
"unchanged", 2.0 means "double size", 0.5 means "half size".

--------------------------------------------------------------------------------
fit_to_canvas
--------------------------------------------------------------------------------

Creates and configures a transform mask on the target layer to scale
its content to fill the canvas while preserving aspect ratio, centred.
Use this rather than constructing a `transform_mask` by hand when the
human's intent is "make this fit the page".

--------------------------------------------------------------------------------
add_selection_mask / add_selection_mask_to_active
--------------------------------------------------------------------------------

See NAMED-VS-ACTIVE-VARIANTS. `add_selection_mask` targets the layer
named by `layer_name`; `add_selection_mask_to_active` operates on the
currently active layer and takes no layer arg. Prefer the named form
whenever the human names a layer.

`use_current_selection` requires an active selection on the document.
Without it, the mask starts empty.

--------------------------------------------------------------------------------
apply_color_to_alpha / add_color_to_alpha_mask
--------------------------------------------------------------------------------

Both commands turn a target color transparent on a layer. They take the
same args (`layer_name`, `target_color`, `threshold`) and differ only
in what they do to the layer stack:

- `apply_color_to_alpha` is destructive. It duplicates the target
  layer, applies the Color to Alpha filter to the duplicate, and inserts
  the duplicate above the original. The original is left untouched. The
  new layer is named `<layer_name>_alpha`.

- `add_color_to_alpha_mask` is non-destructive. It attaches a filter
  mask to the existing layer. The layer's pixel data is unchanged; the
  mask hides the target color at render time. The new mask is named
  `<layer_name>_color_to_alpha`.

Prefer `add_color_to_alpha_mask` unless the human explicitly asks for
the destructive form ("apply and flatten", "make a duplicate with the
color removed", "bake the transparency in"). The non-destructive form
is reversible and keeps the original pixels intact.

--------------------------------------------------------------------------------
convert_to_file_layer / export_layer_to_file
--------------------------------------------------------------------------------

Both take `layer_name` (required) and `output_path` (optional) and both
turn a layer into a file-backed layer. They differ in the shape of what
ends up in the layer stack:

- `convert_to_file_layer` replaces the source layer with a group of the
  same name. Inside the group is a single file layer whose name is a
  random hash. From the human's perspective the layer keeps its name,
  its position, and its place in the stack — it just became
  file-backed.

- `export_layer_to_file` removes the source layer and inserts a file
  layer at the same position, with the file layer's name being the
  original layer's name. No group is created. The layer is a flat file
  layer, not a group wrapping one.

Choose `convert_to_file_layer` when the human wants the layer to remain
a single visual unit that happens to be file-backed. Choose
`export_layer_to_file` when the human wants a flat, unambiguous file
layer with no grouping.

Both write a `.kra` file to disk. If `output_path` is omitted, the file
lands in a sibling `<document_stem>_layers/` directory with a random
8-character name. Name an explicit `output_path` when the bundle is
meant to be shared or re-run — the auto-generated path changes per run.

--------------------------------------------------------------------------------
fill_layer / fill_selection
--------------------------------------------------------------------------------

See FILL-TARGETS-BY-NAME for which one to pick.

Both commands have three color selectors — `color`, `foreground`,
`background` — and none of them is a required arg. At least one must be
supplied at runtime: the handler returns "No color specified" rather
than defaulting to any single one. An authoring AI asked to "fill a
layer" without a named color must either pick one from context or ask
the human.

`fill_layer` also requires `layer_name`; `fill_selection` requires an
active selection on the document and returns "No active selection" if
there is none.

--------------------------------------------------------------------------------
move_layer_to_group / move_active_layer_to_group
--------------------------------------------------------------------------------

See NAMED-VS-ACTIVE-VARIANTS. `move_layer_to_group` takes `layer_name`
and moves that specific layer; `move_active_layer_to_group` takes no
`layer_name` and moves whichever layer is currently active. Prefer the
named form.

One subtlety in `move_layer_to_group`: the `position` arg ("inside",
"above", "below") is relative to the group. `inside` places the moved
layer as a child of the group. `above` and `below` place it as a sibling
of the group, in the group's parent, at the corresponding position.
Neither variant changes the active layer.

--------------------------------------------------------------------------------
export_layer_to_file
--------------------------------------------------------------------------------

Removes the original layer and inserts a file layer at the same
position. If `output_path` is omitted, an auto-generated path in a
sibling `<document_stem>_layers/` directory is used, with a random
8-character filename. Prefer naming an explicit `output_path` when the
bundle is meant to be re-run or shared — the auto-generated path
changes per run.

--------------------------------------------------------------------------------
move_layer_to_new_document
--------------------------------------------------------------------------------

Despite the name, this duplicates the layer into a new document; the
source layer remains in place. Use it when the human wants a layer
"pulled out into its own document" as a working copy, not when they
want it moved out of the current document entirely.

--------------------------------------------------------------------------------
save_document
--------------------------------------------------------------------------------

Requires an explicit `file_path`. There is no "save in place" command
in the daemon; if the human asks to save over the current file, pass
the current document's path explicitly. A document that has never been
saved does not have a path to save to.

--------------------------------------------------------------------------------
create_palette
--------------------------------------------------------------------------------

Writes a `.kpl` file to the user's palette directory. The new palette
does NOT appear in the Palette docker until Krita is restarted. Use
`add_to_palette` to add colors to an existing palette without a
restart.

--------------------------------------------------------------------------------
