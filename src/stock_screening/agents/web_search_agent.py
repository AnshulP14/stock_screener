"""
Web Search Agent

Searches the web for Indian stock news using OpenAI's WebSearchTool.
Returns structured news items with inline citations.

Run standalone:
    python -m src.stock_screening.agents.web_search_agent "Latest news on TCS"
"""

from pydantic_ai import Agent
from pydantic_ai.builtin_tools import WebSearchTool, WebSearchUserLocation
from pydantic_ai.messages import ModelMessage, TextPart

from stock_screening.logging_config import get_logger
from stock_screening.models.constants import ALLOWED_DOMAINS, WEB_SEARCH_MODEL_SETTINGS
from stock_screening.models.llm import get_model, llm_service, with_default_context
from stock_screening.models.outputs import WebSearchResponse
from stock_screening.models.types import AgentType

logger = get_logger(__name__)

SYSTEM_PROMPT = """You are a financial news research agent for Indian NSE/BSE stocks.

SEARCH GUIDELINES:
- Focus on recent news (respect any time constraints in the query)
- Include timestamps (IST) when available
- Cross-check media reports with NSE/SEBI filings when possible
- Add inline domain citations after facts: "TCS Q3 revenue grew 8% YoY [livemint.com]"
- Use the most authoritative source (NSE filing > media report)
- Raise cases where the news is conflicting or unclear across sources.
- Separate facts from analyst opinions
"""


# -----------------------------------------------------------------------------
# Agent Definition
# -----------------------------------------------------------------------------

web_search_agent: Agent[None, WebSearchResponse] = with_default_context(Agent(
    get_model(AgentType.WEB_SEARCH),
    system_prompt=SYSTEM_PROMPT,
    output_type=WebSearchResponse,
    builtin_tools=[
        WebSearchTool(
            search_context_size="high",
            user_location=WebSearchUserLocation(country="IN", city="Mumbai"),
            allowed_domains=ALLOWED_DOMAINS,
        ),
    ],
))


# -----------------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------------


def extract_sources_from_messages(messages: list[ModelMessage]) -> list[dict[str, str]]:
    """Extract unique source URLs from OpenAI annotations in message history."""
    sources: list[dict[str, str]] = []
    seen_urls: set[str] = set()

    for msg in messages:
        for part in getattr(msg, "parts", []):
            if isinstance(part, TextPart) and part.provider_details:
                for ann in part.provider_details.get("annotations", []):
                    if ann.get("type") == "url_citation":
                        url = ann.get("url", "")
                        if url and url not in seen_urls:
                            seen_urls.add(url)
                            sources.append({
                                "title": ann.get("title", ""),
                                "url": url,
                            })

    return sources


# -----------------------------------------------------------------------------
# Standalone Runner
# -----------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse
    import asyncio

    from stock_screening.logging_config import setup_logging

    async def main() -> None:
        parser = argparse.ArgumentParser(description="Web search agent for Indian stock news")
        parser.add_argument("query", nargs="*", help="Search query")
        parser.add_argument("--days", "-d", type=int, default=7, help="Days back (default: 7)")
        args = parser.parse_args()

        setup_logging(level="INFO")
        logger.info("Web search agent starting")

        query = " ".join(args.query) if args.query else input("Search query: ").strip()
        if not query:
            print("No query provided")
            return

        days_back = args.days if args.days > 0 else None
        time_note = f" (last {days_back} days)" if days_back else ""
        full_query = f"{query}{time_note}"

        logger.info("Searching: %s", full_query)
        print(f"\nSearching: {full_query}\n")

        result = await llm_service.run_agent(
            web_search_agent, full_query, model_settings=WEB_SEARCH_MODEL_SETTINGS
        )
        output = result.output

        # Extract sources from annotations
        sources = extract_sources_from_messages(result.all_messages())

        # Print results
        print("=" * 60)
        print(f"RESULTS: {len(output.news_items)} news items | {len(sources)} sources")
        print("=" * 60)

        if output.news_items:
            print("\n📰 NEWS ITEMS:")
            print("-" * 40)
            for i, item in enumerate(output.news_items, 1):
                print(f"\n{i}. {item.what_happened}")
                if item.event_time_ist:
                    print(f"   🕐 {item.event_time_ist}")
                print(f"   💡 {item.why_it_matters}")
                if item.how_did_customers_react:
                    print(f"   📈 {item.how_did_customers_react}")
        else:
            print("\n⚠️  No news items found.")

        if output.overall_market_reaction:
            print(f"\n📊 OVERALL MARKET REACTION:\n   {output.overall_market_reaction}")

        if output.analyst_commentary:
            print(f"\n🎙️  ANALYST COMMENTARY:\n   {output.analyst_commentary}")

        if sources:
            print("\n" + "-" * 40)
            print("🔗 SOURCES:")
            for src in sources:
                title = src['title'][:60] + "..." if len(src['title']) > 60 else src['title']
                print(f"   • {title}")
                print(f"     {src['url']}")

        # Status
        print("\n" + "=" * 60)
        if output.satisfied:
            print("✅ Search complete")
        else:
            print("⏳ Search incomplete")
            if output.next_query:
                print(f"   Suggested follow-up: {output.next_query}")

    asyncio.run(main())
