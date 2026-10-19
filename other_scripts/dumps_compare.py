#!/usr/bin/env python3
"""
Dump two git revisions of a project via dump.py, for diffing.

Per commit, this:
    git checkout --detach <sha>
    load dump.py fresh          (picks up that commit's version of the script)
    apply OVERRIDES to its module-level config variables
    run dump.py                 (writes e.g. project_dump_myproject.md)
    read the output into memory, delete the on-disk file

Then, AFTER checking back out to your original HEAD/branch, it writes both
captured dumps to the directory you invoked this script from, with the
configured suffixes:

    project_dump_myproject.md  ->  project_dump_myproject_previous.md
                                   project_dump_myproject_current.md

Commit refs are resolved to absolute SHAs at start (before any checkout),
so `HEAD~1` and `HEAD` mean the same thing throughout the run regardless of
where checkouts temporarily move the working tree.

Run it as:

    python3 compare_dumps.py

(Invoke it from wherever you want the final .md files to appear.)
"""

import importlib.util
import os
import subprocess
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# CONFIGURATION — edit these
# ---------------------------------------------------------------------------
PREVIOUS_COMMIT = "HEAD~1"     # e.g. a SHA, tag, branch, HEAD~3, HEAD@{1}, ...
CURRENT_COMMIT  = "HEAD"

# If you rewrote your current commit (amend / rebase / reset+recommit) and
# you want the PRE-amend snapshot as `previous`, use git's own reflog syntax:
#     PREVIOUS_COMMIT = "HEAD@{1}"
# (That's the commit HEAD pointed to before the rewrite. Plain "HEAD~1" is
# the *parent* commit and is unaffected by amending.)

# Overrides applied to dump.py's module-level variables for BOTH runs.
# Use exactly the names from the top of dump.py. Anything dump.py reads at
# run time (DEFAULT_DIR, DEFAULT_OUT, DEFAULT_EXTENSIONS, DEFAULT_AFFIX,
# DEFAULT_RECURSIVE, DEFAULT_TREE_ONLY, DEFAULT_EXCLUDE_DIRS,
# DEFAULT_TREE_EXCLUDE_DIRS, DEFAULT_INCLUDE_DIRS,
# DEFAULT_INCLUDE_FILE_SUBSTRINGS, DEFAULT_EXCLUDE_FILE_SUBSTRINGS) can be
# overridden here.
OVERRIDES = {
    # "DEFAULT_INCLUDE_DIRS": ["thisDir"],
    # "DEFAULT_EXTENSIONS": [".py", ".sh"],
    # "DEFAULT_RECURSIVE": True,
}

# Suffixes inserted before the extension of each dump's filename.
PREVIOUS_SUFFIX = "_previous"
CURRENT_SUFFIX  = "_current"

# Path to dump.py, relative to this script.
DUMP_SCRIPT = "dump.py"

# Put you back on the branch/commit you started from when done.
RESTORE_ORIGINAL_HEAD = True
# ---------------------------------------------------------------------------


# --- git helpers -----------------------------------------------------------

def git_rev_parse(repo_root: Path, ref: str):
    """Return the full SHA of `ref`, or None if it doesn't resolve."""
    r = subprocess.run(
        ["git", "rev-parse", "--verify", "--quiet", ref],
        cwd=repo_root, capture_output=True, text=True,
    )
    if r.returncode != 0:
        return None
    sha = r.stdout.strip()
    return sha or None


def run_git(args, cwd: Path):
    print(f"  $ git {' '.join(args)}", file=sys.stderr)
    r = subprocess.run(["git", *args], cwd=cwd)
    if r.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed (exit {r.returncode})")


def current_head_ref(repo_root: Path):
    """Return a branch name, or a SHA if HEAD is detached. None on failure."""
    r = subprocess.run(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"],
        cwd=repo_root, capture_output=True, text=True,
    )
    if r.returncode != 0:
        return None
    ref = r.stdout.strip()
    if ref == "HEAD":  # detached
        return git_rev_parse(repo_root, "HEAD")
    return ref


def resolve_commits(repo_root: Path, prev_spec: str, curr_spec: str):
    """Pin PREVIOUS_COMMIT and CURRENT_COMMIT to absolute SHAs *now*,
    before any checkout can move HEAD. Returns (prev_sha, curr_sha)."""
    prev_sha = git_rev_parse(repo_root, prev_spec)
    if prev_sha is None:
        raise RuntimeError(f"cannot resolve PREVIOUS_COMMIT = {prev_spec!r}")
    curr_sha = git_rev_parse(repo_root, curr_spec)
    if curr_sha is None:
        raise RuntimeError(f"cannot resolve CURRENT_COMMIT = {curr_spec!r}")
    return prev_sha, curr_sha


def shortstat_between(repo_root: Path, prev_sha: str, curr_sha: str):
    """Human-readable summary of what changes between the two commits
    (file/line counts only — nothing path-specific to worry about)."""
    r = subprocess.run(
        ["git", "diff", "--shortstat", prev_sha, curr_sha],
        cwd=repo_root, capture_output=True, text=True,
    )
    if r.returncode != 0:
        return None
    return r.stdout.strip() or None


# --- dump.py driving -------------------------------------------------------

