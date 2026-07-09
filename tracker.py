"""CLI entry point — builds the parser from the command registry and dispatches.

All subcommand logic lives in src/commands/; see src/commands/__init__.py
for the registry.
"""
from __future__ import annotations

import argparse
import sys

from src.commands import COMMANDS


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="tracker", description="AI token tracker")
    parser.add_argument("--db", default=None, help="path to usage.db")
    sub = parser.add_subparsers(dest="cmd")

    for command in COMMANDS:
        p = sub.add_parser(command.name, help=command.help)
        command.configure(p)
        p.set_defaults(run=command.run)

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    run = getattr(args, "run", None)
    if run is None:
        parser.print_help()
        sys.exit(1)
    sys.exit(run(args))


if __name__ == "__main__":
    main()
