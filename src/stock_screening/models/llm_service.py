"""Agent execution with retry on transient provider errors."""

import json
import logging
import time
from typing import Any

from pydantic_ai.exceptions import ModelHTTPError
from pydantic_ai.messages import ToolCallPart, ToolReturnPart
from tenacity import (
    before_sleep_log,
    retry,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential,
)

logger = logging.getLogger(__name__)


def _is_retryable_error(exc: BaseException) -> bool:
    return isinstance(exc, ModelHTTPError) and exc.status_code == 503


def _extract_tool_results(messages: list) -> dict[str, str]:
    """Map tool_call_id -> tool output text, over a message history."""
    results = {}
    for msg in messages:
        for part in getattr(msg, "parts", []) or []:
            if isinstance(part, ToolReturnPart) and part.tool_call_id:
                content = part.content
                results[part.tool_call_id] = (
                    content if isinstance(content, str) else json.dumps(content, indent=2, default=str)
                )
    return results


class LLMService:
    @staticmethod
    @retry(
        retry=retry_if_exception(_is_retryable_error),
        stop=stop_after_attempt(5),
        wait=wait_exponential(multiplier=1, min=2, max=60),
        before_sleep=before_sleep_log(logger, logging.WARNING),
        reraise=True,
    )
    async def run_agent(agent: Any, prompt: str, **kwargs: Any) -> Any:
        return await agent.run(prompt, **kwargs)

    @staticmethod
    async def run_agent_stream(agent: Any, prompt: str, **kwargs: Any):
        """Run an agent, yielding a 'tool_call' event per call, then one 'result' event.

        The result event carries output, messages, usage, duration_ms and tool_results.
        """
        from pydantic_graph import End

        start = time.perf_counter()

        async with agent.iter(prompt, **kwargs) as run:
            async for node in run:
                if isinstance(node, End):
                    break
                response = getattr(node, "model_response", None)
                if response is None:
                    continue
                for part in response.parts:
                    if not isinstance(part, ToolCallPart):
                        continue
                    args = part.args
                    if isinstance(args, str):
                        try:
                            args = json.loads(args)
                        except json.JSONDecodeError:
                            pass  # partial stream or a plain string arg
                    yield {
                        "type": "tool_call",
                        "name": part.tool_name,
                        "args": args,
                        "tool_call_id": part.tool_call_id or "",
                        "timestamp": time.time(),
                    }

            messages = run.result.all_messages()
            usage = run.result.usage()
            yield {
                "type": "result",
                "output": run.result.output,
                "messages": messages,
                "usage": {
                    "input_tokens": getattr(usage, "input_tokens", 0) or getattr(usage, "request_tokens", 0) or 0,
                    "output_tokens": getattr(usage, "output_tokens", 0) or getattr(usage, "response_tokens", 0) or 0,
                } if usage else {},
                "duration_ms": (time.perf_counter() - start) * 1000,
                "tool_results": _extract_tool_results(messages),
            }


llm_service = LLMService()
