"""
LLMService: Retry-enabled wrapper for agent.run().

Provides centralized LLM operations with automatic retry handling for transient errors.
"""

import logging
import time
from typing import Any

from pydantic_ai.exceptions import ModelHTTPError
from tenacity import (
    before_sleep_log,
    retry,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential,
)

_retry_logger = logging.getLogger(__name__)


def _is_retryable_error(exc: BaseException) -> bool:
    """Check if exception is a retryable 503 overload error."""
    return isinstance(exc, ModelHTTPError) and exc.status_code == 503


def _extract_tool_results(messages: list) -> dict[str, str]:
    """Extract tool results from message history, keyed by tool_call_id."""
    from pydantic_ai.messages import ToolReturnPart

    results = {}
    for msg in messages:
        if hasattr(msg, "parts"):
            for part in msg.parts:
                if isinstance(part, ToolReturnPart):
                    tool_call_id = getattr(part, "tool_call_id", "") or ""
                    content = part.content if hasattr(part, "content") else str(part)
                    # Truncate long results
                    if isinstance(content, str) and len(content) > 500:
                        content = content[:500] + "..."
                    results[tool_call_id] = content
    return results


class LLMService:
    """Centralized LLM operations with retry handling for transient errors."""

    @staticmethod
    @retry(
        retry=retry_if_exception(_is_retryable_error),
        stop=stop_after_attempt(5),
        wait=wait_exponential(multiplier=1, min=2, max=60),
        before_sleep=before_sleep_log(_retry_logger, logging.WARNING),
        reraise=True,
    )
    async def run_agent(agent: Any, prompt: str, **kwargs: Any) -> Any:
        """Run an agent with automatic retry on 503 errors.

        Args:
            agent: Pydantic AI Agent instance
            prompt: The prompt to send
            **kwargs: Additional kwargs passed to agent.run()

        Returns:
            The agent run result
        """
        return await agent.run(prompt, **kwargs)

    @staticmethod
    async def run_agent_stream(agent: Any, prompt: str, **kwargs: Any):
        """Run agent with streaming, yielding tool calls with args and timing.

        Yields:
            dict with 'type' ('tool_call', 'result') and relevant data including:
            - tool_call: name, args, tool_call_id, timestamp
            - result: output, messages, usage (tokens), duration_ms
        """
        from pydantic_ai.messages import ToolCallPart
        from pydantic_graph import End

        start_time = time.perf_counter()
        pending_tool_calls: dict[str, dict] = {}  # Track tool calls by id

        async with agent.iter(prompt, **kwargs) as run:
            async for node in run:
                if isinstance(node, End):
                    break
                # CallToolsNode contains tool calls in model_response.parts
                if hasattr(node, "model_response"):
                    for part in node.model_response.parts:
                        if isinstance(part, ToolCallPart):
                            tool_call_id = getattr(part, "tool_call_id", "") or ""
                            tool_data = {
                                "type": "tool_call",
                                "name": part.tool_name,
                                "args": part.args if hasattr(part, "args") else {},
                                "tool_call_id": tool_call_id,
                                "timestamp": time.time(),
                            }
                            pending_tool_calls[tool_call_id] = tool_data
                            yield tool_data

            # Calculate duration and extract usage
            duration_ms = (time.perf_counter() - start_time) * 1000
            messages = run.result.all_messages()

            # Extract tool results and match to calls
            tool_results = _extract_tool_results(messages)

            # Extract usage from result
            usage_data = {}
            if hasattr(run.result, "usage"):
                usage = run.result.usage()
                if usage:
                    usage_data = {
                        "input_tokens": getattr(usage, "request_tokens", 0) or getattr(usage, "input_tokens", 0) or 0,
                        "output_tokens": getattr(usage, "response_tokens", 0) or getattr(usage, "output_tokens", 0) or 0,
                        "total_tokens": getattr(usage, "total_tokens", 0) or 0,
                    }

            yield {
                "type": "result",
                "output": run.result.output,
                "messages": messages,
                "usage": usage_data,
                "duration_ms": duration_ms,
                "tool_results": tool_results,
            }


# Global singleton
llm_service = LLMService()
