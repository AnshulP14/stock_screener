"""
Main Orchestrator

Routes queries to either web search or screening sub-agent.
Uses LLM to decide agent, task description, and success criteria.

Run standalone:
    python -m src.stock_screening.agents.main_agent
"""

from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Literal

from pydantic_ai import Agent

from stock_screening.agents.base import AgentDeps
from stock_screening.logging_config import get_logger
from stock_screening.models.constants import (
    CONVERSATION_CONTEXT_MESSAGES,
    MAX_ITERATIONS,
    SUB_AGENT_CONTEXT_MAX_CHARS,
    SYNTHESIS_CONTEXT_MAX_CHARS,
)
from stock_screening.models.llm import get_model, llm_service, with_default_context
from stock_screening.models.outputs import MainResponse, RoutingDecision, SynthesisOutput
from stock_screening.models.types import AgentType

logger = get_logger(__name__)


# -----------------------------------------------------------------------------
# Chat Events and Formatting (for UI consumption)
# -----------------------------------------------------------------------------


@dataclass
class ToolCallData:
    """Data for a single tool call including args and result."""

    name: str
    args: dict[str, Any] = field(default_factory=dict)
    tool_call_id: str = ""
    start_time: float = 0.0
    end_time: float | None = None
    result: str | None = None  # Truncated result


@dataclass
class TraceData:
    """Aggregated trace data for token usage, timing, and cost (with step-wise breakdown)."""

    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    duration_ms: float = 0.0
    cost_usd: float = 0.0  # Total estimated cost
    model_id: str = ""  # Primary model (empty if merged)
    # Step-wise cost (USD) for UI breakdown
    cost_router_usd: float = 0.0
    cost_agent_usd: float = 0.0
    cost_synthesis_usd: float = 0.0
    # Step-wise token counts (so UI can show tokens per step)
    router_input_tokens: int = 0
    router_output_tokens: int = 0
    agent_input_tokens: int = 0
    agent_output_tokens: int = 0
    synthesis_input_tokens: int = 0
    synthesis_output_tokens: int = 0


@dataclass
class ChatEvent:
    """Typed event for UI consumption from chat_stream()."""

    type: Literal["status", "tool", "final"]
    content: str = ""
    name: str = ""  # Tool name for type="tool"
    tool_data: ToolCallData | None = None  # Full tool call data
    trace: TraceData | None = None  # Trace data for final events
    tool_results: dict[str, str] = field(default_factory=dict)  # Results by tool_call_id


def format_routing_status(decision: RoutingDecision) -> str:
    """Format routing decision for display."""
    tasks_display = "\n".join(f"  - {t}" for t in decision.tasks)
    return (
        f"**📍 Route:** {decision.agent}  \n"
        f"**Tasks:**\n{tasks_display}  \n"
        f"**Success:** {decision.success_criteria}"
    )


def format_final_response(
    result: MainResponse,
    sources: list[dict[str, str]],
    filters: dict | None = None,
    routing_info: str = "",
) -> str:
    """Format final response with sources, filters, and suggestions for display."""
    # Build parts
    agent_label = f"**Agent:** {result.agent_used}" if result.agent_used else ""
    parts = [routing_info, agent_label, "", result.message]
    parts = [p for p in parts if p]

    # Add filters if available (from screening)
    if filters:
        filter_lines = ["### 🔍 Filters Applied", ""]
        for key, value in filters.items():
            filter_lines.append(f"- **{key}:** {value}")
        parts.append("\n".join(filter_lines))

    # Add sources if available (from web search)
    if sources:
        source_lines = ["### 🔗 Sources", ""]
        for src in sources:
            source_lines.append(f"- [{src['title']}]({src['url']})")
        parts.append("\n".join(source_lines))

    # Add follow-up suggestion
    if result.follow_up_suggestion:
        parts.append("")
        parts.append("---")
        parts.append("")
        parts.append(f"**💡 Suggestion:** {result.follow_up_suggestion}")

    return "\n\n".join(parts)


