"""
quiet.py — silence OS-level stderr around noisy C library calls.

The libfontconfig bundled in some pip-installed build123d wheels is older
than the system one and prints warnings while scanning modern
/etc/fonts/conf.d/* files.  We mute OS-level stderr (fd 2) around the
operations that trigger fontconfig — the build123d import and every Text()
call — so the console stays clean.  Python tracebacks still work, because
fd 2 is restored before any exception unwinds past the context manager.
"""

import os
from contextlib import contextmanager


@contextmanager
def quiet():
    """Redirect OS-level stderr (fd 2) to /dev/null for the block."""
    saved = os.dup(2)
    devnull = os.open(os.devnull, os.O_WRONLY)
    try:
        os.dup2(devnull, 2)
        yield
    finally:
        os.dup2(saved, 2)
        os.close(devnull)
        os.close(saved)
