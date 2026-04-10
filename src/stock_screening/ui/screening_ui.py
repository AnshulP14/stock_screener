"""Gradio UI for stock research assistant."""

import html
import json
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import gradio as gr

from stock_screening.agents.base import AgentDeps
from stock_screening.agents.main_agent import ToolCallData, TraceData, chat_stream
from stock_screening.logging_config import get_logger, setup_logging

setup_logging(level="INFO")
logger = get_logger(__name__)

OUTPUT_TYPE_TOOL_NAMES = frozenset({"final_result", "final_result_websearchresponse"})
TOOL_RESULT_MAX = 1200

ROOT_DIR = Path(__file__).parent.parent.parent.parent
DATA_DIR = ROOT_DIR / "data"
SCRIPTS_DIR = ROOT_DIR / "scripts"
SCREENING_SUMMARY = DATA_DIR / "indices" / "screening_summary.json"


# ---------------------------------------------------------------------------
# Data freshness
# ---------------------------------------------------------------------------


def _get_data_status() -> dict:
    """Read screening_summary.json for data freshness info."""
    try:
        with open(SCREENING_SUMMARY) as f:
            data = json.load(f)
        generated_at = data.get("generated_at", "")
        total = data.get("total_companies", 0)
        if generated_at:
            gen_dt = datetime.fromisoformat(generated_at)
            age_days = (datetime.now() - gen_dt).days
            age_str = f"{age_days}d ago" if age_days > 0 else "today"
            date_str = gen_dt.strftime("%b %d, %Y")
            return {"total": total, "date": date_str, "age": age_str, "age_days": age_days, "ok": True}
    except Exception:
        pass
    return {"total": 0, "date": "unknown", "age": "unknown", "age_days": 999, "ok": False}


def _build_status_html() -> str:
    s = _get_data_status()
    if not s["ok"]:
        return (
            "<div style='padding:0.4em 0.75em; background:#2a1a1a; border-radius:6px; "
            "font-size:0.82em; color:#f97316;'>"
            "⚠️ Data index not found — run <code>python scripts/ingest.py</code> to fetch data."
            "</div>"
        )

    age_days = s["age_days"]
    if age_days <= 3:
        color = "#22c55e"
    elif age_days <= 14:
        color = "#f59e0b"
    else:
        color = "#ef4444"

    stale_warning = ""
    if age_days > 14:
        stale_warning = (
            f"<span style='color:#ef4444;'>⚠ Data is {age_days}d old — consider refreshing</span>"
        )

    return (
        f"<div style='padding:0.4em 0.75em; background:#1a1f2e; border-radius:6px; "
        f"font-size:0.82em; color:#aaa; display:flex; align-items:center; gap:1.2em; flex-wrap:wrap;'>"
        f"<span style='color:{color}; font-weight:600;'>● Live</span>"
        f"<span><b style='color:#d4d4d4;'>{s['total']}</b> NSE stocks indexed</span>"
        f"<span>Updated: <b style='color:#d4d4d4;'>{s['date']}</b> ({s['age']})</span>"
        f"{stale_warning}"
        f"</div>"
    )


# ---------------------------------------------------------------------------
# Tool call formatting
# ---------------------------------------------------------------------------


def _args_summary(args: Any) -> str:
    if not args:
        return ""
    if not isinstance(args, dict):
        return str(args)
    parts = []
    for k, v in list(args.items())[:5]:
        if v is None or v == "":
            continue
        sv = str(v)
        if len(sv) > 25:
            sv = sv[:22] + "..."
        parts.append(f"{k}={sv}")
    return " · ".join(parts) if parts else ""


def _args_to_html_table(args: Any) -> str:
    if not args:
        return "<p style='color:#888; margin:0;'>No parameters</p>"
    if not isinstance(args, dict):
        return f"<p style='margin:0; color:#d4d4d4;'>{html.escape(str(args))}</p>"
    rows = []
    for k, v in args.items():
        val = v if v is not None else "—"
        if isinstance(val, (dict, list)):
            val = json.dumps(val, indent=2)
        val = str(val)
        if len(val) > 400:
            val = val[:397] + "..."
        val = html.escape(val).replace("\n", "<br/>")
        rows.append(
            f"<tr><td style='padding:0.25em 0.5em 0.25em 0; color:#aaa; vertical-align:top;'>{html.escape(k)}</td>"
            f"<td style='padding:0.25em 0; word-break:break-word; color:#d4d4d4;'>{val}</td></tr>"
        )
    return (
        "<table style='width:100%; border-collapse:collapse; font-size:0.9em;'><tbody>\n"
        + "\n".join(rows)
        + "\n</tbody></table>"
    )


