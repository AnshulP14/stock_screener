"""
LLM module facade - re-exports from split modules for backwards compatibility.

This module has been split into:
- llm_factory.py: Model building (get_model, AGENT_MODEL_MAP, etc.)
- context_providers.py: Dynamic system prompt context providers
- llm_service.py: LLMService with retry handling
"""

# Re-export from llm_factory
from stock_screening.models.llm_factory import (
    AGENT_MODEL_MAP,
    ModelT,
    clear_model_cache,
    get_default_model,
    get_model,
)

# Re-export from context_providers
from stock_screening.models.context_providers import (
    AgentT,
    ContextProvider,
    datetime_context,
    register_context_provider,
    with_context,
    with_default_context,
)

# Re-export from llm_service
from stock_screening.models.llm_service import (
    LLMService,
    llm_service,
)

__all__ = [
    # Factory
    "get_model",
    "get_default_model",
    "clear_model_cache",
    "AGENT_MODEL_MAP",
    "ModelT",
    # Context providers
    "register_context_provider",
    "with_context",
    "with_default_context",
    "datetime_context",
    "AgentT",
    "ContextProvider",
    # Service
    "LLMService",
    "llm_service",
]
