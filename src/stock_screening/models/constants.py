"""Single source of truth for application constants."""

from typing import Any

# -----------------------------------------------------------------------------
# Agent Execution Limits
# -----------------------------------------------------------------------------

MAX_ITERATIONS = 5
CONVERSATION_CONTEXT_MESSAGES = 10

# Conversation context length limits (chars)
SYNTHESIS_CONTEXT_MAX_CHARS = 3000
SUB_AGENT_CONTEXT_MAX_CHARS = 1000  # Summary passed to screening/web_search

# -----------------------------------------------------------------------------
# Web Search Configuration
# -----------------------------------------------------------------------------

ALLOWED_DOMAINS = [
    "livemint.com",
    "business-standard.com",
    "economictimes.indiatimes.com",
    "moneycontrol.com",
    "thehindubusinessline.com",
    "financialexpress.com",
    "nseindia.com",
    "bseindia.com",
    "sebi.gov.in",
    "reuters.com",
    "bloomberg.com",
]

WEB_SEARCH_MODEL_SETTINGS: dict[str, Any] = {
    "openai_include_web_search_sources": True,
    "openai_include_raw_annotations": True,
}
