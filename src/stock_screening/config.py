"""Settings, loaded from ./.env or ~/.env. Every field is overridable by env var."""

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

_project_env = Path(__file__).parent.parent.parent / ".env"
_env_file = str(_project_env if _project_env.exists() else Path.home() / ".env")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=_env_file,
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # API keys — one per model role below.
    openai_api_key: str | None = None
    anthropic_api_key: str | None = None
    google_api_key: str | None = Field(default=None, description="Gemini via Generative Language API")
    perplexity_api_key: str | None = None

    # Router + synthesis (Gemini). Synthesis reuses the router model without thinking.
    llm_router_model_id: str = "gemini-2.5-flash"
    llm_router_thinking_level: str = Field(default="medium", description="low | medium | high")
    llm_router_thinking_budget: int | None = Field(default=None, description="Overrides thinking_level")

    # Screening agent (Claude).
    llm_anthropic_model_id: str = "claude-sonnet-4-5"
    llm_anthropic_temperature: float = 0.3
    llm_anthropic_max_tokens: int = 4096
    llm_anthropic_thinking_budget: int = Field(default=1024, description="Tokens; 0 disables thinking")

    # Web search agent (OpenAI Responses API).
    llm_openai_responses_model_id: str = "gpt-4o-mini"

    # search_web tool (Perplexity).
    llm_perplexity_model_id: str = "sonar"


@lru_cache
def get_settings() -> Settings:
    return Settings()
