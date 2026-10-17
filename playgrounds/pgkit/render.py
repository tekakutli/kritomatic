"""
pgkit.render — turn the schema into panel DOM.

`pgkitRenderPanel` walks the schema, groups consecutive fields by
`section`, and produces a set of `<details class="section">`
blocks inside the given container.  Each field becomes one
`<div class="field">` with the appropriate control.

Every rendered field is registered with the remote client
automatically, so adding a field to the schema is enough to make
it both visible on the panel and available over the API — no
separate registration step.

The renderer deliberately supports only the three "boring" kinds:
number, bool, enum.  Actions have no DOM of their own (a project
wires its own buttons to them).  Lists, drag interactions, and
anything with non-trivial semantics stay hand-written and register
themselves via `pgkitRegisterField` when they want API exposure.
"""

RENDER_JS = r"""
/* ==========================================================================
   SCHEMA → DOM
   ========================================================================== */

function _pgkitDomId(name) {
  return "pgkit_" + name.replace(/[^A-Za-z0-9]/g, "_");
}

function pgkitRenderPanel(containerId) {
  const container = document.getElementById(containerId);
  if (!container) return;
  container.innerHTML = "";

  let currentSection = null;
  let sectionBody = null;
  let currentDetails = null;

  for (const f of _pgkitSchema.fields) {
    if (f.visible === false) continue;

    if (f.section !== currentSection) {
      currentSection = f.section || "Fields";
      currentDetails = document.createElement("details");
      currentDetails.className = "section";
      currentDetails.open = true;

      const summary = document.createElement("summary");
      summary.textContent = currentSection;
      currentDetails.appendChild(summary);

      sectionBody = document.createElement("div");
      sectionBody.className = "section-body";
      currentDetails.appendChild(sectionBody);

      container.appendChild(currentDetails);
    }

    const el = _pgkitRenderField(f);
    if (el) sectionBody.appendChild(el);
  }
}

function _pgkitRenderField(f) {
  if (f.kind === "number") return _pgkitRenderNumber(f);
  if (f.kind === "bool")   return _pgkitRenderBool(f);
  if (f.kind === "enum")   return _pgkitRenderEnum(f);
  return null;
}

function _pgkitFieldRow(f) {
  const row = document.createElement("div");
  row.className = "field";
  const lbl = document.createElement("label");
  lbl.textContent = f.label || f.name;
  row.appendChild(lbl);
  return row;
}

function _pgkitRenderNumber(f) {
  const row = _pgkitFieldRow(f);

  const input = document.createElement("input");
  input.type = "text";
  input.className = "scrubInput";
  input.id = _pgkitDomId(f.name);
  input.autocomplete = "off";
  input.spellcheck = false;
  if (f.title) input.title = f.title;
  row.appendChild(input);

  _installScrubInput(input, {
    rate:     (typeof f.rate === "number") ? f.rate : 0.01,
    snapStep: f.snapStep,
    read:     f.get,
    write:    (raw) => {
      f.set(raw);
      pgkitNotifyLocal(f.name);
    },
  });

  pgkitRegisterField(f.name, {
    get: f.get,
    set: (v) => {
      _pgkitApplyingRemote = true;
      try { f.set(v); } finally { _pgkitApplyingRemote = false; }
      if (typeof draw === "function") draw();
    },
    meta: {
      kind:  "number",
      label: f.label,
      min:   f.min,
      max:   f.max,
    },
  });

  return row;
}

function _pgkitRenderBool(f) {
  const row = _pgkitFieldRow(f);

  const input = document.createElement("input");
  input.type = "checkbox";
  input.id = _pgkitDomId(f.name);
  if (f.title) input.title = f.title;
  if (f.accent === "red") input.classList.add("accent-red-checkbox");

  input.checked = !!f.get();

  input.addEventListener("change", () => {
    f.set(input.checked);
    pgkitNotifyLocal(f.name);
  });

  row.appendChild(input);

  pgkitRegisterField(f.name, {
    get: () => !!f.get(),
    set: (v) => {
      _pgkitApplyingRemote = true;
      try { f.set(!!v); } finally { _pgkitApplyingRemote = false; }
      if (document.activeElement !== input) input.checked = !!v;
      if (typeof draw === "function") draw();
    },
    meta: {
      kind:  "bool",
      label: f.label,
    },
  });

  return row;
}

function _pgkitRenderEnum(f) {
  const row = _pgkitFieldRow(f);

  const select = document.createElement("select");
  select.id = _pgkitDomId(f.name);
  if (f.title) select.title = f.title;

  for (const opt of (f.options || [])) {
    const o = document.createElement("option");
    o.value = String(opt.value);
    o.textContent = opt.label || String(opt.value);
    select.appendChild(o);
  }

  const cur = f.get();
  if (cur !== undefined && cur !== null) {
    select.value = String(cur);
  }

  select.addEventListener("change", () => {
    f.set(select.value);
    pgkitNotifyLocal(f.name);
  });

  row.appendChild(select);

  pgkitRegisterField(f.name, {
    get: () => f.get(),
    set: (v) => {
      _pgkitApplyingRemote = true;
      try { f.set(String(v)); } finally { _pgkitApplyingRemote = false; }
      if (document.activeElement !== select) {
        select.value = String(v);
      }
      if (typeof draw === "function") draw();
    },
    meta: {
      kind:    "enum",
      label:   f.label,
      options: (f.options || []).map(o => o.value),
    },
  });

  return row;
}

/* Refresh the DOM of every checkbox and select in the schema, so a
   change made by any path (a remote patch, a keyboard shortcut, a
   programmatic write) becomes visible without a full redraw.  The
   scrub inputs are synced by their own installer via the project's
   draw(), so this only touches bools and enums. */
function pgkitSyncSchemaDom() {
  for (const f of _pgkitSchema.fields) {
    if (f.visible === false) continue;
    const el = document.getElementById(_pgkitDomId(f.name));
    if (!el) continue;
    if (document.activeElement === el) continue;
    if (f.kind === "bool") el.checked = !!f.get();
    else if (f.kind === "enum") {
      const v = f.get();
      if (v !== undefined && v !== null) el.value = String(v);
    }
  }
}
"""