def load_dump_module(dump_path: Path):
    """Load dump.py as a *fresh* module object (no caching), so each commit's
    version of the script is used."""
    spec = importlib.util.spec_from_file_location("dump_instance", str(dump_path))
    if spec is None or spec.loader is None:
        raise RuntimeError(f"could not load {dump_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # does not run main()
    return module


def dump_at_commit(commit_sha: str, suffix: str, repo_root: Path,
                   dump_path: Path, overrides):
    """Checkout `commit_sha`, run dump.py with `overrides`, capture its
    output into memory, delete the on-disk file, and return
    (final_name, content).

    Nothing is left on disk from this run. The caller writes the final file
    once every git operation is complete.
    """
    print(f"\n=== {commit_sha} -> *{suffix}.md ===", file=sys.stderr)
    run_git(["checkout", "--detach", commit_sha], cwd=repo_root)

    module = load_dump_module(dump_path)
    for key, value in overrides.items():
        if not hasattr(module, key):
            print(f"  warning: dump.py has no variable {key!r} at "
                  f"{commit_sha[:12]}; override ignored", file=sys.stderr)
        setattr(module, key, value)

    saved_argv = sys.argv
    sys.argv = [str(dump_path)]
    try:
        rc = module.main()
    finally:
        sys.argv = saved_argv

    scanned_dir = Path(os.path.abspath(str(Path(module.DEFAULT_DIR))))
    actual = module.output_path_with_dirname(Path(module.DEFAULT_OUT), scanned_dir)

    if rc != 0:
        try:
            actual.unlink()
        except OSError:
            pass
        raise RuntimeError(f"dump.py exited with {rc} at {commit_sha[:12]}")

    if not actual.is_file():
        raise RuntimeError(f"expected dump output {actual} not found")

    try:
        content = actual.read_text(encoding="utf-8")
    finally:
        try:
            actual.unlink()
        except OSError:
            pass

    # Keep dump.py's basename (including any _<scanneddirname> suffix), but
    # let the caller pick the directory (the cwd the user invoked us from).
    final_name = f"{actual.stem}{suffix}{actual.suffix}"
    return final_name, content


# --- main ------------------------------------------------------------------

def main() -> int:
    user_cwd = Path.cwd()  # captured before any chdir

    repo_root = Path(__file__).resolve().parent
    dump_path = (repo_root / DUMP_SCRIPT).resolve()
    if not dump_path.is_file():
        print(f"error: {dump_path} not found", file=sys.stderr)
        return 1

    status = subprocess.run(
        ["git", "status", "--porcelain", "--untracked-files=no"],
        cwd=repo_root, capture_output=True, text=True,
    )
    if status.returncode != 0:
        print(f"error: not a git repo at {repo_root}", file=sys.stderr)
        return 1
    if status.stdout.strip():
        print("error: working tree has uncommitted changes to tracked files; "
              "commit or stash them first.\n", file=sys.stderr)
        print(status.stdout, file=sys.stderr)
        return 1

    try:
        prev_sha, curr_sha = resolve_commits(
            repo_root, PREVIOUS_COMMIT, CURRENT_COMMIT,
        )
    except RuntimeError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    print(f"Previous commit: {prev_sha}  ({PREVIOUS_COMMIT!r})", file=sys.stderr)
    print(f"Current commit:  {curr_sha}  ({CURRENT_COMMIT!r})", file=sys.stderr)
    if prev_sha == curr_sha:
        print("warning: PREVIOUS and CURRENT resolve to the same commit; "
              "both dumps will be identical. Check your refs.", file=sys.stderr)
    else:
        stat = shortstat_between(repo_root, prev_sha, curr_sha)
        if stat:
            print(f"diff {prev_sha[:8]}..{curr_sha[:8]}: {stat}", file=sys.stderr)
        else:
            print("diff: (no content differences between these commits)",
                  file=sys.stderr)

    orig_ref = current_head_ref(repo_root)

    # Keep cwd fixed at the repo root for dump.py's relative DEFAULT_DIR /
    # DEFAULT_OUT resolution.
    os.chdir(repo_root)

    prev_name = curr_name = None
    prev_content = curr_content = None
    ok = True
    try:
        prev_name, prev_content = dump_at_commit(
            prev_sha, PREVIOUS_SUFFIX, repo_root, dump_path, OVERRIDES,
        )
        curr_name, curr_content = dump_at_commit(
            curr_sha, CURRENT_SUFFIX, repo_root, dump_path, OVERRIDES,
        )
    except Exception as e:
        print(f"\nerror: {e}", file=sys.stderr)
        ok = False
    finally:
        if RESTORE_ORIGINAL_HEAD and orig_ref:
            try:
                run_git(["checkout", orig_ref], cwd=repo_root)
            except Exception as e:
                print(f"warning: could not restore {orig_ref!r}: {e}",
                      file=sys.stderr)

    if not ok:
        return 1

    # Final save — after the tree is back where it started.
    prev_path = user_cwd / prev_name
    curr_path = user_cwd / curr_name
    try:
        prev_path.write_text(prev_content, encoding="utf-8")
        curr_path.write_text(curr_content, encoding="utf-8")
    except OSError as e:
        print(f"error: failed to write dumps: {e}", file=sys.stderr)
        return 1

    print("\nDone.", file=sys.stderr)
    for label, path, content in (
        ("previous", prev_path, prev_content),
        ("current",  curr_path, curr_content),
    ):
        print(f"  {label:>8}: {path}  ({len(content.splitlines())} lines)",
              file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
