"""Router: classifies a query, runs one sub-agent, synthesizes the reply.

Run standalone: python -m stock_screening.agents.main_agent [query]
"""

from typing import Any

from pydantic_ai import Agent

from stock_screening.agents.base import AgentDeps
from stock_screening.config import get_settings
from stock_screening.logging_config import get_logger
from stock_screening.models.constants import (
    MAX_ITERATIONS,
    SUB_AGENT_CONTEXT_MAX_CHARS,
    SYNTHESIS_CONTEXT_MAX_CHARS,
    WEB_SEARCH_MODEL_SETTINGS,
)
from stock_screening.models.context_providers import with_default_context
from stock_screening.models.llm_factory import get_model
from stock_screening.models.llm_service import llm_service
from stock_screening.models.outputs import MainResponse, RoutingDecision, SynthesisOutput
from stock_screening.models.types import AgentType

logger = get_logger(__name__)

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

SYNTHESIS_SYSTEM_PROMPT = """You are the response synthesis agent for a stock research assistant (Indian NSE/BSE).

You receive:
1. Recent conversation (prior turns only)
2. The current user request
3. The task that was executed and which sub-agent was used
4. The raw output from that sub-agent (web search or screening)

CRITICAL RULES:
1. Your response MUST be faithful to the raw agent output. Do NOT contradict it or add your own knowledge.
2. PRESERVE ALL INLINE LINKS: The agent output contains markdown links like [[1]](url). You MUST keep these EXACTLY as-is in your response. Do NOT remove, reformat, or renumber them.

Your job: Reformat the agent output into a clear, user-friendly response. Preserve ALL facts, numbers, events, and inline citation links from the agent output. Use markdown for structure."""

router_agent = with_default_context(Agent(
    get_model("router"),
    system_prompt=ROUTER_SYSTEM_PROMPT,
    output_type=RoutingDecision,
))

synthesis_agent = with_default_context(Agent(
    get_model("synthesis"),
    system_prompt=SYNTHESIS_SYSTEM_PROMPT,
    output_type=SynthesisOutput,
))


def _format_conversation_context(
    messages: list[dict],
    *,
    max_chars: int | None = None,
    per_message_chars: int = 500,
) -> str:
    lines = []
    for m in messages:
        content = (m.get("content") or "").strip()
        if not content:
            continue
        role = (m.get("role") or "user").capitalize()
        suffix = "..." if len(content) > per_message_chars else ""
        lines.append(f"{role}: {content[:per_message_chars]}{suffix}")
    out = "\n".join(lines)
    if max_chars and len(out) > max_chars:
        out = out[:max_chars] + "\n... [truncated]"
    return out


