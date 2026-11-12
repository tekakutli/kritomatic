"""
cli.py — the CLI surface for `kritomatic comfyui convert`.

The algorithm lives in `kritomatic_shared.comfy_convert`. This module
provides:

  * `populate_convert_args` — the argparse wiring, shared by
    `run_convert_command` (this module's runtime entry point) and
    `kritomatic.registry` (which builds the `comfyui convert` menu
    for `kritomatic --help`). Declaring the args once keeps the menu
    and the runtime parser in agreement.

  * `run_convert_command` — the runtime entry point, invoked by
    `kritomatic.main` when `sys.argv[1:3] == ['comfyui', 'convert']`.

The algorithm names (`ConversionError`, `convert_if_ui`,
`convert_workflow`, `is_ui_format`) are re-exported so existing
imports like `from kritomatic.comfy_convert.cli import
ConversionError` keep working unchanged. New code should import them
from `kritomatic_shared.comfy_convert` directly.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

from kritomatic.helpful_argparse import HelpfulArgumentParser
from kritomatic_shared.comfy_convert import (
    ConversionError,
    DEFAULT_COMFY_URL,
    convert_if_ui,
    convert_workflow,
    is_ui_format,
    default_output_dir,
)
from kritomatic_shared.comfy_convert.convert import CONVERT_DIR


__all__ = [
    "ConversionError",
    "convert_if_ui",
    "convert_workflow",
    "is_ui_format",
    "populate_convert_args",
    "run_convert_command",
]


# ============================================================================
#  CLI
# ============================================================================

def populate_convert_args(parser) -> None:
    """Add convert's args to `parser`.

    Shared by run_convert_command (the runtime parser) and by
    registry.py (which builds the same shape for `kritomatic --help`
    and `kritomatic comfyui --help`). Declaring them once keeps the
    two paths in agreement.
    """
    parser.add_argument(
        'input', type=Path,
        help='Path to the UI-format workflow JSON.',
    )
    parser.add_argument(
        '-o', '--output', type=Path, default=None,
        help=(f'Output file or directory. Default: '
              f'{CONVERT_DIR}/<input_filename>.'),
    )
    parser.add_argument(
        '--url', default=DEFAULT_COMFY_URL,
        help=(f'Base URL of the ComfyUI server (default: '
              f'{DEFAULT_COMFY_URL}). Retained for compatibility; '
              f'comfy-cli\'s --print-prompt conversion does not use '
              f'it.'),
    )
    parser.add_argument(
        '--id-scheme', choices=['auto', 'title', 'field'],
        default='auto',
        help=('How handle ids are generated: '
              'auto = <class>_<field>_<node_id> (default); '
              'title = <slug(node title)>_<field>; field = <field>.'),
    )
    parser.add_argument(
        '--quiet', action='store_true',
        help='Print only the final output path.',
    )


def _build_parser() -> HelpfulArgumentParser:
    p = HelpfulArgumentParser(
        prog='kritomatic comfyui convert',
        description=(
            "Convert a ComfyUI UI-format workflow to API format "
            "using comfy-cli, inject {{handle_id}} tokens for "
            f"user-supplied inputs, and save the result to "
            f"{CONVERT_DIR}/."
        ),
    )
    populate_convert_args(p)
    return p


def _resolve_output_path(input_path: Path,
                         user_output: Optional[Path],
                         ) -> Path:
    input_name = input_path.name

    if user_output is None:
        return default_output_dir() / input_name

    user_output = Path(user_output)

    if user_output.suffix == "":
        user_output.mkdir(parents=True, exist_ok=True)
        return user_output / input_name

    user_output.parent.mkdir(parents=True, exist_ok=True)
    return user_output


def _print_report(records: list[dict], out_path: Path) -> None:
    print(f"  Output   {out_path}")
    print()

    if not records:
        print("  No user-supplied inputs — ready to run as-is.")
        return

    n = len(records)
    plural = "" if n == 1 else "s"
    print(f"  {n} handle{plural} to fill:")
    print()

    for r in records:
        print(f'    "{r["token"]}"')
        print(f"      type   {r['type']}")
        print(f"      node   {r['node_id']}.{r['field']}")
        print()


def run_convert_command() -> int:
    """Entry point for `kritomatic comfyui convert`.

    Invoked from main.py when `sys.argv[1:3] == ['comfyui',
    'convert']`. Parses `sys.argv[3:]`.
    """
    parser = _build_parser()
    args = parser.parse_args(sys.argv[3:])

    if not args.input.is_file():
        print(f"Error: input file not found: {args.input}",
              file=sys.stderr)
        return 1

    out_path = _resolve_output_path(args.input, args.output)

    if not args.quiet:
        print(f"Converting {args.input} ...", flush=True)

    try:
        _, records = convert_workflow(
            args.input, out_path,
            url=args.url, id_scheme=args.id_scheme,
        )
    except ConversionError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    if args.quiet:
        # Contract: prints exactly one line — the output path — and
        # exits 0. Anything that wants the converted path (a wrapper
        # script, a caller in the CLI, a future feature) parses this
        # line. Don't change the format without updating that.
        print(out_path)
        return 0

    print()
    _print_report(records, out_path)
    return 0