async def chat_stream(
    deps: AgentDeps,
    user_message: str,
    history: list[dict],
) -> AsyncIterator[ChatEvent]:
    """
    High-level chat API for UI consumption.
    
    Yields ChatEvent objects for routing status, tool calls, and final response.
    Handles conversation context extraction internally.
    
    Args:
        deps: Agent dependencies (maintains history state)
        user_message: The user's message
        history: Chat history (list of {role, content} dicts)
    
    Yields:
        ChatEvent with type 'status', 'tool', or 'final'
    """
    import time

    # Extract conversation context (excluding current message)
    conversation_context = history[:-1][-CONVERSATION_CONTEXT_MESSAGES:] if history else []

    routing_info = ""
    result = None
    trace_data = None
    tool_results: dict[str, str] = {}

    # Track tool calls with full data
    tool_calls: dict[str, ToolCallData] = {}

    async for event in route_query_stream(deps, user_message, conversation_context=conversation_context):
        if event["type"] == "routing":
            routing_info = format_routing_status(event["decision"])
            yield ChatEvent(type="status", content=routing_info)

        elif event["type"] == "tool_call":
            # Create ToolCallData with args and timing
            tool_call_id = event.get("tool_call_id", "")
            tool_data = ToolCallData(
                name=event["name"],
                args=event.get("args", {}),
                tool_call_id=tool_call_id,
                start_time=event.get("timestamp", time.time()),
            )
            tool_calls[tool_call_id] = tool_data
            yield ChatEvent(type="tool", name=event["name"], tool_data=tool_data)

        elif event["type"] == "final":
            result = event["response"]
            trace_data = event.get("trace")
            tool_results = event.get("tool_results", {})

    if result:
        # Update tool calls with results and end times
        end_time = time.time()
        for tool_call_id, tool_data in tool_calls.items():
            tool_data.end_time = end_time
            if tool_call_id in tool_results:
                tool_data.result = tool_results[tool_call_id]

        # Get sources and filters, then clear them from deps
        sources = deps.last_sources.copy()
        deps.last_sources = []
        filters = deps.last_filters
        deps.last_filters = None

        # Create TraceData if available (cost may be precomputed in merged trace)
        trace = None
        if trace_data:
            cost_usd = trace_data.get("cost_usd", 0.0)
            model_id = trace_data.get("model_id", "")
            if not cost_usd and model_id:
                cost_usd = _cost_for_usage(
                    {
                        "input_tokens": trace_data.get("input_tokens", 0),
                        "output_tokens": trace_data.get("output_tokens", 0),
                    },
                    model_id,
                )
            trace = TraceData(
                input_tokens=trace_data.get("input_tokens", 0),
                output_tokens=trace_data.get("output_tokens", 0),
                total_tokens=trace_data.get("total_tokens", 0),
                duration_ms=trace_data.get("duration_ms", 0),
                cost_usd=cost_usd,
                model_id=model_id,
                cost_router_usd=trace_data.get("cost_router_usd", 0.0),
                cost_agent_usd=trace_data.get("cost_agent_usd", 0.0),
                cost_synthesis_usd=trace_data.get("cost_synthesis_usd", 0.0),
                router_input_tokens=trace_data.get("router_input_tokens", 0),
                router_output_tokens=trace_data.get("router_output_tokens", 0),
                agent_input_tokens=trace_data.get("agent_input_tokens", 0),
                agent_output_tokens=trace_data.get("agent_output_tokens", 0),
                synthesis_input_tokens=trace_data.get("synthesis_input_tokens", 0),
                synthesis_output_tokens=trace_data.get("synthesis_output_tokens", 0),
            )

        final_content = format_final_response(result, sources, filters, routing_info)
        yield ChatEvent(type="final", content=final_content, trace=trace, tool_results=tool_results)