def _make_urls_clickable(text: str) -> str:
    import re

    text = re.sub(
        r'\[\[(\d+)\]\]\((https?://[^\s\)]+)\)',
        r'<a href="\2" target="_blank" style="color:#6cb6ff;">[\1]</a>',
        text,
    )
    text = re.sub(
        r'(?<!href=")(https?://[^\s<>\"\)]+)',
        r'<a href="\1" target="_blank" style="color:#6cb6ff;">\1</a>',
        text,
    )
    return text


def _extract_answer_from_result(result: str) -> str:
    try:
        data = json.loads(result)
        if isinstance(data, dict) and "answer" in data:
            return data["answer"]
    except (json.JSONDecodeError, TypeError):
        pass
    return result


def format_tool_call_collapsible(tool_data: ToolCallData, index: int) -> str:
    duration_ms: float | None = None
    if tool_data.end_time and tool_data.start_time:
        duration_ms = (tool_data.end_time - tool_data.start_time) * 1000
    duration_str = f"{duration_ms:.0f}ms" if duration_ms is not None else "…"
    summary_line = _args_summary(tool_data.args)
    if summary_line:
        summary_line = f" — {summary_line}"

    params_block = _args_to_html_table(tool_data.args)
    result = (tool_data.result or "").strip()
    result = _extract_answer_from_result(result)
    result_display = html.escape(result) if result else "_no output_"
    result_display = _make_urls_clickable(result_display)
    result_display = result_display.replace("\n", "<br/>")
    result_block = (
        "<div style=\"margin:0.5em 0; padding:0.75em; background:#1e1e1e; color:#d4d4d4; "
        "border-radius:6px; overflow-y:auto; max-height:500px; font-size:0.9em; line-height:1.6;\">"
        f"{result_display}</div>"
    )

    return (
        f"<div style=\"margin:0.5em 0; border:1px solid #444; border-radius:8px; overflow:hidden; background:#2d2d2d;\">"
        f"<details style=\"margin:0;\">"
        f"<summary style=\"cursor:pointer; padding:0.6em 0.75em; background:#3a3a3a; font-weight:600; "
        f"list-style:none; display:flex; align-items:center; gap:0.5em; color:#e0e0e0;\">"
        f"<span style=\"color:#aaa;\">#{index}</span> "
        f"<span style=\"color:#e0e0e0;\">🔧 {html.escape(tool_data.name)}</span>"
        f"<span style=\"color:#999; font-weight:400; font-size:0.9em;\">({duration_str})</span>"
        f"<span style=\"color:#888; font-weight:400; font-size:0.85em;\">{html.escape(summary_line)}</span>"
        f"</summary>"
        f"<div style=\"padding:0.75em 1em; border-top:1px solid #444; color:#d4d4d4;\">"
        f"<p style=\"margin:0 0 0.4em 0; font-weight:600; font-size:0.9em; color:#e0e0e0;\">Parameters</p>"
        f"{params_block}"
        f"<p style=\"margin:1em 0 0.4em 0; font-weight:600; font-size:0.9em; color:#e0e0e0;\">Result</p>"
        f"{result_block}"
        f"</div></details></div>"
    )


def format_tool_calls_section(tool_calls: list[ToolCallData]) -> str:
    visible = [tc for tc in tool_calls if tc.name not in OUTPUT_TYPE_TOOL_NAMES]
    if not visible:
        return ""
    tool_cards = "\n".join(format_tool_call_collapsible(tc, i) for i, tc in enumerate(visible, 1))
    return (
        f"<div style=\"margin:1em 0;\">"
        f"<p style=\"margin:0 0 0.5em 0; font-weight:700; font-size:1em;\">🛠️ Tool calls ({len(visible)})</p>"
        f"{tool_cards}"
        f"</div>\n\n"
    )


def _fmt_usd(value: float) -> str:
    return f"${value:.4f}" if value else "—"


def format_trace_summary(trace: TraceData) -> str:
    cost_str = _fmt_usd(trace.cost_usd)
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
        rows.append(f"| Router | {r_in} | {r_out} | {_fmt_usd(trace.cost_router_usd)} |")
        rows.append(f"| Agent | {a_in} | {a_out} | {_fmt_usd(trace.cost_agent_usd)} |")
        rows.append(f"| Synthesis | {s_in} | {s_out} | {_fmt_usd(trace.cost_synthesis_usd)} |")
        rows.append(f"| **Total** | **{trace.input_tokens}** | **{trace.output_tokens}** | **{cost_str}** |")
    else:
        rows.append(f"| **Cost (est., all LLM ops)** | {cost_str} |")
        rows.append(f"| Input tokens | {trace.input_tokens} |")
        rows.append(f"| Output tokens | {trace.output_tokens} |")
        rows.append(f"| Total tokens | {trace.total_tokens} |")
    duration_s = trace.duration_ms / 1000
    rows.append(f"| Duration (agent) | {duration_s:.2f}s |")
    table = "\n".join(rows)
    return (
        f"<details><summary>📊 <b>Trace</b> (total LLM) {cost_str} · {duration_s:.2f}s</summary>"
        f"\n\n{table}\n\n</details>"
    )


