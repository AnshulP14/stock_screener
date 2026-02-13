"""
Screening Agent

LLM-powered agent for natural language stock screening.
Interprets user queries and calls appropriate screening functions.
Uses structured output_type for the agent's final response (ScreeningResponse).

Can be run standalone:
    python -m src.stock_screening.agents.screening_agent

Or imported and used programmatically.
"""

from pydantic_ai import Agent, RunContext

from stock_screening.agents.base import AgentDeps
from stock_screening.logging_config import get_logger
from stock_screening.models.llm import get_model, llm_service, with_default_context
from stock_screening.models.outputs import ScreeningResponse
from stock_screening.models.types import AgentType

from stock_screening.tools import stock_screener
from stock_screening.tools.stock_screener import ProfileSection

logger = get_logger(__name__)

# System prompt for the screening agent
SCREENING_SYSTEM_PROMPT = """You are a stock screening assistant for Indian NSE500 stocks.
You translate natural language requests into filter parameters and use your tools to find matching stocks.

Refer to the screen_stocks tool description for available filters, their semantics, and common strategies (VALUE, GROWTH, QUALITY, GARP, RELATIVE VALUE). All filter parameters are described in that tool.

Use get_stock_details with a list of symbols (e.g. ['TCS', 'INFY']) to fetch details for one or more companies.
Use it after screening to compare top picks or check competitors.

Always show what filters you applied so users can refine.
"""


screening_agent = Agent(
    get_model(AgentType.SCREENING),
    deps_type=AgentDeps,
    system_prompt=SCREENING_SYSTEM_PROMPT,
    output_type=ScreeningResponse,
)

# Register screen_stocks directly (has Annotated Field descriptions + docstring with strategies)
screening_agent.tool_plain(stock_screener.screen_stocks)

# Add default context (datetime, etc.) after tools are registered
with_default_context(screening_agent)


@screening_agent.tool
def get_sectors(ctx: RunContext[None]) -> str:
    """Get list of all available sectors and company counts."""
    logger.info("Tool: get_sectors called")
    result = stock_screener.list_sectors()
    logger.debug("Tool: get_sectors returned %d chars", len(result))
    return result


@screening_agent.tool
def get_industries(ctx: RunContext[None]) -> str:
    """Get list of all available industries and company counts."""
    logger.info("Tool: get_industries called")
    result = stock_screener.get_screener_metadata()
    logger.debug("Tool: get_industries returned %d chars", len(result))
    return result


@screening_agent.tool
def list_companies_in_industry(ctx: RunContext[None], industry: str) -> str:
    """Get list of all companies (symbols) in a specific industry."""
    logger.info("Tool: list_companies_in_industry called with industry=%s", industry)
    result = stock_screener.list_companies_in_industry(industry)
    logger.debug("Tool: list_companies_in_industry returned %d chars", len(result))
    return result


@screening_agent.tool
def get_stock_details(
    ctx: RunContext[None],
    symbols: list[str],
    sections: list[ProfileSection] | None = None,
) -> str:
    """Get detailed profiles for one or more stocks.
    
    Args:
        symbols: List of stock symbols (e.g. ['TCS', 'INFY'])
        sections: Optional list of profile sections to include. Options: basic, valuation,
            profitability, financial_health, size, dividends, growth, historical, insights,
            comparison. None returns all sections.
    """
    logger.info("Tool: get_stock_details called with symbols=%s, sections=%s", symbols, sections)
    result = stock_screener.get_companies(symbols, sections=sections)  # type: ignore[arg-type]
    logger.debug("Tool: get_stock_details returned %d chars", len(result))
    return result


# =============================================================================
# Standalone screening (no LLM)
# =============================================================================

def run_screen(query_params: dict) -> str:
    """
    Run screening with a dict of parameters (for non-LLM usage).
    """
    return stock_screener.screen_stocks(**query_params)


if __name__ == "__main__":
    import asyncio
    import sys

    from stock_screening.logging_config import setup_logging

    async def main():
        # Setup logging - use DEBUG for more details
        setup_logging(level="INFO")
        logger.info("Screening agent starting")

        # If query passed as argument, use that; otherwise interactive mode
        if len(sys.argv) > 1:
            query = " ".join(sys.argv[1:])
            logger.info("Running single query: %s", query[:100])
            print(f"Query: {query}\n")
            result = await llm_service.run_agent(screening_agent, query)
            logger.info("Query completed, output length: %d", len(result.output.message))
            print(f"Completed: {result.output.completed}")
            print(f"Message: {result.output.message}")
            if result.output.applied_filters:
                print(f"Filters: {result.output.applied_filters}")
            if result.output.follow_up_suggestion:
                print(f"Follow-up: {result.output.follow_up_suggestion}")
        else:
            # Interactive loop with conversation history
            print("Screening Agent (type 'quit' to exit)\n")
            message_history: list = []
            while True:
                query = input("You: ").strip()
                if not query or query.lower() in ("quit", "exit", "q"):
                    break
                logger.info("User query: %s", query[:100])
                result = await llm_service.run_agent(screening_agent, query, message_history=message_history)
                message_history = result.all_messages()  # carry forward
                logger.info("Response received, completed=%s", result.output.completed)
                print(f"\nAgent: {result.output.message}")
                if result.output.applied_filters:
                    print(f"[Filters: {result.output.applied_filters}]")
                if result.output.follow_up_suggestion:
                    print(f"[Suggestion: {result.output.follow_up_suggestion}]")
                print()

    asyncio.run(main())

