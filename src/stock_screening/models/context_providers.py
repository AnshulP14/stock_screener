"""
Dynamic System Prompt Context Providers.

Register functions that provide dynamic context to agent system prompts.
Use @register_context_provider to add providers that apply to all agents.
"""

from datetime import datetime
from typing import Callable, TypeVar

from pydantic_ai import Agent

AgentT = TypeVar("AgentT", bound=Agent)
ContextProvider = Callable[[], str]

# Registry of default context providers applied to all agents
_default_providers: list[ContextProvider] = []


def register_context_provider(provider: ContextProvider) -> ContextProvider:
    """Register a context provider to be applied to all agents via with_default_context().

    Can be used as decorator:
        @register_context_provider
        def my_context() -> str:
            return "some dynamic context"
    """
    _default_providers.append(provider)
    return provider


def with_context(agent: AgentT, *providers: ContextProvider) -> AgentT:
    """Apply specific context providers to an agent's system prompt.

    Usage:
        agent = with_context(Agent(...), datetime_context, custom_context)
    """
    for provider in providers:
        agent.system_prompt(provider)
    return agent


def with_default_context(agent: AgentT) -> AgentT:
    """Apply all registered default context providers to an agent.

    Usage:
        agent = with_default_context(Agent(...))
    """
    return with_context(agent, *_default_providers)


# -----------------------------------------------------------------------------
# Built-in Context Providers
# -----------------------------------------------------------------------------


@register_context_provider
def datetime_context() -> str:
    """Provides current date/time in IST."""
    now = datetime.now()
    return f"Current date/time: {now.strftime('%Y-%m-%d %H:%M')} ({now.strftime('%A')}), timezone: IST"
