# Stock Screening — Multi-Agent System

A chat-based stock research assistant for **Indian NSE500 stocks**. You ask in natural language; an LLM **router** classifies your intent and routes to either **screening** (fundamental filters, screener tools) or **web search** (news, market reaction). A **synthesis** step formats the final answer. Built with **Pydantic AI** (agents + tools).

## Architecture

- **Main orchestrator** (`main_agent`): Classifies the user query → routes to one of two sub-agents → runs the chosen agent → synthesizes a final response (with optional follow-up suggestion).
- **Screening agent**: Interprets natural language (e.g. “value stocks with low P/E”), calls `screen_stocks` and related tools (sectors, industries, `get_stock_details`), returns structured output with applied filters.
- **Web search agent**: Handles news and “what happened in the markets” queries; uses web search and returns structured news items, market reaction, and sources.
- **Synthesis**: Same LLM as router (no thinking) formats the sub-agent result into a clear reply and may add a follow-up suggestion.

No LangGraph; orchestration is done in the main agent (router → execute → synthesize).

## Project Structure

```
stock_screening/
├── src/stock_screening/
│   ├── config.py              # Settings (API keys, model names)
│   ├── models/                # Pydantic request/response, state, LLM factory
│   │   ├── outputs.py         # ScreeningResponse, WebSearchResponse, RoutingDecision, etc.
│   │   ├── types.py           # AgentType (screening | web_search)
│   │   ├── llm.py             # Thin wrapper / context
│   │   ├── llm_factory.py     # get_model(agent_name), OpenAI/Anthropic/Google
│   │   └── ...
│   ├── tools/
│   │   └── stock_screener.py  # screen_stocks, get_stock_details, sectors, industries
│   ├── agents/
│   │   ├── base.py            # AgentDeps
│   │   ├── main_agent.py      # Router, route_query, chat_stream, run_*_agent_stream
│   │   ├── screening_agent.py # Screening agent + tools
│   │   └── web_search_agent.py# Web search agent
│   └── ui/
│       └── screening_ui.py    # Gradio chat UI (streaming, tool cards, trace/cost)
├── pyproject.toml
└── README.md
```

## Setup

```bash
cd stock_screening
uv sync   # or: pip install -e .
```

Set environment variables (or use `.env`). Copy `.env.example` and set keys for the providers you use:

- `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `GOOGLE_API_KEY` (set those needed for router and chosen sub-agents)
- `DEFAULT_LLM=openai` | `claude` | `gemini` (used by `get_default_model()`; router/agents use their own mapping)
- Optional: `FINANCIAL_API_KEY`, `WEB_SEARCH_API_KEY` for tools

### Per-Agent Model Assignment

| Role       | Model / API              | Use case                          |
|-----------|---------------------------|-----------------------------------|
| **Router**| Gemini (e.g. gemini-3-flash-preview) | Intent classification, tasks, success criteria |
| **Synthesis** | Same Gemini (no thinking) | Format final response             |
| **Screening** | Claude (e.g. claude-sonnet-4-5) | Tool use, filter interpretation   |
| **Web search** | OpenAI Responses (gpt-4o) | Web search, news summarization    |

Config (see `config.py` and `llm_factory.py`):

| Env / concept | Purpose |
|---------------|--------|
| `DEFAULT_LLM` | Default provider for `get_default_model()`; router/agents use `AGENT_MODEL_MAP` |
| `LLM_OPENAI_*` | Model ID, temperature, max_tokens, reasoning_effort |
| `LLM_OPENAI_RESPONSES_MODEL_ID` | Model for web search agent (e.g. gpt-4o) |
| `LLM_ANTHROPIC_*` | Model ID, temperature, max_tokens, thinking_budget |
| `LLM_GOOGLE_*` | Model ID, temperature, max_tokens, thinking_level / thinking_budget |
| `LLM_ROUTER_*` | Router (and synthesis) model and thinking settings |

## Usage

**Gradio UI (recommended):**

```bash
uv run python -m stock_screening.ui.screening_ui
```

Then ask e.g. “Find 5 value stocks with low P/E and high ROE” or “Latest news on Reliance Industries”.

**CLI (main agent, optional query):**

```bash
uv run python -m stock_screening.agents.main_agent "Find IT sector stocks with market cap above 50000 crores"
# Or no args for interactive mode
uv run python -m stock_screening.agents.main_agent
```

The UI streams responses, shows tool calls (with args and truncated results), and displays token/cost breakdown (router, agent, synthesis).

## Jeeves Integration (Optional)

For production deployment with resource management, distributed execution, and multi-user support, see [Jeeves Integration Guide](docs/jeeves-integration.md).

**Features:**
- Resource quotas (LLM calls, tokens, iterations)
- Distributed architecture (multi-user, horizontal scaling)
- Production-ready API gateway (FastAPI/WebSocket)
- Rate limiting and cost control
- Observability (metrics, tracing)

**Installation:**
```bash
pip install -e ".[jeeves]"
# Requires: jeeves-core (Rust), Redis
```

See `docs/jeeves-integration.md` and `docs/jeeves-migration-guide.md` for details.

## Extending

- **New agent**: Add a module under `agents/`, define a Pydantic AI `Agent` and output model, then in `main_agent` add a branch in the router output (e.g. new `AgentType`), an `execute` branch, and optionally a streaming runner.
- **New tool**: Add under `tools/` and register on the screening (or other) agent.
- **New routing branch**: Extend the router’s structured output (e.g. new agent type), then add the corresponding execution and streaming path in `main_agent`.