def _usage_from_result(result: Any) -> dict:
    usage = getattr(result, "usage", lambda: None)()
    if not usage:
        return {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
    in_tok = getattr(usage, "input_tokens", 0) or getattr(usage, "request_tokens", 0) or 0
    out_tok = getattr(usage, "output_tokens", 0) or getattr(usage, "response_tokens", 0) or 0
    total = getattr(usage, "total_tokens", None)
    return {
        "input_tokens": in_tok,
        "output_tokens": out_tok,
        "total_tokens": in_tok + out_tok if total is None else total,
    }


def _cost_for_usage(usage: dict, model_id: str) -> float:
    in_tok = usage.get("input_tokens", 0) or 0
    out_tok = usage.get("output_tokens", 0) or 0
    if not model_id or not (in_tok or out_tok):
        return 0.0
    try:
        from genai_prices import Usage, calc_price

        return float(calc_price(Usage(input_tokens=in_tok, output_tokens=out_tok), model_id).total_price)
    except Exception as e:
        logger.debug("Cost lookup failed for %s: %s", model_id, e)
        return 0.0


async def classify_intent(
    query: str,
    conversation_context: list[dict] | None = None,
) -> tuple[RoutingDecision, dict]:
    prompt = query
    if conversation_context:
        ctx = _format_conversation_context(conversation_context)
        if ctx:
            prompt = f"Recent conversation:\n{ctx}\n\nCurrent user message: {query}"
    result = await llm_service.run_agent(router_agent, prompt)
    return result.output, _usage_from_result(result)


def _decision_to_prompt(decision: RoutingDecision, prior_context: str | None = None) -> str:
    if len(decision.tasks) == 1:
        task_block = decision.tasks[0]
    else:
        task_block = "Do the following:\n" + "\n".join(f"{i+1}. {t}" for i, t in enumerate(decision.tasks))
    body = f"{task_block}\n\nSuccess criteria: {decision.success_criteria}"
    if prior_context:
        return f"Relevant prior conversation:\n{prior_context}\n\n{body}"
    return body


def _format_web_search_output(output) -> str:
    lines = []
    if output.news_items:
        lines += ["### 📰 News", ""]
        for i, item in enumerate(output.news_items, 1):
            lines.append(f"**{i}. {item.what_happened}**")
            if item.event_time_ist:
                lines.append(f"   🕐 {item.event_time_ist}")
            lines.append(f"   💡 *Why it matters:* {item.why_it_matters}")
            if item.how_did_customers_react:
                lines.append(f"   📈 *Market reaction:* {item.how_did_customers_react}")
            lines.append("")
        lines += ["---", ""]
    if output.overall_market_reaction:
        lines += ["### 📊 Overall market reaction", "", output.overall_market_reaction, ""]
    if output.analyst_commentary:
        lines += ["### 🎙️ Analyst commentary", "", output.analyst_commentary, ""]
    return "\n".join(lines).strip() or "*No news found.*"


async def _run_sub_agent_stream(
    deps: AgentDeps,
    decision: RoutingDecision,
    conversation_summary: str | None,
    max_iterations: int,
):
    """Run the sub-agent chosen by `decision`, yielding its tool calls, then a 'final' event."""
    is_web = decision.agent == AgentType.WEB_SEARCH
    s = get_settings()

    if is_web:
        from stock_screening.agents.web_search_agent import (
            extract_sources_from_messages,
            web_search_agent,
        )

        agent, name = web_search_agent, "web_search_agent"
        history = deps.web_search_history
        run_kwargs = {"model_settings": WEB_SEARCH_MODEL_SETTINGS}
        model_id = s.llm_openai_responses_model_id
    else:
        from stock_screening.agents.screening_agent import screening_agent

        agent, name = screening_agent, "screening_agent"
        history = deps.screening_history
        run_kwargs = {}
        model_id = s.llm_anthropic_model_id

    base_prompt = _decision_to_prompt(decision, prior_context=conversation_summary)
    logger.info("Running %s (streaming): %s", name, base_prompt[:100])

    output = None
    in_tokens = out_tokens = 0
    duration_ms = 0.0
    tool_results: dict[str, str] = {}

    for i in range(max_iterations):
        logger.info("%s iteration %d/%d", name, i + 1, max_iterations)
        if i == 0:
            prompt = base_prompt
        else:
            prompt = f"Continue: {base_prompt}. {output.follow_up_suggestion or ''}"

        async for event in llm_service.run_agent_stream(
            agent, prompt, message_history=history, **run_kwargs
        ):
            if event["type"] != "result":
                yield event
                continue
            history = event["messages"]
            output = event["output"]
            usage = event.get("usage", {})
            in_tokens += usage.get("input_tokens", 0)
            out_tokens += usage.get("output_tokens", 0)
            duration_ms += event.get("duration_ms", 0)
            tool_results.update(event.get("tool_results", {}))

        if not output or output.completed:
            break
        if not output.follow_up_suggestion:
            logger.warning("%s not completed but gave no follow_up_suggestion", name)
            break

    if is_web:
        deps.web_search_history = history
        deps.last_sources = extract_sources_from_messages(history)
        message = _format_web_search_output(output)
    else:
        deps.screening_history = history
        deps.last_filters = output.applied_filters if output else None
        message = output.message if output else ""

    yield {
        "type": "final",
        "response": MainResponse(
            message=message,
            agent_used=decision.agent,
            follow_up_suggestion=output.follow_up_suggestion if output else None,
            routing_decision=decision,
        ),
        "trace": {
            "input_tokens": in_tokens,
            "output_tokens": out_tokens,
            "duration_ms": duration_ms,
            "model_id": model_id,
        },
        "tool_results": tool_results,
    }


async def synthesize_response(
    query: str,
    decision: RoutingDecision,
    agent_output: str,
    agent_used: str,
    conversation_context: list[dict] | None = None,
) -> tuple[str, dict]:
    parts = []
    if conversation_context:
        ctx = _format_conversation_context(conversation_context, max_chars=SYNTHESIS_CONTEXT_MAX_CHARS)
        if ctx:
            parts.append(f"Recent conversation (prior turns only):\n{ctx}\n")
    parts.append(f"Current user request: {query}\n")
    parts.append(f"Tasks executed: {'; '.join(decision.tasks)}\nAgent used: {agent_used}\n")
    parts.append(f"Raw output from agent:\n\n{agent_output}")
    parts.append("\n\nWrite the final response to the user.")

    logger.info("Synthesis running: query=%s... agent_used=%s", query[:60], agent_used)
    result = await llm_service.run_agent(synthesis_agent, "\n".join(parts))
    return result.output.message.strip(), _usage_from_result(result)


async def route_query_stream(
    deps: AgentDeps,
    query: str,
    conversation_context: list[dict] | None = None,
    force_agent: str | None = None,
    max_iterations: int = MAX_ITERATIONS,
):
    """Classify, run the sub-agent, synthesize.

    Yields 'routing' and 'tool_call' events as they happen, then a 'final' event
    carrying the response plus a trace of merged token usage and cost.
    """
    if force_agent in (AgentType.SCREENING, AgentType.WEB_SEARCH):
        decision = RoutingDecision(
            agent=AgentType(force_agent),
            tasks=[query],
            success_criteria="Answer the user's question",
        )
        router_usage = {"input_tokens": 0, "output_tokens": 0}
    else:
        decision, router_usage = await classify_intent(query, conversation_context=conversation_context)
    yield {"type": "routing", "decision": decision}

    summary = None
    if conversation_context:
        summary = _format_conversation_context(
            conversation_context, max_chars=SUB_AGENT_CONTEXT_MAX_CHARS
        ) or None

    result = None
    trace = {}
    tool_results: dict[str, str] = {}
    async for event in _run_sub_agent_stream(deps, decision, summary, max_iterations):
        if event["type"] == "final":
            result = event["response"]
            trace = event["trace"]
            tool_results = event["tool_results"]
        else:
            yield event

    if not result:
        return

    result.message, synth_usage = await synthesize_response(
        query, decision, result.message, decision.agent.value,
        conversation_context=conversation_context,
    )

    router_model = get_settings().llm_router_model_id
    agent_usage = {"input_tokens": trace["input_tokens"], "output_tokens": trace["output_tokens"]}
    costs = {
        "router": _cost_for_usage(router_usage, router_model),
        "agent": _cost_for_usage(agent_usage, trace["model_id"]),
        "synthesis": _cost_for_usage(synth_usage, router_model),
    }
    stages = (router_usage, agent_usage, synth_usage)
    total_in = sum(u.get("input_tokens", 0) for u in stages)
    total_out = sum(u.get("output_tokens", 0) for u in stages)

    yield {
        "type": "final",
        "response": result,
        "trace": {
            "input_tokens": total_in,
            "output_tokens": total_out,
            "total_tokens": total_in + total_out,
            "duration_ms": trace["duration_ms"],
            "cost_usd": sum(costs.values()),
            "cost_router_usd": costs["router"],
            "cost_agent_usd": costs["agent"],
            "cost_synthesis_usd": costs["synthesis"],
        },
        "tool_results": tool_results,
    }


async def route_query(
    deps: AgentDeps,
    query: str,
    conversation_context: list[dict] | None = None,
) -> MainResponse | None:
    """Non-streaming convenience wrapper: drains route_query_stream and returns the response."""
    async for event in route_query_stream(deps, query, conversation_context=conversation_context):
        if event["type"] == "final":
            return event["response"]
    return None


if __name__ == "__main__":
    import argparse
    import asyncio

    from stock_screening.logging_config import setup_logging
    from stock_screening.models.constants import CONVERSATION_CONTEXT_MESSAGES

    def _print(result: MainResponse | None) -> None:
        if not result:
            print("[No response]")
            return
        print("\n" + "-" * 50)
        print(result.message)
        print("-" * 50)
        if result.agent_used:
            print(f"  Agent: {result.agent_used.value}")
        if result.follow_up_suggestion:
            print(f"  Suggestion: {result.follow_up_suggestion}")
        print()

    async def main() -> None:
        parser = argparse.ArgumentParser(description="Stock research assistant")
        parser.add_argument("query", nargs="*", help="Query; omit for interactive mode")
        args = parser.parse_args()

        setup_logging(level="INFO")
        deps = AgentDeps()

        if args.query:
            query = " ".join(args.query)
            print(f"Query: {query}\n")
            _print(await route_query(deps, query))
            return

        print("Stock Research Assistant — 'quit' to exit, 'clear' to reset history\n")
        history: list[dict] = []
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
                deps, history = AgentDeps(), []
                print("[History cleared]\n")
                continue

            try:
                result = await route_query(
                    deps, query, conversation_context=history[-CONVERSATION_CONTEXT_MESSAGES:]
                )
                if result:
                    history.append({"role": "user", "content": query})
                    history.append({"role": "assistant", "content": result.message})
                _print(result)
            except Exception as e:
                logger.error("Error: %s", e)
                print(f"\n[Error: {e}]\n")

    asyncio.run(main())
