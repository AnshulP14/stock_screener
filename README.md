# Stock Screening — Multi-Agent System

A chat-based stock research assistant for **Indian NSE500 stocks**. You ask in natural language; an LLM **router** classifies the intent and routes to either **screening** (fundamental filters over local data) or **web search** (news via Perplexity). A **synthesis** step formats the final answer. Built with **Pydantic AI**.

## Architecture

Every query follows one path — there is no second, non-streaming implementation:

```
route_query_stream(deps, query, context, force_agent)
  ├─ classify_intent()        → RoutingDecision(agent, tasks, success_criteria)   [Gemini]
  ├─ _run_sub_agent_stream()  → screening_agent | web_search_agent, looped up to
  │                             MAX_ITERATIONS until output.completed             [Claude | GPT]
  └─ synthesize_response()    → final user-facing message                         [Gemini]
```

It yields `routing` → `tool_call`* → `final` events. The `final` event carries the response plus a
trace merging token usage and USD cost across all three LLM stages.

`route_query` is a thin wrapper that drains the stream and returns just the response.

## Project Structure

```
src/stock_screening/
├── api.py                  # FastAPI: SSE chat endpoint + data-refresh job runner
├── static/index.html       # Chat UI (vanilla JS, no build step)
├── config.py               # Settings — every field overridable by env var
├── logging_config.py
├── agents/
│   ├── base.py             # AgentDeps: per-session state
│   ├── main_agent.py       # Router, sub-agent loop, synthesis, cost merge, CLI
│   ├── screening_agent.py  # Screening agent + its tools
│   └── web_search_agent.py # Web search agent + Perplexity search_web tool
├── models/
│   ├── outputs.py          # Structured agent outputs (field descriptions are prompts)
│   ├── types.py            # AgentType
│   ├── constants.py        # Iteration/context limits, allowed news domains
│   ├── llm_factory.py      # get_model(agent_or_role) → cached Pydantic AI model
│   ├── llm_service.py      # run_agent (retry) + run_agent_stream (tool events)
│   └── context_providers.py# Dynamic system-prompt context (e.g. current date)
└── tools/
    └── stock_screener.py   # screen_stocks, get_companies, list_* — reads data/
```

Data lives in `data/` as JSON produced by `scripts/`, not in a database.

## Setup

```bash
uv sync                          # or: pip install -e .
python scripts/setup_data.py     # downloads data/ — required, see below
```

The data set is **not in git**. It lives as a zip on Google Drive and `setup_data.py` fetches and
unpacks it into `data/`. Point it at the link with `--url`, `$STOCK_SCREENING_DATA_URL`, or by
filling in `DATA_URL` in the script. This step is not optional: importing the package reads
`data/indices/by_industry.json` at module load, so without it even `import stock_screening` fails.

Set keys in `.env` (project root) or `~/.env`:

- `GOOGLE_API_KEY` — router + synthesis (required for every query)
- `ANTHROPIC_API_KEY` — screening agent
- `OPENAI_API_KEY` — web search agent
- `PERPLEXITY_API_KEY` — the `search_web` tool

### Model roles

| Role | Model | Configured by |
|------|-------|---------------|
| Router | Gemini (thinking on) | `LLM_ROUTER_MODEL_ID`, `LLM_ROUTER_THINKING_LEVEL` |
| Synthesis | Same Gemini (thinking off) | `LLM_ROUTER_MODEL_ID` |
| Screening | Claude | `LLM_ANTHROPIC_MODEL_ID`, `LLM_ANTHROPIC_THINKING_BUDGET` |
| Web search | OpenAI Responses | `LLM_OPENAI_RESPONSES_MODEL_ID` |
| `search_web` tool | Perplexity | `LLM_PERPLEXITY_MODEL_ID` |

Roles map to builders in `llm_factory._BUILDERS`; agents map to roles via `AGENT_MODEL_MAP`.

## Run

**Web UI:**

```bash
uv run uvicorn stock_screening.api:app --reload
# http://127.0.0.1:8000
```

The UI streams responses, shows each tool call with its args and output, and displays the
token/cost breakdown. "Refresh data" re-runs the ingest pipeline and streams its log.

**CLI:**

```bash
uv run python -m stock_screening.agents.main_agent                    # interactive
uv run python -m stock_screening.agents.main_agent "high ROE IT stocks"
```

## Extending

- **New tool**: add a function to `tools/stock_screener.py`, then register it on
  `screening_agent` — the docstring and `Annotated[..., Field(description=...)]` text *are* the
  LLM's documentation.
- **New agent**: add a module under `agents/`, add a value to `AgentType`, map it in
  `AGENT_MODEL_MAP`, and add a branch in `_run_sub_agent_stream`.

See `.claude/skills/stock-screening/SKILL.md` for the maintenance guide — house style, invariants,
and how to verify a change without a test suite.

## Data

`data/` is gitignored and distributed as a zip on Drive — fetch it with `scripts/setup_data.py`.
The screener reads those JSON files directly on every call; there is no database.

`scripts/ingest.py` regenerates `data/companies/*.json` and `data/indices/*.json` from source. The
UI's "Refresh data" button runs it as a subprocess and streams its log — see `/data/refresh` in
`api.py`.

To publish a refreshed data set:

```bash
zip -rq dist/stock-screening-data.zip data/companies data/indices
```

Upload it to Drive, share as "Anyone with the link", then point `setup_data.py` at that link.
Paths inside the zip are relative to the project root, so it unpacks as `data/companies/…` and
`data/indices/…`.
