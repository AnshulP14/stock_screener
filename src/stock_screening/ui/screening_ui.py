"""Gradio UI for stock research assistant."""

import html
import json

import gradio as gr

from stock_screening.agents.base import AgentDeps
from stock_screening.agents.main_agent import ToolCallData, TraceData, chat_stream
from stock_screening.logging_config import get_logger, setup_logging

# Setup logging at import time
setup_logging(level="INFO")
logger = get_logger(__name__)


# Pydantic AI uses this as the "tool" name for structured output_type; hide from UI
OUTPUT_TYPE_TOOL_NAMES = frozenset({"final_result", "final_result_websearchresponse"})

# Max chars to show in tool result; rest is truncated
TOOL_RESULT_MAX = 1200


def _args_summary(args: dict) -> str:
    """One-line summary of key args for the tool card header."""
    if not args:
        return ""
    parts = []
    for k, v in list(args.items())[:5]:
        if v is None or v == "":
            continue
        sv = str(v)
        if len(sv) > 25:
            sv = sv[:22] + "..."
        parts.append(f"{k}={sv}")
    return " · ".join(parts) if parts else ""


def _args_to_html_table(args: dict) -> str:
    """Format args as HTML table (param | value)."""
    if not args:
        return "<p style='color:#888; margin:0;'>No parameters</p>"
    rows = []
    for k, v in args.items():
        val = v if v is not None else "—"
        if isinstance(val, (dict, list)):
            val = json.dumps(val, indent=2)
        val = str(val)
        if len(val) > 400:
            val = val[:397] + "..."
        val = html.escape(val).replace("\n", "<br/>")
        rows.append(f"<tr><td style='padding:0.25em 0.5em 0.25em 0; color:#555; vertical-align:top;'>{html.escape(k)}</td><td style='padding:0.25em 0; word-break:break-word;'>{val}</td></tr>")
    return f"<table style='width:100%; border-collapse:collapse; font-size:0.9em;'><tbody>\n" + "\n".join(rows) + "\n</tbody></table>"


def format_tool_call_collapsible(tool_data: ToolCallData, index: int) -> str:
    """Format a single tool call as a compact, scannable card (collapsed by default)."""
    duration_ms: float | None = None
    if tool_data.end_time and tool_data.start_time:
        duration_ms = (tool_data.end_time - tool_data.start_time) * 1000
    duration_str = f"{duration_ms:.0f}ms" if duration_ms is not None else "…"
    summary_line = _args_summary(tool_data.args)
    if summary_line:
        summary_line = f" — {summary_line}"

    params_block = _args_to_html_table(tool_data.args)
    result = (tool_data.result or "").strip()
    if len(result) > TOOL_RESULT_MAX:
        result = result[:TOOL_RESULT_MAX] + "\n\n_… truncated_"
    result_display = html.escape(result) if result else "_no output_"
    # Use <pre> for result so newlines and spacing are preserved
    result_block = f"<pre style=\"margin:0.5em 0; padding:0.75em; background:#f5f5f5; border-radius:6px; overflow:auto; max-height:280px; white-space:pre-wrap; font-size:0.9em;\">{result_display}</pre>"

    return f"""<div style="margin:0.5em 0; border:1px solid #e0e0e0; border-radius:8px; overflow:hidden;">
<details style="margin:0;">
<summary style="cursor:pointer; padding:0.6em 0.75em; background:#fafafa; font-weight:600; list-style:none; display:flex; align-items:center; gap:0.5em;">
<span style="color:#555;">#{index}</span> <span>🔧 {html.escape(tool_data.name)}</span>
<span style="color:#888; font-weight:400; font-size:0.9em;">({duration_str})</span>
<span style="color:#666; font-weight:400; font-size:0.85em;">{html.escape(summary_line)}</span>
</summary>
<div style="padding:0.75em 1em; border-top:1px solid #eee;">
<p style="margin:0 0 0.4em 0; font-weight:600; font-size:0.9em;">Parameters</p>
{params_block}
<p style="margin:1em 0 0.4em 0; font-weight:600; font-size:0.9em;">Result</p>
{result_block}
</div>
</details>
</div>"""


def format_tool_calls_section(tool_calls: list[ToolCallData]) -> str:
    """Format all tool calls in a clear section (excludes output_type tools like final_result)."""
    visible = [tc for tc in tool_calls if tc.name not in OUTPUT_TYPE_TOOL_NAMES]
    if not visible:
        return ""

    tool_cards = "\n".join(format_tool_call_collapsible(tc, i) for i, tc in enumerate(visible, 1))

    return f"""<div style="margin:1em 0;">
<p style="margin:0 0 0.5em 0; font-weight:700; font-size:1em;">🛠️ Tool calls ({len(visible)})</p>
{tool_cards}
</div>

"""


def _fmt_usd(value: float) -> str:
    return f"${value:.4f}" if value else "—"


