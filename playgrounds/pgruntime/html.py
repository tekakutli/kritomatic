"""
pgruntime.html — helpers that wrap pgkit.html_shell.page() calls
with the render-or-exit pattern every *<name>_html.py module was
implementing locally.
"""

import sys

from pgkit.js_check import JsSyntaxError


def render_or_exit(render_fn, *, label: str):
    """Call `render_fn()`; on `JsSyntaxError`, print a diagnostic
    block to stderr and `sys.exit(1)`.

    The diagnostic includes the file label, line (if known), and
    the offending source lines.  Same format every existing
    `<name>_html.py` uses; the extraction just centralises it so a
    future playground doesn't have to reimplement it.
    """
    try:
        return render_fn()
    except JsSyntaxError as err:
        _report_and_exit(err, label)


def _report_and_exit(err: JsSyntaxError, label: str) -> None:
    print(file=sys.stderr)
    print("=" * 60, file=sys.stderr)
    if err.lineno is not None:
        print(f"JS syntax error in {label}, line {err.lineno}:",
              file=sys.stderr)
    else:
        print(f"JS syntax error in {label}:", file=sys.stderr)
    print(file=sys.stderr)
    print(f"  {err.message}", file=sys.stderr)
    print(file=sys.stderr)
    for line in err.raw.splitlines():
        print(f"  {line}", file=sys.stderr)
    print("=" * 60, file=sys.stderr)
    sys.exit(1)