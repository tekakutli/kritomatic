#!/usr/bin/env python3
"""
Kritomatic CLI entry point.

Before `main()` runs, if a project-local `.venv` exists next to the
source tree and the current interpreter is not already that venv's
Python, this file re-execs into it. The re-exec is what makes the CLI
usable regardless of which Python the `kritomatic` shim on PATH was
installed under.

Why this exists
===============
`pipx install -e .` binds the `kritomatic` console script to a
pipx-managed virtual environment. That environment is created from
whatever `install_requires` declared at install time; if `setup.py`
gained a dependency afterwards, the shim's interpreter does not have
it, and the CLI fails on import with a message like "requires the
'tomlkit' package" even though the user has the package installed in
the repo's own `.venv`.

The alternative to a re-exec would be telling the user to
`pipx inject` or to re-install into the pipx venv every time a
dependency is added. That is friction the CLI can absorb: it looks
for a `.venv` at the repository root and, if one is present and this
process is not already running under it, replaces this process with
the venv's Python. The venv is the environment the developer
maintains; the shim becomes a launcher for it.


PIPX INSTALL MODES — WHAT TO DO WHEN A DEPENDENCY IS ADDED
==========================================================
The CLI has two supported install shapes, and they behave
differently the moment a new runtime dependency lands in
`setup.py`'s `install_requires`.

1. Non-editable: `pipx install .` (the pipx default).

   pipx copies the source tree into its own venv at install time.
   The shim on PATH imports `kritomatic` from that copy. Editing the
   source afterwards — even a change as small as this file's own
   `_reexec_into_repo_venv()` — does not reach the shim. Adding a
   dependency to `setup.py` and re-running `kritomatic` produces an
   ImportError naming the missing package; nothing else changes,
   because the whole file that would have provided the fix was
   captured before the fix existed.

   This is what happened with `tomlkit`. The panel module requires
   it; the field that was supposed to make the CLI look in the repo
   `.venv` for it was added to `__main__.py` after the initial
   install; the shim ran a stale `__main__.py` that did not have the
   field, so the re-exec never fired. The error message was correct
   about the missing package and wrong about what had already been
   done to fix it.

2. Editable: `pipx install --force -e .`

   pipx installs a pointer to the source tree rather than a copy.
   The shim imports `kritomatic` from `<repo>/src/` directly, so
   source edits take effect on the next run without reinstalling.
   The re-exec in this file fires on the first run after the switch
   and pulls the process into the repo's `.venv`, where the new
   dependency is visible.

   This is the mode the CLI expects. It is what makes adding a
   dependency a one-line edit to `setup.py` plus (at most) a
   `pip install <package>` in the repo `.venv`.


WHAT TO TELL THE USER WHEN A DEPENDENCY IS MISSING
==================================================
The symptom: `kritomatic <anything>` exits immediately with

    This command requires the '<package>' package:
        pip install <package>

even though the package is installed in the repo's `.venv`.

Diagnosis, in two commands:

    which kritomatic
    head -1 "$(which kritomatic)"

The shebang line of the shim names the interpreter it runs under.
If that path is a pipx venv and the shim's `kritomatic` package is
imported from a pipx-managed directory rather than from
`<repo>/src/`, the install is non-editable and stale.

Fix:

    pipx install --force -e .

That one command switches the shim to an editable install; from
then on, `setup.py` edits and source edits reach the shim without a
reinstall, and the re-exec in this file brings the process into the
repo `.venv` on the next run. Verify with `kritomatic --help` or any
command whose module requires the new package.

Diagnosis if the CLI still fails after `pipx install --force -e .`:

    kritomatic --refresh
    python -c "import sys, kritomatic; \\
               print(sys.executable); print(kritomatic.__file__)"

That prints the interpreter and the installed package location. If
`kritomatic.__file__` is not under `<repo>/src/`, the editable
install did not take; re-run the install, or investigate with
`pipx list`.


WHAT TO DO WHEN ADDING A DEPENDENCY AS A DEVELOPER
==================================================
1. Add the package to `install_requires` in `setup.py`.
2. Install it in the repo `.venv`: `source .venv/bin/activate &&
   pip install <package>`.
3. If your own `kritomatic` shim was installed non-editably, run
   `pipx install --force -e .` once.
4. Source edits under `src/kritomatic/` and `kritomatic_daemon/` do
   not require any other step; the editable install and the re-exec
   together make them live on the next invocation.


What is preserved
=================
The argument vector and the process identity. `os.execve` replaces
the running image; signal handling, exit code, and stdin/stdout
all come from the new interpreter. `sys.argv` is reconstructed from
`sys.argv[1:]`, so the user's arguments pass through untouched.

What is deliberately not done
=============================
- No `sys.path` surgery to "borrow" the venv's site-packages into
  the current interpreter. Borrowing works for pure-Python
  packages but breaks for anything with compiled extensions that
  was built against a different Python version.
- No detection of which package is missing to decide whether to
  re-exec. The check is unconditional: a `.venv` at the repo root
  takes precedence over whatever interpreter the shim was bound to.
- No bypass env var. If a case arises where the re-exec is
  unwanted, that is a design conversation, not a hidden switch.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


def _reexec_into_repo_venv() -> None:
    """Replace this process with the project venv's Python, if any.

    Detection:
      - The repo root is two parents up from this file
        (<repo>/src/kritomatic/__main__.py).
      - The venv lives at <repo>/.venv. Its Python is at
        .venv/bin/python on POSIX and .venv/Scripts/python.exe on
        Windows.
      - If the venv Python is missing, or if it is the interpreter
        we are already running under, this function returns and the
        current interpreter continues. Comparing resolved paths
        protects against a symlinked shim causing a re-exec loop.

    On re-exec:
      - The new argv is `[venv_python, -m, kritomatic, *argv[1:]]`.
        Running as a module rather than as a script means the
        `kritomatic` package on `src/` is importable by name, which
        is what `main.py` expects.
      - `PYTHONPATH` gains `<repo>/src` so the `-m kritomatic` lookup
        finds the package even when the venv does not itself have an
        editable install registered. The existing `PYTHONPATH`, if
        any, is preserved after our prefix.
      - `os.execve` does not return on success. If it raises (a
        permission problem, a race, an unusual platform restriction),
        the exception is caught and the current interpreter carries
        on. The failure mode is "the re-exec did not happen", which
        is where we started.
    """
    here = Path(__file__).resolve().parent          # .../src/kritomatic
    repo_root = here.parent.parent                  # <repo>
    src_dir = repo_root / "src"

    if os.name == "nt":
        venv_python = repo_root / ".venv" / "Scripts" / "python.exe"
    else:
        venv_python = repo_root / ".venv" / "bin" / "python"

    if not venv_python.is_file():
        return

    try:
        current = Path(sys.executable).resolve()
        target = venv_python.resolve()
    except OSError:
        return

    if current == target:
        return  # already running under the venv

    env = os.environ.copy()
    existing = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = (
        str(src_dir) + os.pathsep + existing if existing else str(src_dir)
    )

    argv = [str(target), "-m", "kritomatic", *sys.argv[1:]]

    try:
        os.execve(str(target), argv, env)
    except OSError:
        return


_reexec_into_repo_venv()


from kritomatic.main import main


if __name__ == '__main__':
    main()
