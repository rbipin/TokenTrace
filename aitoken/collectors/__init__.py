"""Collectors package."""

from .base import ActivityCollector
from .copilot_cli import CopilotCliCollector

__all__ = [
    "ActivityCollector",
    "CopilotCliCollector",
]
