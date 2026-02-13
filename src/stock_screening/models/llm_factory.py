"""
LLM factory: build OpenAI, Anthropic, and Google models with provider-specific settings.

Use get_model(agent_type) (e.g. "screening", "web_search") or get_model("openai" | "claude" | "gemini").
Agent-to-model mapping: AGENT_MODEL_MAP. get_default_model() uses DEFAULT_LLM.
Configure via env: LLM_OPENAI_*, LLM_ANTHROPIC_*, LLM_GOOGLE_*, DEFAULT_LLM.
"""

from typing import Any

from stock_screening.config import get_settings
from stock_screening.models.types import AgentType

# Type for "any Pydantic AI Model" (base class is not always in scope)
ModelT = Any

_REGISTRY: dict[str, ModelT] = {}


def _build_openai() -> ModelT:
    from pydantic_ai.models.openai import OpenAIChatModel, OpenAIChatModelSettings
    from pydantic_ai.providers.openai import OpenAIProvider

    s = get_settings()
    if not s.openai_api_key:
        raise ValueError("OPENAI_API_KEY is required for OpenAI model.")

    settings_dict: dict[str, Any] = {
        "temperature": s.llm_openai_temperature,
        "max_tokens": s.llm_openai_max_tokens,
    }
    if s.llm_openai_reasoning_effort:
        settings_dict["openai_reasoning_effort"] = s.llm_openai_reasoning_effort

    settings = OpenAIChatModelSettings(**settings_dict)
    provider = OpenAIProvider(api_key=s.openai_api_key)
    return OpenAIChatModel(
        s.llm_openai_model_id,
        provider=provider,
        settings=settings,
    )


def _build_openai_responses() -> ModelT:
    """OpenAI Responses API - required for WebSearchTool."""
    from pydantic_ai.models.openai import OpenAIResponsesModel
    from pydantic_ai.providers.openai import OpenAIProvider

    s = get_settings()
    if not s.openai_api_key:
        raise ValueError("OPENAI_API_KEY is required for OpenAI model.")

    provider = OpenAIProvider(api_key=s.openai_api_key)
    # Use gpt-4o for responses API (supports web search)
    return OpenAIResponsesModel(
        s.llm_openai_responses_model_id,
        provider=provider,
    )


def _build_anthropic() -> ModelT:
    from pydantic_ai.models.anthropic import AnthropicModel, AnthropicModelSettings
    from pydantic_ai.providers.anthropic import AnthropicProvider

    s = get_settings()
    if not s.anthropic_api_key:
        raise ValueError("ANTHROPIC_API_KEY is required for Claude model.")

    settings_dict: dict[str, Any] = {
        "temperature": s.llm_anthropic_temperature,
        "max_tokens": s.llm_anthropic_max_tokens,
    }
    if s.llm_anthropic_thinking_budget is not None and s.llm_anthropic_thinking_budget > 0:
        settings_dict["anthropic_thinking"] = {"budget_tokens": s.llm_anthropic_thinking_budget}

    settings = AnthropicModelSettings(**settings_dict)
    provider = AnthropicProvider(api_key=s.anthropic_api_key)
    return AnthropicModel(
        s.llm_anthropic_model_id,
        provider=provider,
        settings=settings,
    )


def _build_google() -> ModelT:
    from pydantic_ai.models.google import GoogleModel, GoogleModelSettings
    from pydantic_ai.providers.google import GoogleProvider

    s = get_settings()
    if not s.google_api_key:
        raise ValueError("GOOGLE_API_KEY is required for Gemini model.")

    settings_dict: dict[str, Any] = {
        "temperature": s.llm_google_temperature,
        "max_tokens": s.llm_google_max_tokens,
    }
    # Thinking not supported on gemini-2.5-flash; use for pro/other models
    thinking: dict[str, Any] = {}
    if "gemini-2.5-flash" not in s.llm_google_model_id:
        if s.llm_google_thinking_budget is not None and s.llm_google_thinking_budget > 0:
            thinking["thinking_budget"] = s.llm_google_thinking_budget
        elif s.llm_google_thinking_level:
            thinking["thinking_level"] = s.llm_google_thinking_level
    if thinking:
        settings_dict["google_thinking_config"] = thinking

    settings = GoogleModelSettings(**settings_dict)
    provider = GoogleProvider(api_key=s.google_api_key)
    return GoogleModel(
        s.llm_google_model_id,
        provider=provider,
        settings=settings,
    )


def _build_router() -> ModelT:
    """Router: gemini-3-flash with thinking on (separate from default gemini)."""
    from pydantic_ai.models.google import GoogleModel, GoogleModelSettings
    from pydantic_ai.providers.google import GoogleProvider

    s = get_settings()
    if not s.google_api_key:
        raise ValueError("GOOGLE_API_KEY is required for router (Gemini) model.")

    settings_dict: dict[str, Any] = {
        "temperature": 0.2,
        "max_tokens": 8192,
    }
    thinking: dict[str, Any] = {}
    if s.llm_router_thinking_budget is not None and s.llm_router_thinking_budget > 0:
        thinking["thinking_budget"] = s.llm_router_thinking_budget
    else:
        thinking["thinking_level"] = s.llm_router_thinking_level
    settings_dict["google_thinking_config"] = thinking

    settings = GoogleModelSettings(**settings_dict)
    provider = GoogleProvider(api_key=s.google_api_key)
    return GoogleModel(
        s.llm_router_model_id,
        provider=provider,
        settings=settings,
    )


def _build_synthesis() -> ModelT:
    """Synthesis: same model as router but no thinking (faster, for response formatting)."""
    from pydantic_ai.models.google import GoogleModel, GoogleModelSettings
    from pydantic_ai.providers.google import GoogleProvider

    s = get_settings()
    if not s.google_api_key:
        raise ValueError("GOOGLE_API_KEY is required for synthesis (Gemini) model.")

    settings = GoogleModelSettings(
        temperature=0.2,
        max_tokens=8192,
    )
    provider = GoogleProvider(api_key=s.google_api_key)
    return GoogleModel(
        s.llm_router_model_id,
        provider=provider,
        settings=settings,
    )


_BUILDERS = {
    "openai": _build_openai,
    "openai_responses": _build_openai_responses,
    "claude": _build_anthropic,
    "gemini": _build_google,
    "router": _build_router,
    "synthesis": _build_synthesis,
}

# Agent type -> model (provider) name. Use get_model(agent_type) or get_model(model_name).
AGENT_MODEL_MAP: dict[str, str] = {
    AgentType.SCREENING: "claude",
    AgentType.WEB_SEARCH: "openai_responses",  # gpt-4o with domain filters
}


def get_model(name: str) -> ModelT:
    """
    Return a Pydantic AI Model for the given provider or agent.
    name: agent type (e.g. "screening", "web_search") or "openai" | "claude" | "gemini"
    """
    key = name.strip().lower()
    model_name = AGENT_MODEL_MAP.get(key, key)
    if model_name not in _BUILDERS:
        raise ValueError(
            f"Unknown LLM or agent: {name}. Use agent name or one of: {list(_BUILDERS)}"
        )
    if model_name not in _REGISTRY:
        _REGISTRY[model_name] = _BUILDERS[model_name]()
    return _REGISTRY[model_name]


def get_default_model() -> ModelT:
    """Return the model for DEFAULT_LLM (openai, claude, or gemini)."""
    s = get_settings()
    return get_model(s.default_llm)


def clear_model_cache() -> None:
    """Clear cached model instances (e.g. after config change)."""
    _REGISTRY.clear()
