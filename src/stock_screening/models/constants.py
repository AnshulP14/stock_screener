"""Application constants."""

from typing import Any

MAX_ITERATIONS = 5
CONVERSATION_CONTEXT_MESSAGES = 10

# Conversation context budgets (chars)
SYNTHESIS_CONTEXT_MAX_CHARS = 3000
SUB_AGENT_CONTEXT_MAX_CHARS = 1000

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
