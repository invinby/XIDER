"""Command handler registry for the Windows agent."""

from collections.abc import Mapping
from typing import Any, Callable

HANDLERS: dict[str, Callable[..., Any]] = {}


def register(group: Mapping[str, Callable[..., Any]]) -> None:
    """Register a group of named command handlers."""
    HANDLERS.update(group)
