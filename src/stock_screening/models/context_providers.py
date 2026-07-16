"""Dynamic context injected into every agent's system prompt at run time."""

from datetime import datetime
from typing import Callable, TypeVar

from pydantic_ai import Agent

AgentT = TypeVar("AgentT", bound=Agent)
ContextProvider = Callable[[], str]

_default_providers: list[ContextProvider] = []


def register_context_provider(provider: ContextProvider) -> ContextProvider:
    """Decorator: add a provider to every agent built with with_default_context()."""
    _default_providers.append(provider)
    return provider


def with_default_context(agent: AgentT) -> AgentT:
    for provider in _default_providers:
        agent.system_prompt(provider)
    return agent


@register_context_provider
def datetime_context() -> str:
    now = datetime.now()
    return f"Current date/time: {now.strftime('%Y-%m-%d %H:%M')} ({now.strftime('%A')}), timezone: IST"
