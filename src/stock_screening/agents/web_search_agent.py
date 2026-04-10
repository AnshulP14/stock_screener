"""
Web Search Agent (Research Coordinator)

Uses GPT-4o-mini to coordinate research tasks using the Perplexity Search API.
Supports multiple searches with advanced filters (recency).
"""

import json
from typing import Literal, Optional

import httpx
from pydantic_ai import Agent, RunContext
from pydantic_ai.messages import ModelMessage, TextPart

from stock_screening.config import get_settings
from stock_screening.logging_config import get_logger
from stock_screening.models.constants import ALLOWED_DOMAINS
from stock_screening.models.llm import get_model, with_default_context
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

# -----------------------------------------------------------------------------
# Agent Definition
# -----------------------------------------------------------------------------

web_search_agent: Agent[None, WebSearchResponse] = with_default_context(Agent(
    get_model(AgentType.WEB_SEARCH),
    system_prompt=SYSTEM_PROMPT,
    output_type=WebSearchResponse,
))

@web_search_agent.tool
async def search_web(
    ctx: RunContext[None],
    query: str,
    recency: Optional[Literal["hour", "day", "week", "month", "year"]] = None,
) -> str:
    """
    Search the web for Indian stock news using Perplexity API.

    Args:
        ctx: The run context.
        query: The search query.
        recency: Optional recency filter ('hour', 'day', 'week', 'month', 'year').
    """
    s = get_settings()
    if not s.perplexity_api_key:
        raise ValueError("PERPLEXITY_API_KEY is required for direct API search.")

    logger.info("Perplexity API call: query=%s, recency=%s", query, recency)

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
                headers={
                    "Authorization": f"Bearer {s.perplexity_api_key}",
                    "Content-Type": "application/json"
                },
                json=payload
            )
            if response.status_code != 200:
                logger.error("Perplexity API error: %d - %s", response.status_code, response.text)
            response.raise_for_status()
            data = response.json()
        except httpx.HTTPStatusError as e:
            return json.dumps({
                "error": f"API Error: {e.response.status_code}",
                "details": e.response.text
            })

    # Extract answer, citations, and search_results (richer metadata)
    answer = data["choices"][0]["message"]["content"]
    citations = data.get("citations", [])
    search_results = data.get("search_results", [])
    
    # Resolve inline [1], [2] markers to clickable [[1]](url) links
    import re
    def resolve_citation(match: re.Match) -> str:
        num = int(match.group(1))
        if 1 <= num <= len(citations):
            return f"[[{num}]]({citations[num - 1]})"
        return match.group(0)
    answer_with_links = re.sub(r"\[(\d+)\]", resolve_citation, answer)
    
    # Check if the answer indicates no results found
    no_results_keywords = ["no results", "could not find", "no information", "no reports", "no news"]
    has_results = not any(k in answer.lower() for k in no_results_keywords) or len(citations) > 0

    return json.dumps({
        "answer": answer_with_links,
        "citations": citations,
        "search_results": search_results,
        "has_results": has_results
    })

# -----------------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------------

def extract_sources_from_messages(messages: list[ModelMessage]) -> list[dict[str, str]]:
    """Extract unique source URLs with titles from Perplexity API tool outputs."""
    sources: list[dict[str, str]] = []
    seen_urls: set[str] = set()

    for msg in messages:
        for part in getattr(msg, "parts", []):
            from pydantic_ai.messages import ToolReturnPart
            if isinstance(part, ToolReturnPart):
                try:
                    data = json.loads(part.content)
                    citations = data.get("citations", [])
                    search_results = data.get("search_results", [])
                    
                    # Build URL -> title map from search_results
                    url_to_title = {r.get("url"): r.get("title") for r in search_results if r.get("url")}
                    
                    for url in citations:
                        if url and url not in seen_urls:
                            seen_urls.add(url)
                            title = url_to_title.get(url) or "Source"
                            sources.append({"title": title, "url": url})
                except (json.JSONDecodeError, TypeError):
                    continue

    return sources

# -----------------------------------------------------------------------------
# Standalone Runner
# -----------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse
    import asyncio
    from stock_screening.logging_config import setup_logging
    from stock_screening.models.llm import llm_service

    async def main() -> None:
        parser = argparse.ArgumentParser(description="Web search coordinator for Indian stock news")
        parser.add_argument("query", nargs="*", help="Search query")
        args = parser.parse_args()

        setup_logging(level="INFO")
        logger.info("Web search coordinator starting")

        query = " ".join(args.query) if args.query else input("Search query: ").strip()
        if not query:
            print("No query provided")
            return

        logger.info("Coordinating research for: %s", query)
        
        result = await llm_service.run_agent(web_search_agent, query)
        output = result.output
        sources = extract_sources_from_messages(result.all_messages())

        print("\n" + "=" * 60)
        print(f"RESULTS: {len(output.news_items)} news items | {len(sources)} sources")
        print("=" * 60)

        for i, item in enumerate(output.news_items, 1):
            print(f"\n{i}. {item.what_happened}")
            print(f"   💡 {item.why_it_matters}")

        if sources:
            print("\n🔗 SOURCES:")
            for i, src in enumerate(sources, 1):
                print(f"   • {src['url']}")

    asyncio.run(main())