ROUTER_SYSTEM_PROMPT = """You are a query router for a stock research assistant (Indian NSE/BSE).

You receive "Recent conversation" and "Current user message". From the conversation, create a concrete list of tasks so the sub-agent can execute without seeing the full chat.

Rules:
- Use conversation context to make tasks specific: include company names, metrics, anomalies, and themes already discussed.
- For verification/cross-check (e.g. "verify", "cross-check", "confirm"): Read the assistant's prior response and extract exact numbers and metrics (e.g. P/E 23.15, ROE 32.7%, revenue growth 3.2%). Create web_search tasks that mention these numbers so the search can validate them. Example: ["Search: Is Infosys (INFY) P/E ratio 23.15 in 2024?", "Search: Verify Infosys ROE 32.7% and revenue growth 3.2%"]—not generic "Verify Infosys financial metrics".
- Each task must be a good search query or screening instruction on its own.

Which agent:
1. **screening** - Fundamentals: PE, ROE, EPS, market cap, sector filters.
2. **web_search** - News, events, earnings, policy; cross-verification (use specific numbers from context in tasks).
"""


# -----------------------------------------------------------------------------
# Router Agent
# -----------------------------------------------------------------------------

router_agent = with_default_context(Agent(
    get_model("router"),
    system_prompt=ROUTER_SYSTEM_PROMPT,
    output_type=RoutingDecision,
))

SYNTHESIS_SYSTEM_PROMPT = """You are the response synthesis agent for a stock research assistant (Indian NSE/BSE).

You receive:
1. Recent conversation (prior turns only—the current user request and raw agent output are provided separately below)
2. The current user request
3. The task that was executed and which sub-agent was used
4. The raw output from that sub-agent (web search or screening)

Your job: Write a single, clear, well-formatted final response to the user. Use the conversation to sound natural (e.g. refer to what was discussed). Preserve all key facts, numbers, companies, and conclusions. Use markdown for structure (headings, bold, lists) where helpful. Do not add facts not in the agent output. Do not repeat the user's question or the raw output verbatim—synthesize. Sources will be listed separately by the UI."""

synthesis_agent = with_default_context(Agent(
    get_model("synthesis"),
    system_prompt=SYNTHESIS_SYSTEM_PROMPT,
    output_type=SynthesisOutput,
))


