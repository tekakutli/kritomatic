"""
pgkit.schema — the schema runtime.

The schema is a flat, ordered list of field descriptors.  Each
descriptor declares one bindable value: its name (the API path),
its kind, its DOM rendering hints, and the `get` / `set` pair
that reads and writes the model.

Two things consume the schema:

    pgkit.render   walks the list and produces the panel DOM
                   plus the scrub-input / checkbox / select
                   bindings.

    pgkit.remote_client   walks the list at connect time and
                          sends the current values to the server,
                          and applies patches by calling each
                          field's `set`.

The descriptor shape:

    {
      name:      'pair.inclination',    // dotted, unique, stable
      kind:      'number'|'bool'|'enum'|'action',
      label:     'Incl.°',              // shown on the panel
      section:   'Pair',                // groups fields under a <details>
      title:     '...',                 // optional tooltip

      // number-only:
      rate:       0.4,                  // units per pixel of drag
      snapStep:   15,                   // Shift-held snap, optional
      min:        -90,                  // informational; the setter clamps
      max:        90,

      // enum-only:
      options:    [{ value, label }, ...],

      // bool / number / enum:
      get:        () => number | bool | string,
      set:        (value) => void,
      visible:    true,                 // false = API-only, no DOM

      // action-only:
      run:        () => void,
      prompt:     'Are you sure?',      // optional, if confirmation is wanted
    }

The schema runtime does not render anything itself; it only
stores the descriptors and provides lookup by name.
"""

SCHEMA_JS = r"""
/* ==========================================================================
   SCHEMA RUNTIME
   ========================================================================== */

const _pgkitSchema = {
  fields: [],
  byName: {},
};

function pgkitField(desc) {
  if (!desc || !desc.name) {
    throw new Error("pgkitField: name is required");
  }
  if (_pgkitSchema.byName[desc.name]) {
    throw new Error("pgkitField: duplicate name " + desc.name);
  }
  _pgkitSchema.fields.push(desc);
  _pgkitSchema.byName[desc.name] = desc;
  return desc;
}

/* Descriptor constructors.  These are thin wrappers that set the
   `kind` and forward every other option as-is, so a project's
   schema file reads as a list of declarative entries. */

function pgkitNumber(opts) {
  return Object.assign({ kind: "number", visible: opts.visible !== false },
                       opts);
}

function pgkitBool(opts) {
  return Object.assign({ kind: "bool", visible: opts.visible !== false },
                       opts);
}

function pgkitEnum(opts) {
  return Object.assign({ kind: "enum", visible: opts.visible !== false },
                       opts);
}

function pgkitAction(opts) {
  return Object.assign({ kind: "action" }, opts);
}

/* Direct read / write by name, used by the remote client when a
   patch arrives.  Returns undefined / false for unknown names. */

function pgkitGet(name) {
  const f = _pgkitSchema.byName[name];
  if (!f) return undefined;
  return f.get ? f.get() : undefined;
}

function pgkitSet(name, value) {
  const f = _pgkitSchema.byName[name];
  if (!f) return false;
  if (f.set) f.set(value);
  return true;
}

/* Every name currently declared, in declaration order. */
function pgkitNames() {
  return _pgkitSchema.fields.map(f => f.name);
}
"""
