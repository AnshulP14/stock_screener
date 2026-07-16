"""Web search agent: coordinates Perplexity searches for Indian market news."""

import json
import re
from typing import Literal, Optional

import httpx
from pydantic_ai import Agent, RunContext
from pydantic_ai.messages import ModelMessage, ToolReturnPart

from stock_screening.config import get_settings
from stock_screening.logging_config import get_logger
from stock_screening.models.constants import ALLOWED_DOMAINS
from stock_screening.models.context_providers import with_default_context
from stock_screening.models.llm_factory import get_model
from stock_screening.models.outputs import WebSearchResponse
from stock_screening.models.types import AgentType

logger = get_logger(__name__)

SYSTEM_PROMPT = """You are a financial news research coordinator for Indian NSE/BSE stocks.

Use the `search_web` tool to fulfill research tasks.

CRITICAL OUTPUT RULES:
1. You MUST populate `news_items` with information from the tool's "answer" field.
2. Extract key facts from the tool result and create NewsItem entries.
3. Each NewsItem needs: what_happened (the fact) and why_it_matters (significance).
4. Never return empty news_items - always extract at least one fact from tool results.

TOOL USAGE:
- Use `recency` parameter for time filtering: 'hour', 'day', 'week', 'month', 'year'.
- Call the tool multiple times if needed for comprehensive data.
"""

web_search_agent: Agent[None, WebSearchResponse] = with_default_context(Agent(
    get_model(AgentType.WEB_SEARCH),
    system_prompt=SYSTEM_PROMPT,
    output_type=WebSearchResponse,
))

_NO_RESULT_PHRASES = ("no results", "could not find", "no information", "no reports", "no news")


@web_search_agent.tool
async def search_web(
    ctx: RunContext[None],
    query: str,
    recency: Optional[Literal["hour", "day", "week", "month", "year"]] = None,
) -> str:
    """Search the web for Indian stock news.

    Args:
        query: The search query.
        recency: Optional recency filter ('hour', 'day', 'week', 'month', 'year').
    """
    s = get_settings()
    if not s.perplexity_api_key:
        raise ValueError("PERPLEXITY_API_KEY is required for search_web.")

    logger.info("Perplexity search: query=%s recency=%s", query, recency)
    payload = {
        "model": s.llm_perplexity_model_id,
        "messages": [{"role": "user", "content": query}],
        "search_domain_filter": ALLOWED_DOMAINS,
    }
    if recency:
        payload["search_recency_filter"] = recency

    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            response = await client.post(
                "https://api.perplexity.ai/chat/completions",
                headers={"Authorization": f"Bearer {s.perplexity_api_key}"},
                json=payload,
            )
            response.raise_for_status()
            data = response.json()
        except httpx.HTTPStatusError as e:
            logger.error("Perplexity API error %d: %s", e.response.status_code, e.response.text)
            return json.dumps({
                "error": f"API Error: {e.response.status_code}",
                "details": e.response.text,
            })

    answer = data["choices"][0]["message"]["content"]
    citations = data.get("citations", [])

    def _link(match: re.Match) -> str:
        num = int(match.group(1))
        if 1 <= num <= len(citations):
            return f"[[{num}]]({citations[num - 1]})"
        return match.group(0)

    return json.dumps({
        "answer": re.sub(r"\[(\d+)\]", _link, answer),
        "citations": citations,
        "search_results": data.get("search_results", []),
        "has_results": bool(citations) or not any(p in answer.lower() for p in _NO_RESULT_PHRASES),
    })


def extract_sources_from_messages(messages: list[ModelMessage]) -> list[dict[str, str]]:
    """Collect unique {title, url} sources from search_web tool outputs."""
    sources: list[dict[str, str]] = []
    seen: set[str] = set()

    for msg in messages:
        for part in getattr(msg, "parts", []) or []:
            if not isinstance(part, ToolReturnPart):
                continue
            try:
                data = json.loads(part.content)
            except (json.JSONDecodeError, TypeError):
                continue
            titles = {
                r.get("url"): r.get("title")
                for r in data.get("search_results", [])
                if r.get("url")
            }
            for url in data.get("citations", []):
                if url and url not in seen:
                    seen.add(url)
                    sources.append({"title": titles.get(url) or "Source", "url": url})

    return sources
