"""Shared state passed between the router and its sub-agents."""

from dataclasses import dataclass, field
from typing import Any

__all__ = ["AgentDeps"]


@dataclass
class AgentDeps:
    """Per-session agent state. Message histories persist across turns."""

    screening_history: list[Any] = field(default_factory=list)
    web_search_history: list[Any] = field(default_factory=list)
    last_sources: list[dict[str, str]] = field(default_factory=list)
    last_filters: dict[str, Any] | None = None
