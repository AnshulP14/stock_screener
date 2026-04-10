"""Application configuration (API keys, model names, per-LLM settings)."""

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- API keys (per provider) ---
    openai_api_key: str | None = None
    anthropic_api_key: str | None = None
    google_api_key: str | None = Field(default=None, description="Gemini via Generative Language API")

    # --- Default LLM: "openai" | "claude" | "gemini" ---
    default_llm: str = "openai"

    # --- OpenAI (e.g. o3-mini) ---
    llm_openai_model_id: str = "o3-mini"
    llm_openai_temperature: float = 0.2
    llm_openai_max_tokens: int = 4096
    # Reasoning effort for o-series: "low" | "medium" | "high"
    llm_openai_reasoning_effort: str | None = Field(default="medium", description="OpenAI reasoning effort")
    # OpenAI Responses API model (for web search) - gpt-4o supports domain filters
    llm_openai_responses_model_id: str = "gpt-4o-mini"

    # --- Anthropic (e.g. claude-sonnet-4-5) ---
    llm_anthropic_model_id: str = "claude-sonnet-4-5"
    llm_anthropic_temperature: float = 0.3
    llm_anthropic_max_tokens: int = 4096
    # Thinking budget (tokens) for extended thinking; 0 = off
    llm_anthropic_thinking_budget: int = Field(default=1024, description="Anthropic thinking budget (0 to disable)")

    # --- Google Gemini (e.g. gemini-2.5-flash) ---
    llm_google_model_id: str = "gemini-2.5-flash"
    llm_google_temperature: float = 0.2
    llm_google_max_tokens: int = 8192
    # Thinking: "low" | "medium" | "high", or budget (int); 0 = off
    llm_google_thinking_level: str | None = Field(default="low", description="Gemini thinking level")
    llm_google_thinking_budget: int | None = Field(default=None, description="Gemini thinking budget (overrides level if set)")

    # --- Router (main agent: gemini with thinking) ---
    llm_router_model_id: str = Field(default="gemini-2.5-flash", description="Router model — override with e.g. gemini-2.5-pro or gemini-3-flash-preview")
    llm_router_thinking_level: str = Field(default="medium", description="Router thinking level: low | medium | high")
    llm_router_thinking_budget: int | None = Field(default=None, description="Router thinking budget (overrides level if set)")

    # --- Optional: tools ---
    financial_api_key: str | None = None
    web_search_api_key: str | None = None
    perplexity_api_key: str | None = None

    # --- Perplexity (e.g. sonar) ---
    llm_perplexity_model_id: str = "sonar"
    llm_perplexity_temperature: float = 0.2
    llm_perplexity_max_tokens: int = 4096


@lru_cache
def get_settings() -> Settings:
    """Get cached settings instance. Settings are loaded once and reused."""
    return Settings()
