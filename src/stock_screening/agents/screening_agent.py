"""Screening agent: turns natural language into screener filters and company lookups."""

from pydantic_ai import Agent, RunContext

from stock_screening.logging_config import get_logger
from stock_screening.models.context_providers import with_default_context
from stock_screening.models.llm_factory import get_model
from stock_screening.models.outputs import ScreeningResponse
from stock_screening.models.types import AgentType
from stock_screening.tools import stock_screener
from stock_screening.tools.stock_screener import ProfileSection

logger = get_logger(__name__)

SCREENING_SYSTEM_PROMPT = """You are a stock screening assistant for Indian NSE500 stocks.
You translate natural language requests into filter parameters and use your tools to find matching stocks.

Refer to the screen_stocks tool description for available filters, their semantics, and common strategies (VALUE, GROWTH, QUALITY, GARP, RELATIVE VALUE). All filter parameters are described in that tool.

Use get_stock_details with a list of symbols (e.g. ['TCS', 'INFY']) to fetch details for one or more companies.
Use it after screening to compare top picks or check competitors.

Always show what filters you applied so users can refine.
"""

# No deps_type: the tools below are pure reads of local data. AgentDeps holds
# message history, which main_agent threads through as message_history instead.
screening_agent = Agent(
    get_model(AgentType.SCREENING),
    system_prompt=SCREENING_SYSTEM_PROMPT,
    output_type=ScreeningResponse,
)

# screen_stocks carries its own Annotated filter docs, so register it as-is.
screening_agent.tool_plain(stock_screener.screen_stocks)


@screening_agent.tool
def get_sectors(ctx: RunContext[None]) -> str:
    """Get list of all available sectors and company counts."""
    logger.info("Tool: get_sectors")
    return stock_screener.list_sectors()


@screening_agent.tool
def get_industries(ctx: RunContext[None]) -> str:
    """Get list of all available industries and company counts."""
    logger.info("Tool: get_industries")
    return stock_screener.list_industries()


@screening_agent.tool
def list_companies_in_industry(ctx: RunContext[None], industry: str) -> str:
    """Get list of all companies (symbols) in a specific industry."""
    logger.info("Tool: list_companies_in_industry industry=%s", industry)
    return stock_screener.list_companies_in_industry(industry)


@screening_agent.tool
def get_stock_details(
    ctx: RunContext[None],
    symbols: list[str],
    sections: list[ProfileSection] | None = None,
) -> str:
    """Get detailed profiles for one or more stocks.

    Args:
        symbols: List of stock symbols (e.g. ['TCS', 'INFY'])
        sections: Optional profile sections. Options: basic, valuation, profitability,
            financial_health, size, dividends, growth, historical, insights, comparison,
            shareholding, credit_ratings. None returns all sections.
    """
    logger.info("Tool: get_stock_details symbols=%s sections=%s", symbols, sections)
    return stock_screener.get_companies(symbols, sections=sections)


# Context providers must be registered after the tools.
with_default_context(screening_agent)
