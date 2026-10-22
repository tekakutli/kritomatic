"""
pgkit.lint — static check for ES-module import/export consistency.

For every `.py` file in a playground project, extract its JS source
and verify that every `import { a, b } from "./x.js"` names symbols
that `x.py` actually exports.  Catches the ES-module pitfall where
the browser refuses to hand the module to any importer, the module
graph aborts, and the page loads as a shell.

USAGE
=====
    cd playgrounds/
    python -m pgkit.lint cable/

or from inside a project:

    cd playgrounds/cable/
    PYTHONPATH=.. python -m pgkit.lint .

The pgkit package directory is looked up as a sibling of the
argument directory (or the parent of `.`) when `--pgkit` is not given.

WHAT IT CHECKS
==============
    · imports whose target module doesn't exist in the scanned set
    · imports naming symbols their target module doesn't export
    · (both static `import { ... }` and top-of-file `export { ... }`
      lists are parsed; `export *` and `export { X as Y }` patterns
      are not currently handled and will appear as "target not
      found" — a known limitation)

WHAT IT DOESN'T CHECK
=====================
    · `no-undef` — unreferenced identifiers.  Not implemented;
      depends on ESLint being on PATH which isn't a dependency of
      pgkit.
    · runtime behavior — this is a static analysis pass, not a test.

GENERATED MODULES
=================
`pg_geometry.js` (in cable) and `bx_geometry.js` (in boxes) are
baked at render time by `_build_module_sources(geom)` and do not
exist as `.py` files.  The linter knows about these two by name and
treats them as "present" without checking their exports — they are
trivially correct by construction (`export const GEOMETRY = …`).

Any future generated module must be added to `_GENERATED_MODULES`
here, or the linter will emit a false positive.

SELF-EXCLUSION
==============
`pgkit/lint.py` contains docstring examples of the form

    import { a, b } from "./x.js"

which the import regex would match if `lint.py` were scanned as a
source module.  `lint.py` is therefore excluded from the pgkit scan
by name — it is a Python-side tool, not a JS source, and any
`import` line inside it is documentation, not runtime.
"""

import argparse
import os
import re
import sys
from pathlib import Path


_IMPORT_RE = re.compile(
    r'import\s*\{([^}]+)\}\s*from\s*["\'](\./[^"\']+)["\']'
)
_EXPORT_PATTERNS = [
    re.compile(r'\bexport\s+(?:async\s+)?function\s+(\w+)'),
    re.compile(r'\bexport\s+function\s*\*\s*(\w+)'),
    re.compile(r'\bexport\s+(?:const|let|var)\s+(\w+)'),
    re.compile(r'\bexport\s+class\s+(\w+)'),
]
_EXPORT_LIST_RE = re.compile(r'\bexport\s*\{\s*([^}]+)\s*\}')

# Modules generated at build time by _build_module_sources() — treated
# as present, exports not checked.  Any future generated module must
# be listed here.
_GENERATED_MODULES = {
    "pg_geometry.js",
    "bx_geometry.js",
}

# Python files under pgkit/ that are tools, not JS source.  Their
# contents are skipped during the pgkit scan.
_PGKIT_SKIP = {"__init__.py", "lint.py"}


def _parse(text):
    imports = []
    exports = []
    for m in _IMPORT_RE.finditer(text):
        lineno = text[:m.start()].count("\n") + 1
        names = set()
        for raw in m.group(1).split(","):
            raw = raw.strip()
            if raw:
                names.add(raw.split()[0])
        imports.append((lineno, m.group(2), names))
    for pat in _EXPORT_PATTERNS:
        for m in pat.finditer(text):
            lineno = text[:m.start()].count("\n") + 1
            exports.append((lineno, m.group(1)))
    for m in _EXPORT_LIST_RE.finditer(text):
        lineno = text[:m.start()].count("\n") + 1
        for raw in m.group(1).split(","):
            raw = raw.strip()
            if raw:
                exports.append((lineno, raw.split()[0]))
    return imports, exports


def _index(project_dir, pgkit_dir):
    exports = {}
    imports = {}
    sources = {}

    for py in sorted(project_dir.glob("*.py")):
        mod = py.stem + ".js"
        text = py.read_text(encoding="utf-8")
        imps, exps = _parse(text)
        exports[mod] = {name for _, name in exps}
        imports[mod] = imps
        sources[mod] = py

    if pgkit_dir and pgkit_dir.is_dir():
        for py in sorted(pgkit_dir.glob("*.py")):
            if py.name in _PGKIT_SKIP:
                continue
            mod = "pgkit/" + py.stem + ".js"
            text = py.read_text(encoding="utf-8")
            imps, exps = _parse(text)
            exports[mod] = {name for _, name in exps}
            imports[mod] = imps
            sources[mod] = py

    return exports, imports, sources


def _resolve(source_mod, path):
    if path.startswith("./"):
        path = path[2:]
    if source_mod.startswith("pgkit/"):
        return "pgkit/" + path
    return path


def main():
    p = argparse.ArgumentParser(prog="pgkit.lint")
    p.add_argument("project")
    p.add_argument("--pgkit", default=None)
    args = p.parse_args()

    project_dir = Path(args.project).resolve()
    if not project_dir.is_dir():
        print("not a directory: " + str(project_dir), file=sys.stderr)
        return 1

    if args.pgkit:
        pgkit_dir = Path(args.pgkit).resolve()
    else:
        pgkit_dir = project_dir.parent / "pgkit"
        if not pgkit_dir.is_dir():
            pgkit_dir = None

    exports, imports, sources = _index(project_dir, pgkit_dir)

    errors = []
    for source_mod, imps in imports.items():
        for (lineno, path, names) in imps:
            target = _resolve(source_mod, path)
            if target in _GENERATED_MODULES:
                continue
            if target not in exports:
                errors.append((sources[source_mod], lineno,
                               path, "target not found"))
                continue
            for name in sorted(names):
                if name not in exports[target]:
                    errors.append((sources[source_mod], lineno,
                                   name, "not exported by " + target))

    print("import/export ", end="")
    if not errors:
        print("✓  (" + str(len(exports)) + " module(s))")
        return 0
    print("FAIL")
    for py, lineno, name, reason in errors:
        print("  " + str(py) + ":" + str(lineno))
        print("    " + name + " — " + reason)
    return 1


if __name__ == "__main__":
    sys.exit(main())