def _extract_text(content: object) -> str:
    """Extract plain text from Gradio/API message content (str, list of str, or list of dicts with text/content)."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts = []
        for x in content:
            if isinstance(x, str):
                parts.append(x)
            elif isinstance(x, dict):
                parts.append((x.get("text") or x.get("content") or ""))
            else:
                parts.append(str(x))
        return " ".join(str(p).strip() for p in parts if p).strip()
    return (str(content) or "").strip()


def _format_conversation_context(
    messages: list,
    *,
    max_chars: int | None = None,
    per_message_chars: int = 500,
) -> str:
    """Format recent chat turns. Handles Gradio: list of {role, content} or list of [user, assistant] tuples.
    If max_chars is set, the result is truncated to that length (with '... [truncated]' suffix)."""
    lines = []
    for m in messages:
        role = "User"
        raw = None
        if isinstance(m, dict):
            role = (m.get("role") or "user").capitalize()
            raw = m.get("content")
        elif isinstance(m, (list, tuple)) and len(m) >= 2:
            lines.append(f"User: {_extract_text(m[0])[:per_message_chars]}" + ("..." if len(_extract_text(m[0])) > per_message_chars else ""))
            lines.append(f"Assistant: {_extract_text(m[1])[:per_message_chars]}" + ("..." if len(_extract_text(m[1])) > per_message_chars else ""))
            continue
        content = _extract_text(raw)
        if not content:
            continue
        lines.append(f"{role}: {content[:per_message_chars]}" + ("..." if len(content) > per_message_chars else ""))
    out = "\n".join(lines) if lines else ""
    if max_chars and len(out) > max_chars:
        out = out[:max_chars] + "\n... [truncated]"
    return out


async def classify_intent(
    query: str,
    conversation_context: list[dict] | None = None,
) -> RoutingDecision:
    """Use LLM to classify query and generate task/success criteria. Optional context from prior turns."""
    prompt = query
    if conversation_context:
        ctx = _format_conversation_context(conversation_context)
        prompt = f"Recent conversation:\n{ctx}\n\nCurrent user message: {query}"
        if ctx:
            logger.info("Router context (%d messages): %s", len(conversation_context), ctx[:500] + ("..." if len(ctx) > 500 else ""))
        else:
            # Content may be non-text (e.g. images) or wrong shape
            summary = []
            for m in conversation_context:
                if isinstance(m, dict):
                    summary.append(f"role={m.get('role')} content_type={type(m.get('content')).__name__}")
                else:
                    summary.append(f"type={type(m).__name__} len={getattr(m, '__len__', lambda: 0)()}")
            logger.info("Router context (%d messages): no extractable text; %s", len(conversation_context), summary)
    else:
        logger.info("Router context: none (no conversation history)")
    result = await llm_service.run_agent(router_agent, prompt)
    return result.output, _usage_from_result(result)


# -----------------------------------------------------------------------------
# Sub-agent runners
# -----------------------------------------------------------------------------


def _conversation_summary_for_sub_agent(conversation_context: list[dict] | None) -> str | None:
    """Build a short prior-context summary for sub-agents (screening/web_search). None if no context."""
    if not conversation_context:
        return None
    summary = _format_conversation_context(
        conversation_context, max_chars=SUB_AGENT_CONTEXT_MAX_CHARS
    )
    return summary if summary.strip() else None


def _decision_to_prompt(
    decision: RoutingDecision,
    prior_context: str | None = None,
) -> str:
    """Build agent prompt from decision tasks list. Optionally prepend prior conversation summary."""
    if len(decision.tasks) == 1:
        task_block = decision.tasks[0]
    else:
        task_block = "Do the following:\n" + "\n".join(f"{i+1}. {t}" for i, t in enumerate(decision.tasks))
    body = f"{task_block}\n\nSuccess criteria: {decision.success_criteria}"
    if prior_context:
        return f"Relevant prior conversation:\n{prior_context}\n\n{body}"
    return body


async def _run_agent_loop(
    agent,
    base_prompt: str,
    message_history: list,
    agent_name: str,
    max_iterations: int = MAX_ITERATIONS,
    model_settings: dict | None = None,
    continue_prefix: str = "Continue:",
):
    """
    Shared iteration logic for sub-agents.

    Args:
        agent: The Pydantic AI agent to run
        base_prompt: The initial prompt from the routing decision
        message_history: Initial message history
        agent_name: Name for logging
        max_iterations: Maximum iteration count
        model_settings: Optional model settings (e.g., for web search)
        continue_prefix: Prefix for continuation prompts

    Returns:
        Tuple of (output, message_history)
    """
    output = None
    kwargs = {"message_history": message_history}
    if model_settings:
        kwargs["model_settings"] = model_settings

    for i in range(max_iterations):
        logger.info("%s iteration %d/%d", agent_name, i + 1, max_iterations)

        if i == 0:
            prompt = base_prompt
        else:
            follow_up = output.follow_up_suggestion or ""
            prompt = f"{continue_prefix} {base_prompt}. {follow_up}"

        result = await llm_service.run_agent(agent, prompt, **kwargs)
        message_history = result.all_messages()
        kwargs["message_history"] = message_history
        output = result.output

        if output.completed:
            logger.info("%s completed after %d iterations", agent_name, i + 1)
            break

        if not output.follow_up_suggestion:
            logger.warning("%s not completed but no follow_up_suggestion", agent_name)
            break

    return output, message_history


async def run_screening_agent(
    deps: AgentDeps,
    decision: RoutingDecision,
    conversation_summary: str | None = None,
    max_iterations: int = MAX_ITERATIONS,
) -> MainResponse:
    """Run screening sub-agent for fundamental metrics analysis."""
    from stock_screening.agents.screening_agent import screening_agent

    prompt = _decision_to_prompt(decision, prior_context=conversation_summary)
    logger.info("Running screening_agent: %s", prompt[:100])

    output, message_history = await _run_agent_loop(
        agent=screening_agent,
        base_prompt=prompt,
        message_history=deps.screening_history,
        agent_name="screening_agent",
        max_iterations=max_iterations,
    )
    deps.screening_history = message_history
    deps.last_filters = output.applied_filters

    return MainResponse(
        message=output.message,
        agent_used=AgentType.SCREENING,
        follow_up_suggestion=output.follow_up_suggestion,
        routing_decision=decision,
    )


def _get_stream_model_id(agent_key: str) -> str:
    """Return model ID used for cost lookup (screening=claude, web_search=openai_responses)."""
    from stock_screening.config import get_settings

    s = get_settings()
    if agent_key == "web_search":
        return s.llm_openai_responses_model_id
    return s.llm_anthropic_model_id  # screening


def _usage_from_result(result: Any) -> dict:
    """Extract input/output token counts from a Pydantic AI run result."""
    usage = getattr(result, "usage", lambda: None)()
    if not usage:
        return {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
    in_tok = (
        getattr(usage, "input_tokens", 0)
        or getattr(usage, "request_tokens", 0)
        or getattr(usage, "prompt_tokens", 0)
    )
    out_tok = (
        getattr(usage, "output_tokens", 0)
        or getattr(usage, "response_tokens", 0)
        or getattr(usage, "completion_tokens", 0)
    )
    total = getattr(usage, "total_tokens", None)
    if total is None:
        total = in_tok + out_tok
    return {"input_tokens": in_tok, "output_tokens": out_tok, "total_tokens": total}


def _cost_for_usage(usage: dict, model_id: str) -> float:
    """Compute USD cost for a usage dict using genai-prices (same source as Pydantic AI)."""
    if not model_id or not usage:
        return 0.0
    in_tok = usage.get("input_tokens", 0) or 0
    out_tok = usage.get("output_tokens", 0) or 0
    if not in_tok and not out_tok:
        return 0.0
    try:
        from genai_prices import Usage, calc_price
        u = Usage(input_tokens=in_tok, output_tokens=out_tok)
        result = calc_price(u, model_id)
        return float(result.total_price)
    except (LookupError, Exception) as e:
        logger.debug("Cost lookup failed for %s: %s", model_id, e)
        return 0.0


async def run_screening_agent_stream(
    deps: AgentDeps,
    decision: RoutingDecision,
    conversation_summary: str | None = None,
    max_iterations: int = MAX_ITERATIONS,
):
    """Run screening sub-agent with streaming - yields tool calls as they happen."""
    from stock_screening.agents.screening_agent import screening_agent

    prompt = _decision_to_prompt(decision, prior_context=conversation_summary)
    logger.info("Running screening_agent (streaming): %s", prompt[:100])

    message_history = deps.screening_history
    output = None

    # Aggregate trace data across iterations
    total_input_tokens = 0
    total_output_tokens = 0
    total_duration_ms = 0.0
    tool_results: dict[str, str] = {}

    for i in range(max_iterations):
        logger.info("screening_agent iteration %d/%d", i + 1, max_iterations)

        iter_prompt = prompt if i == 0 else f"Continue: {prompt}. {output.follow_up_suggestion or ''}"

        async for event in llm_service.run_agent_stream(screening_agent, iter_prompt, message_history=message_history):
            if event["type"] == "result":
                message_history = event["messages"]
                output = event["output"]
                # Aggregate usage
                usage = event.get("usage", {})
                total_input_tokens += usage.get("input_tokens", 0)
                total_output_tokens += usage.get("output_tokens", 0)
                total_duration_ms += event.get("duration_ms", 0)
                tool_results.update(event.get("tool_results", {}))
            else:
                yield event

        if output and output.completed:
            logger.info("screening_agent completed after %d iterations", i + 1)
            break

    deps.screening_history = message_history
    deps.last_filters = output.applied_filters if output else None

    model_id = _get_stream_model_id("screening")
    yield {
        "type": "final",
        "response": MainResponse(
            message=output.message,
            agent_used=AgentType.SCREENING,
            follow_up_suggestion=output.follow_up_suggestion,
            routing_decision=decision,
        ),
        "trace": {
            "input_tokens": total_input_tokens,
            "output_tokens": total_output_tokens,
            "total_tokens": total_input_tokens + total_output_tokens,
            "duration_ms": total_duration_ms,
            "model_id": model_id,
        },
        "tool_results": tool_results,
    }


async def run_web_search_agent(
    deps: AgentDeps,
    decision: RoutingDecision,
    conversation_summary: str | None = None,
    max_iterations: int = MAX_ITERATIONS,
) -> MainResponse:
    """Run web search sub-agent for news and recent events. Uses task list from main agent only."""
    from stock_screening.agents.web_search_agent import (
        extract_sources_from_messages,
        web_search_agent,
    )
    from stock_screening.models.constants import WEB_SEARCH_MODEL_SETTINGS

    prompt = _decision_to_prompt(decision, prior_context=conversation_summary)
    logger.info("Running web_search_agent: %s", prompt[:100])

    output, message_history = await _run_agent_loop(
        agent=web_search_agent,
        base_prompt=prompt,
        message_history=deps.web_search_history,
        agent_name="web_search_agent",
        max_iterations=max_iterations,
        model_settings=WEB_SEARCH_MODEL_SETTINGS,
        continue_prefix="Continue: Search for:",
    )
    deps.web_search_history = message_history

    # Extract sources and store in deps
    sources = extract_sources_from_messages(message_history)
    deps.last_sources = sources

    message = _format_web_search_output(output)

    return MainResponse(
        message=message,
        agent_used=AgentType.WEB_SEARCH,
        follow_up_suggestion=output.follow_up_suggestion,
        routing_decision=decision,
    )


def _format_web_search_output(output) -> str:
    """Format web search output into markdown."""
    lines = []
    if output.news_items:
        lines.append("### 📰 News")
        lines.append("")
        for i, item in enumerate(output.news_items, 1):
            lines.append(f"**{i}. {item.what_happened}**")
            if item.event_time_ist:
                lines.append(f"   🕐 {item.event_time_ist}")
            lines.append(f"   💡 *Why it matters:* {item.why_it_matters}")
            if item.how_did_customers_react:
                lines.append(f"   📈 *Market reaction:* {item.how_did_customers_react}")
            lines.append("")
        lines.append("---")
        lines.append("")
    if output.overall_market_reaction:
        lines.append("### 📊 Overall market reaction")
        lines.append("")
        lines.append(output.overall_market_reaction)
        lines.append("")
    if output.analyst_commentary:
        lines.append("### 🎙️ Analyst commentary")
        lines.append("")
        lines.append(output.analyst_commentary)
        lines.append("")
    return "\n".join(lines).strip() if lines else "*No news found.*"


async def run_web_search_agent_stream(
    deps: AgentDeps,
    decision: RoutingDecision,
    conversation_summary: str | None = None,
    max_iterations: int = MAX_ITERATIONS,
):
    """Run web search sub-agent with streaming - yields tool calls as they happen."""
    from stock_screening.agents.web_search_agent import (
        extract_sources_from_messages,
        web_search_agent,
    )
    from stock_screening.models.constants import WEB_SEARCH_MODEL_SETTINGS

    full_query = _decision_to_prompt(decision, prior_context=conversation_summary)
    logger.info("Running web_search_agent (streaming): %s", full_query[:100])

    message_history = deps.web_search_history
    output = None

    # Aggregate trace data across iterations
    total_input_tokens = 0
    total_output_tokens = 0
    total_duration_ms = 0.0
    tool_results: dict[str, str] = {}

    for i in range(max_iterations):
        logger.info("web_search_agent iteration %d/%d", i + 1, max_iterations)

        prompt = full_query if i == 0 else f"Continue: {full_query}. Search for: {output.follow_up_suggestion}"

        async for event in llm_service.run_agent_stream(
            web_search_agent,
            prompt,
            message_history=message_history,
            model_settings=WEB_SEARCH_MODEL_SETTINGS,
        ):
            if event["type"] == "result":
                message_history = event["messages"]
                output = event["output"]
                # Aggregate usage
                usage = event.get("usage", {})
                total_input_tokens += usage.get("input_tokens", 0)
                total_output_tokens += usage.get("output_tokens", 0)
                total_duration_ms += event.get("duration_ms", 0)
                tool_results.update(event.get("tool_results", {}))
            else:
                yield event

        if output and output.completed:
            logger.info("web_search_agent completed after %d iterations", i + 1)
            break

        if output and not output.follow_up_suggestion:
            logger.warning("web_search_agent not completed but no follow_up_suggestion")
            break

    deps.web_search_history = message_history
    sources = extract_sources_from_messages(message_history)
    deps.last_sources = sources

    message = _format_web_search_output(output)

    model_id = _get_stream_model_id("web_search")
    yield {
        "type": "final",
        "response": MainResponse(
            message=message,
            agent_used=AgentType.WEB_SEARCH,
            follow_up_suggestion=output.follow_up_suggestion,
            routing_decision=decision,
        ),
        "trace": {
            "input_tokens": total_input_tokens,
            "output_tokens": total_output_tokens,
            "total_tokens": total_input_tokens + total_output_tokens,
            "duration_ms": total_duration_ms,
            "model_id": model_id,
        },
        "tool_results": tool_results,
    }


# -----------------------------------------------------------------------------
# Synthesis (main agent produces final response from sub-agent output)
# -----------------------------------------------------------------------------


async def synthesize_response(
    query: str,
    decision: RoutingDecision,
    agent_output: str,
    agent_used: str,
    conversation_context: list[dict] | None = None,
) -> tuple[str, dict]:
    """Main agent synthesizes sub-agent output into the final user-facing response. Returns (message, usage_dict)."""
    parts = []
    if conversation_context:
        ctx = _format_conversation_context(conversation_context, max_chars=SYNTHESIS_CONTEXT_MAX_CHARS)
        if ctx:
            parts.append(f"Recent conversation (prior turns only):\n{ctx}\n")
    parts.append(f"Current user request: {query}\n")
    tasks_str = "; ".join(decision.tasks)
    parts.append(f"Tasks executed: {tasks_str}\nAgent used: {agent_used}\n")
    parts.append("Raw output from agent:\n\n")
    parts.append(agent_output)
    parts.append("\n\nWrite the final response to the user.")
    prompt = "\n".join(parts)
    logger.info("Synthesis agent running: query=%s... agent_used=%s", query[:60], agent_used)
    result = await llm_service.run_agent(synthesis_agent, prompt)
    return result.output.message.strip(), _usage_from_result(result)


# -----------------------------------------------------------------------------
# Main router
# -----------------------------------------------------------------------------


async def execute_decision(
    deps: AgentDeps,
    decision: RoutingDecision,
    conversation_summary: str | None = None,
) -> MainResponse:
    """Execute the routing decision by calling the appropriate agent."""
    logger.info("Executing decision: agent=%s, tasks=%s", decision.agent, decision.tasks[:2])

    if decision.agent == AgentType.WEB_SEARCH:
        return await run_web_search_agent(deps, decision, conversation_summary=conversation_summary)
    else:
        return await run_screening_agent(deps, decision, conversation_summary=conversation_summary)


async def route_query(
    deps: AgentDeps,
    query: str,
    conversation_context: list[dict] | None = None,
) -> MainResponse:
    """Classify, execute sub-agent, then synthesize final response in the main agent."""
    decision, _ = await classify_intent(query, conversation_context=conversation_context)
    summary = _conversation_summary_for_sub_agent(conversation_context)
    result = await execute_decision(deps, decision, conversation_summary=summary)
    result.message, _ = await synthesize_response(
        query, decision, result.message, result.agent_used or "",
        conversation_context=conversation_context,
    )
    return result


async def route_query_stream(
    deps: AgentDeps,
    query: str,
    conversation_context: list[dict] | None = None,
):
    """Streaming version of route_query - yields tool calls as they happen.

    Yields:
        dict with 'type': 'routing', 'tool_call', 'final' and trace data (total LLM cost).
    """
    from stock_screening.config import get_settings

    decision, router_usage = await classify_intent(query, conversation_context=conversation_context)
    yield {"type": "routing", "decision": decision}

    summary = _conversation_summary_for_sub_agent(conversation_context)
    if decision.agent == AgentType.WEB_SEARCH:
        runner = run_web_search_agent_stream(deps, decision, conversation_summary=summary)
    else:
        runner = run_screening_agent_stream(deps, decision, conversation_summary=summary)

    result = None
    trace_data = None
    tool_results: dict[str, str] = {}

    async for event in runner:
        if event["type"] == "final":
            result = event["response"]
            trace_data = event.get("trace")
            tool_results = event.get("tool_results", {})
        else:
            yield event

    # Synthesize final response and merge trace (router + agent + synthesis = total cost)
    if result:
        result.message, synthesis_usage = await synthesize_response(
            query, decision, result.message, result.agent_used or "",
            conversation_context=conversation_context,
        )

        # Merge all LLM usage for total cost
        s = get_settings()
        router_model = s.llm_router_model_id
        agent_in = (trace_data or {}).get("input_tokens", 0)
        agent_out = (trace_data or {}).get("output_tokens", 0)
        router_in = router_usage.get("input_tokens", 0)
        router_out = router_usage.get("output_tokens", 0)
        syn_in = synthesis_usage.get("input_tokens", 0)
        syn_out = synthesis_usage.get("output_tokens", 0)

        total_input = router_in + agent_in + syn_in
        total_output = router_out + agent_out + syn_out
        agent_model = (trace_data or {}).get("model_id", "")

        cost_router = _cost_for_usage(router_usage, router_model)
        cost_agent = _cost_for_usage(
            {"input_tokens": agent_in, "output_tokens": agent_out}, agent_model
        )
        cost_synthesis = _cost_for_usage(synthesis_usage, router_model)
        total_cost = cost_router + cost_agent + cost_synthesis

        merged_trace = {
            "input_tokens": total_input,
            "output_tokens": total_output,
            "total_tokens": total_input + total_output,
            "duration_ms": (trace_data or {}).get("duration_ms", 0),
            "model_id": "",
            "cost_usd": total_cost,
            "cost_router_usd": cost_router,
            "cost_agent_usd": cost_agent,
            "cost_synthesis_usd": cost_synthesis,
            "router_input_tokens": router_in,
            "router_output_tokens": router_out,
            "agent_input_tokens": agent_in,
            "agent_output_tokens": agent_out,
            "synthesis_input_tokens": syn_in,
            "synthesis_output_tokens": syn_out,
        }
        yield {
            "type": "final",
            "response": result,
            "trace": merged_trace,
            "tool_results": tool_results,
        }


# -----------------------------------------------------------------------------
# Standalone Runner
# -----------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse
    import asyncio

    from stock_screening.logging_config import setup_logging

    async def main() -> None:
        parser = argparse.ArgumentParser(description="Stock research assistant")
        parser.add_argument(
            "query", nargs="*", help="Query (optional, enters interactive mode if not provided)"
        )
        args = parser.parse_args()

        setup_logging(level="INFO")
        logger.info("Main agent starting")

        deps = AgentDeps()

        if args.query:
            # Single query mode
            query = " ".join(args.query)
            logger.info("Running single query: %s", query[:100])
            print(f"Query: {query}\n")

            result = await route_query(deps, query)
            print("\n" + "-" * 50)
            print(result.message)
            print("-" * 50)
            if result.agent_used:
                print(f"  Agent: {result.agent_used}")
            if result.follow_up_suggestion:
                print(f"  Suggestion: {result.follow_up_suggestion}")
            print()
        else:
            # Interactive mode
            print("=" * 60)
            print("Stock Research Assistant")
            print("Commands: 'quit' to exit, 'clear' to reset all history")
            print("=" * 60)
            print()

            cli_history: list[dict] = []
            while True:
                try:
                    query = input("You: ").strip()
                except EOFError:
                    break

                if not query:
                    continue
                if query.lower() in ("quit", "exit", "q"):
                    break
                if query.lower() == "clear":
                    deps = AgentDeps()
                    cli_history = []
                    print("[All history cleared]\n")
                    continue

                logger.info("User query: %s", query[:100])

                try:
                    result = await route_query(
                        deps, query, conversation_context=cli_history[-CONVERSATION_CONTEXT_MESSAGES:]
                    )
                    cli_history.append({"role": "user", "content": query})
                    cli_history.append({"role": "assistant", "content": result.message})
                    print("\n" + "-" * 50)
                    print("Assistant:\n")
                    print(result.message)
                    print("-" * 50)
                    if result.agent_used:
                        print(f"  Agent: {result.agent_used}")
                    if result.follow_up_suggestion:
                        print(f"  Suggestion: {result.follow_up_suggestion}")
                    print()
                except Exception as e:
                    logger.error("Error: %s", e)
                    print(f"\n[Error: {e}]\n")

    asyncio.run(main())
