"""Shared base for agents: common model and optional dependencies."""

from dataclasses import dataclass, field
from typing import Any

from stock_screening.models.llm import get_default_model

__all__ = ["AgentDeps", "get_default_model"]


@dataclass
class AgentDeps:
    """Dependencies injectable into agents (e.g. API clients, shared state)."""

    # Sub-agent message histories (persisted across tool calls)
    screening_history: list[Any] = field(default_factory=list)
    web_search_history: list[Any] = field(default_factory=list)
    # Last extracted sources from web search (for main agent to include in response)
    last_sources: list[dict[str, str]] = field(default_factory=list)
    # Last applied filters from screening (for main agent to include in response)
    last_filters: dict[str, Any] | None = None