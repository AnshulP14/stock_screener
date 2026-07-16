"""Structured outputs for each agent. Field descriptions are prompts — the LLM reads them."""

from typing import Any, Optional, Union

from pydantic import BaseModel, Field, field_validator

from stock_screening.models.types import AgentType


class BaseAgentResponse(BaseModel):
    completed: bool = Field(
        default=True,
        description="True when the output fulfils the user's success criteria. Set False if there are missing details or more information is needed."
    )
    message: str = Field(default="", description="Clear response to the user with results")
    follow_up_suggestion: Optional[str] = Field(
        default=None,
        description="Suggested next action if there's a natural follow-up (e.g. 'Compare the financials with the industry average')"
    )


class ScreeningResponse(BaseAgentResponse):
    applied_filters: Optional[dict[str, Any]] = Field(
        default=None,
        description="The screening filters that were applied (show users so they can refine the results)"
    )


class NewsItem(BaseModel):
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
    def _join_commentary(cls, v: Optional[Union[str, list[str]]]) -> Optional[str]:
        """Models sometimes return a list here despite the str annotation."""
        if isinstance(v, list):
            return "\n".join(str(x) for x in v) if v else None
        return str(v) if v else None


class RoutingDecision(BaseModel):
    agent: AgentType = Field(
        description="'screening' for fundamentals (PE, ROE, market cap); 'web_search' for news/events/verification"
    )
    tasks: list[str] = Field(
        description="Concrete tasks for the sub-agent. Each must work as a standalone search query or screening instruction."
    )
    success_criteria: str = Field(
        description="Actionable criteria for the agent to determine if the task is complete. The criteria should be specific and measurable."
    )


class SynthesisOutput(BaseModel):
    message: str = Field(
        description="Clear, well-formatted final response to the user. The response should be concise and to the point, but comprehensive enough to answer the user's question."
    )


class MainResponse(BaseModel):
    """The router's response, returned to the API/CLI."""

    message: str = Field(description="Response to user with results.")
    agent_used: Optional[AgentType] = Field(default=None, description="Which agent produced this")
    follow_up_suggestion: Optional[str] = Field(default=None, description="Suggested next action")
    routing_decision: Optional[RoutingDecision] = Field(default=None, description="How the query was routed")
