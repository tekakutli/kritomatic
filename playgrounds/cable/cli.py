"""Top-level CLI dispatcher.  Each subcommand lives in commands/<name>.py
and exposes a single `run(argv) -> int` function.  This module only
builds the root parser and dispatches; it holds zero domain logic."""
import argparse
import sys

from . import __version__
from .commands import (
    capture as capture_cmd,
    recover as recover_cmd,
    dig as dig_cmd,
    extension as extension_cmd,
    diagnose as diagnose_cmd,
)


def _build_root_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="deepdig",
        description="Capture DeepSeek conversations and recover files from them.",
    )
    p.add_argument("--version", action="version", version=f"deepdig {__version__}")
    p.add_argument("-v", "--verbose", action="store_true",
                   help="verbose diagnostics to stderr")
    p.add_argument("--no-color", action="store_true")
    sub = p.add_subparsers(dest="command", required=True)

    capture_cmd.register(sub)
    recover_cmd.register(sub)
    dig_cmd.register(sub)
    extension_cmd.register(sub)
    diagnose_cmd.register(sub)
    return p


_DISPATCH = {
    "capture": capture_cmd.run,
    "recover": recover_cmd.run,
    "dig": dig_cmd.run,
    "extension": extension_cmd.run,
    "diagnose": diagnose_cmd.run,
}


def main(argv: list[str] | None = None) -> int:
    args = _build_root_parser().parse_args(argv)
    try:
        return _DISPATCH[args.command](args)
    except KeyboardInterrupt:
        print("\nCancelled.", file=sys.stderr)
        return 1