def format_trace_summary(trace: TraceData) -> str:
    """Format trace data as a collapsible summary with step-wise tokens and cost."""
    cost_str = _fmt_usd(trace.cost_usd)
    # Per-step tokens (so router/synthesis token figures are visible even when cost is —)
    r_in = trace.router_input_tokens
    r_out = trace.router_output_tokens
    a_in = trace.agent_input_tokens
    a_out = trace.agent_output_tokens
    s_in = trace.synthesis_input_tokens
    s_out = trace.synthesis_output_tokens
    has_step_tokens = r_in or r_out or a_in or a_out or s_in or s_out
    rows = []
    if has_step_tokens:
        rows.append("| **Step** | **Input tok** | **Output tok** | **Cost (est.)** |")
        rows.append("|----------|---------------|----------------|----------------|")
        rows.append("| Router | {} | {} | {} |".format(r_in, r_out, _fmt_usd(trace.cost_router_usd)))
        rows.append("| Agent | {} | {} | {} |".format(a_in, a_out, _fmt_usd(trace.cost_agent_usd)))
        rows.append("| Synthesis | {} | {} | {} |".format(s_in, s_out, _fmt_usd(trace.cost_synthesis_usd)))
        rows.append("| **Total** | **{}** | **{}** | **{}** |".format(trace.input_tokens, trace.output_tokens, cost_str))
    else:
        # Fallback if no step breakdown
        rows.append("| **Cost (est., all LLM ops)** | {} |".format(cost_str))
        rows.append("| Input tokens | {} |".format(trace.input_tokens))
        rows.append("| Output tokens | {} |".format(trace.output_tokens))
        rows.append("| Total tokens | {} |".format(trace.total_tokens))
    duration_s = trace.duration_ms / 1000
    rows.append("| Duration (agent) | {:.2f}s |".format(duration_s))
    table = "\n".join(rows)
    return f"""<details>
<summary>📊 <b>Trace</b> (total LLM) {cost_str} · {duration_s:.2f}s</summary>

{table}

</details>"""


async def process_query(user_message: str, history: list, deps: AgentDeps):
    """Process query with streaming tool call display."""
    if not user_message.strip():
        yield history, deps
        return

    logger.info("User message received: %s", user_message[:100])

    # Add user message
    history = history + [{"role": "user", "content": user_message}]
    yield history, deps

    try:
        # Track tool calls for display
        tool_calls: list[ToolCallData] = []
        routing_info = ""

        async for event in chat_stream(deps, user_message, history):
            if event.type == "status":
                routing_info = event.content + "\n\n"
                yield history + [{"role": "assistant", "content": routing_info + "*Running...*"}], deps

            elif event.type == "tool":
                # Skip output_type "tools" (e.g. final_result) so we only show real tools
                if event.name in OUTPUT_TYPE_TOOL_NAMES:
                    continue
                # Use enhanced tool_data if available, fallback to name-only
                if event.tool_data:
                    tool_calls.append(event.tool_data)
                else:
                    tool_calls.append(ToolCallData(name=event.name))

                # Show collapsible tool calls during execution
                tool_section = format_tool_calls_section(tool_calls)
                status = routing_info + tool_section + "*Executing...*"
                yield history + [{"role": "assistant", "content": status}], deps

            elif event.type == "final":
                # Update tool calls with results
                import time
                end_time = time.time()
                for tc in tool_calls:
                    tc.end_time = end_time
                    if tc.tool_call_id and tc.tool_call_id in event.tool_results:
                        tc.result = event.tool_results[tc.tool_call_id]

                # Build final content (event.content already has route + agent + message)
                parts = []
                if tool_calls:
                    parts.append(format_tool_calls_section(tool_calls))
                parts.append(event.content)

                if event.trace:
                    parts.append("\n\n---\n\n")
                    parts.append(format_trace_summary(event.trace))

                final_content = "".join(parts)
                logger.info("Sending response (%d chars)", len(final_content))
                yield history + [{"role": "assistant", "content": final_content}], deps

    except Exception as e:
        logger.exception("Error: %s", e)
        yield history + [{"role": "assistant", "content": f"**Error:** {e}"}], deps


def clear_chat():
    """Clear chat history and reset agent state."""
    return [], AgentDeps()


# Build UI
with gr.Blocks(title="Stock Research Assistant") as demo:
    gr.Markdown("# Stock Research Assistant\nAsk about NSE/BSE stocks - screening, news, and analysis.")

    # Session state for agent dependencies (pass instance, not class)
    deps_state = gr.State(AgentDeps())

    chatbot = gr.Chatbot(height=500)

    with gr.Row():
        msg = gr.Textbox(
            placeholder="e.g., Find value stocks or get latest news on TCS",
            show_label=False,
            scale=9,
        )
        submit = gr.Button("Send", scale=1)

    clear = gr.Button("Clear Chat")

    gr.Examples(
        examples=[
            "Find 5 value stocks with low P/E and high ROE",
            "Latest news on Reliance Industries",
            "Show me IT sector stocks with market cap above 50000 crores",
            "What happened in the markets today?",
        ],
        inputs=msg,
    )

    submit.click(process_query, [msg, chatbot, deps_state], [chatbot, deps_state]).then(
        lambda: "", outputs=[msg]
    )
    msg.submit(process_query, [msg, chatbot, deps_state], [chatbot, deps_state]).then(
        lambda: "", outputs=[msg]
    )
    clear.click(clear_chat, outputs=[chatbot, deps_state])


if __name__ == "__main__":
    demo.launch()
