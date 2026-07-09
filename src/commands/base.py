"""Command protocol for CLI subcommands.

Each subcommand is a self-contained unit implementing this protocol and
registered in the COMMANDS list in src/commands/__init__.py. Adding a new
subcommand only requires a new module plus one registry entry.
"""
from __future__ import annotations

import argparse
from typing import Protocol, runtime_checkable


@runtime_checkable
class Command(Protocol):
    """A single CLI subcommand: knows its name, its flags, and how to run."""

    name: str
    help: str

    def configure(self, parser: argparse.ArgumentParser) -> None:
        """Add this command's arguments to its subparser."""
        ...

    def run(self, args: argparse.Namespace) -> int:
        """Execute the command; return a process exit code."""
        ...
