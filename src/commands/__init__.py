"""Static registry of CLI subcommands.

Adding a new subcommand: create src/commands/<name>.py implementing the
Command protocol, then append an instance to COMMANDS below.
"""
from __future__ import annotations

from src.commands.base import Command
from src.commands.collect import CollectCommand
from src.commands.config import ConfigCommand
from src.commands.projects import ProjectsCommand
from src.commands.report import ReportCommand
from src.commands.sync import SyncCommand

COMMANDS: list[Command] = [
    CollectCommand(),
    ReportCommand(),
    ConfigCommand(),
    ProjectsCommand(),
    SyncCommand(),
]

__all__ = ["Command", "COMMANDS"]
