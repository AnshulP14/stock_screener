"""Builds and caches the Pydantic AI model for each agent role.

get_model() takes an agent type ("screening", "web_search") or a role name
("router", "synthesis"). Configure via LLM_* env vars — see config.Settings.
"""

from typing import Any

from stock_screening.config import get_settings
from stock_screening.models.types import AgentType

ModelT = Any  # Pydantic AI Model; providers are imported lazily below.

_REGISTRY: dict[str, ModelT] = {}


def _build_openai_responses() -> ModelT:
    """OpenAI Responses API — required for the web search agent."""
    from pydantic_ai.models.openai import OpenAIResponsesModel
    from pydantic_ai.providers.openai import OpenAIProvider

    s = get_settings()
    if not s.openai_api_key:
        raise ValueError("OPENAI_API_KEY is required for the web_search agent.")
    return OpenAIResponsesModel(
        s.llm_openai_responses_model_id,
        provider=OpenAIProvider(api_key=s.openai_api_key),
    )


def _build_anthropic() -> ModelT:
    from pydantic_ai.models.anthropic import AnthropicModel, AnthropicModelSettings
    from pydantic_ai.providers.anthropic import AnthropicProvider

    s = get_settings()
    if not s.anthropic_api_key:
        raise ValueError("ANTHROPIC_API_KEY is required for the screening agent.")

    settings: dict[str, Any] = {
        "temperature": s.llm_anthropic_temperature,
        "max_tokens": s.llm_anthropic_max_tokens,
    }
    if s.llm_anthropic_thinking_budget:
        settings["anthropic_thinking"] = {
            "type": "enabled",
            "budget_tokens": s.llm_anthropic_thinking_budget,
        }
        settings["temperature"] = 1.0  # required by the API when thinking is enabled

    return AnthropicModel(
        s.llm_anthropic_model_id,
        provider=AnthropicProvider(api_key=s.anthropic_api_key),
        settings=AnthropicModelSettings(**settings),
    )


def _build_gemini(*, thinking: bool) -> ModelT:
    from pydantic_ai.models.google import GoogleModel, GoogleModelSettings
    from pydantic_ai.providers.google import GoogleProvider

    s = get_settings()
    if not s.google_api_key:
        raise ValueError("GOOGLE_API_KEY is required for the router and synthesis models.")

    settings: dict[str, Any] = {"temperature": 0.2, "max_tokens": 8192}
    # gemini-2.5-flash rejects thinking config.
    if thinking and "gemini-2.5-flash" not in s.llm_router_model_id:
        if s.llm_router_thinking_budget:
            settings["google_thinking_config"] = {"thinking_budget": s.llm_router_thinking_budget}
        elif s.llm_router_thinking_level:
            settings["google_thinking_config"] = {"thinking_level": s.llm_router_thinking_level}

    return GoogleModel(
        s.llm_router_model_id,
        provider=GoogleProvider(api_key=s.google_api_key),
        settings=GoogleModelSettings(**settings),
    )


_BUILDERS = {
    "openai_responses": _build_openai_responses,
    "claude": _build_anthropic,
    "router": lambda: _build_gemini(thinking=True),
    "synthesis": lambda: _build_gemini(thinking=False),
}

AGENT_MODEL_MAP: dict[str, str] = {
    AgentType.SCREENING: "claude",
    AgentType.WEB_SEARCH: "openai_responses",
}


def get_model(name: str) -> ModelT:
    key = AGENT_MODEL_MAP.get(name.strip().lower(), name.strip().lower())
    if key not in _BUILDERS:
        raise ValueError(f"Unknown agent or model: {name}. Use one of: {list(_BUILDERS)}")
    if key not in _REGISTRY:
        _REGISTRY[key] = _BUILDERS[key]()
    return _REGISTRY[key]