# ---------------------------------------------------------------------------
# Query processing
# ---------------------------------------------------------------------------


async def process_query(user_message: str, history: list, deps: AgentDeps):
    """Process query with streaming tool call display."""
    if not user_message.strip():
        yield history, deps
        return

    logger.info("User message received: %s", user_message[:100])
    history = history + [{"role": "user", "content": user_message}]
    yield history, deps

    try:
        tool_calls: list[ToolCallData] = []
        routing_info = ""

        async for event in chat_stream(deps, user_message, history):
            if event.type == "status":
                routing_info = event.content + "\n\n"
                yield history + [{"role": "assistant", "content": routing_info + "*Running...*"}], deps

            elif event.type == "tool":
                if event.name in OUTPUT_TYPE_TOOL_NAMES:
                    continue
                if event.tool_data:
                    tool_calls.append(event.tool_data)
                else:
                    tool_calls.append(ToolCallData(name=event.name))
                tool_section = format_tool_calls_section(tool_calls)
                yield history + [{"role": "assistant", "content": routing_info + tool_section + "*Executing...*"}], deps

            elif event.type == "final":
                end_time = time.time()
                for tc in tool_calls:
                    tc.end_time = end_time
                    if tc.tool_call_id and tc.tool_call_id in event.tool_results:
                        tc.result = event.tool_results[tc.tool_call_id]

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
    return [], AgentDeps()


# ---------------------------------------------------------------------------
# Data refresh (ingestion pipeline)
# ---------------------------------------------------------------------------


