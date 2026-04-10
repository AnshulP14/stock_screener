"""Structured outputs from each Pydantic AI agent."""

from typing import Any, Literal, Optional, Union

from pydantic import BaseModel, Field, field_validator

from stock_screening.models.types import AgentType


# -----------------------------------------------------------------------------
# Base Agent Response
# -----------------------------------------------------------------------------


class BaseAgentResponse(BaseModel):
    """Base class for all agent responses with common fields."""

    completed: bool = Field(
        default=True,
        description="True when the output fulfils the user's success criteria. Set False if there are missing details or more information is needed."
    )
    message: str = Field(default="", description="Clear response to the user with results")
    follow_up_suggestion: Optional[str] = Field(
        default=None,
        description="Suggested next action if there's a natural follow-up (e.g. 'Compare the financials with the industry average')"
    )


# -----------------------------------------------------------------------------
# Screening Agent Output
# -----------------------------------------------------------------------------


class ScreeningResponse(BaseAgentResponse):
    """Structured final output from the screening agent."""

    applied_filters: Optional[dict[str, Any]] = Field(
        default=None,
        description="The screening filters that were applied (show users so they can refine the results)"
    )


# -----------------------------------------------------------------------------
# Web Search Agent Output
# -----------------------------------------------------------------------------


class NewsItem(BaseModel):
    """A single news item with inline citation."""

    what_happened: str = Field(
        description="Factual description of what happened, with inline [source.com] citation"
    )
    why_it_matters: str = Field(
        description="Brief explanation of why this matters for investors"
    )
    event_time_ist: Optional[str] = Field(
        default=None,
        description="Timestamp in IST format (e.g., '2026-02-01 11:30 IST') if available"
    )
    how_did_customers_react: Optional[str] = Field(
        default=None,
        description="Stock price or market reaction specific to this news item"
    )


class WebSearchResponse(BaseAgentResponse):
    """Structured output from web search agent."""

    news_items: list[NewsItem] = Field(
        ...,
        min_length=1,
        description="List of news items extracted from search results. MUST contain at least 1 item."
    )
    overall_market_reaction: Optional[str] = Field(
        default=None,
        description="Summary of overall market/index price and volume movements"
    )
    analyst_commentary: Optional[str] = Field(
        default=None,
        description="Expert opinions or analyst quotes (labeled as opinion, not fact)"
    )

    @field_validator("analyst_commentary", mode="before")
    @classmethod
    def _analyst_commentary_str(cls, v: Optional[Union[str, list[str]]]) -> Optional[str]:
        if v is None:
            return None
        if isinstance(v, list):
            return "\n".join(str(x) for x in v) if v else None
        return str(v) if v else None


# -----------------------------------------------------------------------------
# Routing Decision
# -----------------------------------------------------------------------------


class RoutingDecision(BaseModel):
    """LLM router decision for which agent to use."""

    agent: AgentType = Field(
        description="'screening' for fundamentals (PE, ROE, market cap); 'web_search' for news/events/verification"
    )
    tasks: list[str] = Field(
        description="Concrete tasks for the sub-agent. Each must work as a standalone search query or screening instruction."
    )
    success_criteria: str = Field(
        description="Actionable criteria for the agent to determine if the task is complete. The criteria should be specific and measurable."
    )


# -----------------------------------------------------------------------------
# Synthesis (main agent turns sub-agent output into final response)
# -----------------------------------------------------------------------------


class SynthesisOutput(BaseModel):
    """Main agent's synthesized response to the user."""

    message: str = Field(description="Clear, well-formatted final response to the user. The response should be concise and to the point, but comprehensive enough to answer the user's question.")


# -----------------------------------------------------------------------------
# Main Agent Output
# -----------------------------------------------------------------------------


class MainResponse(BaseModel):
    """Response from main orchestrator agent."""

    message: str = Field(description="Response to user with results. The response should be concise and to the point, but comprehensive enough to answer the user's question.")
    agent_used: Optional[AgentType] = Field(
        default=None, description="Which agent was used: 'screening' or 'web_search'"
    )
    follow_up_suggestion: Optional[str] = Field(
        default=None, description="Suggested next action"
    )
    routing_decision: Optional[RoutingDecision] = Field(
        default=None, description="The routing decision made by the LLM"
    )
