"""Shared code for the kritomatic CLI and the Krita daemon plugin.

Modules here must be importable under both Krita's bundled Python and
the repo's .venv (wherever the CLI runs). That rules out PyQt6 and
`krita`; stdlib plus common third-party libraries only.

The CLI reaches this package via pip install — both `pip install -e .`
and `pipx install .` put `kritomatic_shared` in site-packages, because
`setup.py` uses `find_packages(where='src')` and this package lives at
`src/kritomatic_shared/`.

The daemon reaches it by prepending `<repo>/src/` to `sys.path` at the
top of `kritomatic_daemon/utils/comfy_convert.py`. Both sides import
the same source files, so drift between the two is impossible by
construction.
"""