def _run_ingest(mode: str):
    """Run the ingestion pipeline and yield log lines."""
    script = SCRIPTS_DIR / "ingest.py"
    if not script.exists():
        # Fall back to update_data.py
        script = SCRIPTS_DIR / "update_data.py"

    if not script.exists():
        yield "❌ Ingestion script not found. Expected scripts/ingest.py or scripts/update_data.py\n"
        return

    flag_map = {
        "quick": ["--quick"],
        "incremental": [],
        "full": ["--full"],
        "transform": ["--rebuild"],
    }
    flags = flag_map.get(mode, [])

    yield f"▶ Running: python {script.name} {' '.join(flags)}\n"
    yield "─" * 50 + "\n"

    try:
        proc = subprocess.Popen(
            [sys.executable, str(script)] + flags,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        for line in proc.stdout:
            yield line
        proc.wait()
        yield "─" * 50 + "\n"
        if proc.returncode == 0:
            yield "✅ Done!\n"
        else:
            yield f"❌ Exited with code {proc.returncode}\n"
    except Exception as e:
        yield f"❌ Error: {e}\n"


def run_ingest_streaming(mode: str, current_log: str):
    """Generator for streaming ingestion output into the log textbox."""
    log = f"[{datetime.now().strftime('%H:%M:%S')}] Starting {mode} refresh...\n"
    yield log, gr.update(interactive=False), gr.update(interactive=False), gr.update(interactive=False), gr.update(interactive=False)

    for line in _run_ingest(mode):
        log += line
        yield log, gr.update(interactive=False), gr.update(interactive=False), gr.update(interactive=False), gr.update(interactive=False)

    # Re-enable buttons and refresh status
    yield log, gr.update(interactive=True), gr.update(interactive=True), gr.update(interactive=True), gr.update(interactive=True)


# ---------------------------------------------------------------------------
# UI layout
# ---------------------------------------------------------------------------

_HEADER = """<div style="padding:0.75em 0 0.25em 0;">
  <h1 style="margin:0; font-size:1.55em; font-weight:700;">📈 NSE Stock Research Assistant</h1>
  <p style="margin:0.25em 0 0 0; color:#888; font-size:0.88em;">
    Fundamental screening across 500 NSE stocks &nbsp;·&nbsp; Live news via web search &nbsp;·&nbsp;
    Powered by Claude, Gemini &amp; GPT-4o
  </p>
</div>"""

_TIPS = """**Tips**
- Filter by sector, industry, P/E, ROE, market cap…
- Ask for news + fundamentals in the same query
- Name companies directly: *Reliance*, *INFY*, *HDFC Bank*
- Follow-up on any result to go deeper

**Data**
- ~500 NSE500 stocks
- Fundamentals from yfinance (offline)
- News via Perplexity live search
"""

_EXAMPLES = [
    ["Find 5 value stocks with P/E below 15 and ROE above 20%"],
    ["Show IT sector stocks with market cap above ₹50,000 Cr"],
    ["Best dividend-paying FMCG stocks"],
    ["Latest news on Reliance Industries"],
    ["Compare Infosys vs TCS fundamentals"],
    ["What happened in Indian markets this week?"],
    ["High-growth mid-cap stocks with low debt"],
    ["Top 10 pharma companies by market cap"],
    ["Which auto stocks have debt-to-equity below 0.5?"],
    ["HDFC Bank Q3 results and analyst commentary"],
]

_INGEST_HELP = """**Refresh modes**

| Mode | Description | Time |
|------|-------------|------|
| **Quick** | Top 50 stocks by market cap | ~5 min |
| **Incremental** | Stocks older than 7 days | varies |
| **Full** | All 500 stocks | 60–90 min |
| **Rebuild** | Re-index from existing data | ~10 sec |

Run via CLI for more control:
```
python scripts/ingest.py --quick
python scripts/ingest.py --symbols RELIANCE TCS
python scripts/ingest.py --full
```
"""

with gr.Blocks(title="NSE Stock Research Assistant") as demo:
    gr.HTML(_HEADER)

    with gr.Tabs():
        # ----------------------------------------------------------------
        # Tab 1: Chat
        # ----------------------------------------------------------------
        with gr.Tab("💬 Chat"):
            gr.HTML(_build_status_html())

            deps_state = gr.State(AgentDeps())

            with gr.Row(equal_height=False):
                with gr.Column(scale=4):
                    chatbot = gr.Chatbot(
                        height=560,
                        show_label=False,
                    )
                    with gr.Row():
                        msg = gr.Textbox(
                            placeholder="e.g. 'Find high-ROE IT stocks' or 'Latest news on HDFC Bank'",
                            show_label=False,
                            scale=9,
                            lines=1,
                            max_lines=4,
                            autofocus=True,
                        )
                        send_btn = gr.Button("Send", scale=1, variant="primary", min_width=72)
                    clear_btn = gr.Button("Clear chat", size="sm", variant="secondary")

                with gr.Column(scale=1, min_width=200):
                    gr.Markdown("**Examples**")
                    gr.Examples(examples=_EXAMPLES, inputs=msg, label=None)
                    gr.Markdown(_TIPS)

            send_btn.click(process_query, [msg, chatbot, deps_state], [chatbot, deps_state]).then(
                lambda: "", outputs=[msg]
            )
            msg.submit(process_query, [msg, chatbot, deps_state], [chatbot, deps_state]).then(
                lambda: "", outputs=[msg]
            )
            clear_btn.click(clear_chat, outputs=[chatbot, deps_state])

        # ----------------------------------------------------------------
        # Tab 2: Data Refresh
        # ----------------------------------------------------------------
        with gr.Tab("🔄 Data Refresh"):
            with gr.Row():
                with gr.Column(scale=2):
                    gr.Markdown("### Refresh NSE500 Stock Data")
                    gr.Markdown(
                        "Update the local stock database by fetching fresh data from yfinance. "
                        "Use **Quick** for a fast sanity-check or **Full** for a complete refresh."
                    )

                    with gr.Row():
                        btn_quick = gr.Button("⚡ Quick (top 50)", variant="primary")
                        btn_incremental = gr.Button("🔁 Incremental", variant="secondary")
                        btn_full = gr.Button("📥 Full (all 500)", variant="secondary")
                        btn_rebuild = gr.Button("🔨 Rebuild Index", variant="secondary")

                    ingest_log = gr.Textbox(
                        label="Output",
                        lines=20,
                        max_lines=30,
                        interactive=False,
                        placeholder="Click a button above to start refreshing data...",
                        autoscroll=True,
                        buttons=["copy"],
                    )

                with gr.Column(scale=1):
                    gr.Markdown(_INGEST_HELP)

            all_btns = [btn_quick, btn_incremental, btn_full, btn_rebuild]

            btn_quick.click(
                fn=run_ingest_streaming,
                inputs=[gr.State("quick"), ingest_log],
                outputs=[ingest_log] + all_btns,
            )
            btn_incremental.click(
                fn=run_ingest_streaming,
                inputs=[gr.State("incremental"), ingest_log],
                outputs=[ingest_log] + all_btns,
            )
            btn_full.click(
                fn=run_ingest_streaming,
                inputs=[gr.State("full"), ingest_log],
                outputs=[ingest_log] + all_btns,
            )
            btn_rebuild.click(
                fn=run_ingest_streaming,
                inputs=[gr.State("transform"), ingest_log],
                outputs=[ingest_log] + all_btns,
            )


if __name__ == "__main__":
    demo.launch(
        theme=gr.themes.Base(
            primary_hue="blue",
            neutral_hue="slate",
            font=[gr.themes.GoogleFont("Inter"), "system-ui", "sans-serif"],
        ),
        css="footer { display: none !important; }",
    )
