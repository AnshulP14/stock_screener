---
name: stock-screening
description: Maintain the stock_screening codebase — its layout, invariants, prompt-as-code rules, and how to verify changes without a test suite. Use when editing anything under stock_screening/src, adding a screener filter/tool/agent, changing models or prompts, or touching the API or chat UI.
---

# Maintaining stock_screening

A router LLM classifies a query, one sub-agent answers it, a synthesis LLM formats the reply.
Read `README.md` for the map and `docs/context-flow-trace.md` for what each stage can see.

## House style

The codebase was deliberately stripped down. Keep it that way.

- **No verbose comments.** Comment only to state a constraint the code cannot show — a provider
  quirk, a data-shape gotcha, a "why not the obvious thing". Never narrate what the next line does,
  never leave changelog notes ("refactored to…", "previously…"), never restate the signature in the
  docstring.
- **One way to do a thing.** There is a single execution path — streaming. Do not add a parallel
  non-streaming implementation; `route_query` already wraps the stream. If you find yourself
  writing a second variant of an existing runner, unify instead.
- **No re-export facades.** Import from the module that defines the symbol. A `foo.py` that only
  re-exports from `foo_impl.py` will be deleted.
- **Delete dead code, don't keep it "just in case".** Git has it. Unused config fields, unused
  model builders, and unreferenced helpers are churn — every one of them was removed once already.

## Prompts are code

Text that reaches an LLM is behaviour, not documentation. Changing it changes output.

- `Annotated[..., Field(description=...)]` on `screen_stocks` params and every `Field(description=)`
  in `models/outputs.py` is **read by the model**. The strategy recipes (VALUE/GROWTH/QUALITY/GARP)
  in the `screen_stocks` docstring are how the LLM knows what to set. Don't "tidy" them away.
- A tool's docstring is its spec to the model. Keep the `Args:` block accurate — especially the
  list of valid `sections`.
- `ROUTER_SYSTEM_PROMPT` demands exact numbers be copied into verification tasks. That is load
  bearing: sub-agents never see the raw conversation (see `docs/context-flow-trace.md`).
- `SYNTHESIS_SYSTEM_PROMPT` forbids dropping `[[1]](url)` citation links. Web search answers embed
  them; losing them silently strips all sourcing.

## Invariants

- **`Industry` is a `Literal` built at import time** from `data/indices/by_industry.json`
  (`stock_screener._industry_names`). Importing the package therefore requires that file to exist.
  If imports fail with a file-not-found, the data is missing — run `python scripts/setup_data.py`.
  It is not a code bug, so do not "fix" it by making the Literal lazy or wrapping it in a try.
- **`data/` is gitignored and not in the repo.** It ships as a zip on Drive. Never `git add` it
  back, and never commit data files alongside a code change.
- **The screener reads JSON from disk on every call.** No caching, no database. `data/` is the
  source of truth, produced by `scripts/ingest.py`.
- **Agents are declared without `deps_type` and tools use `RunContext[None]`.** Nothing passes
  `deps=` to `.run()`. If a tool ever needs shared state you must add `deps_type` *and* thread
  `deps=` through `llm_service` — doing only one is silently useless.
- **Missing values are not zero.** In this data set a `0` or `None` metric means "not reported".
  `_passes()` fails a company on a `None` metric, except `debt_to_equity_max`, where `None` passes
  (`_FILTERS` marks it `none_passes=True`). The historical revenue column prints `N/A` for a falsy
  value. These asymmetries are intentional — preserve them.
- **Cost is merged across three LLM calls** (router + sub-agent + synthesis) in
  `route_query_stream`. A new stage that calls an LLM must add its usage there or the reported
  cost silently under-reports.

## Common tasks

**Add a screener filter:** add the param with an `Annotated[... Field(description=...)]` explaining
*when to use it* → add one row to `_FILTERS` mapping it to its company field, bound, and None
semantics. `_passes` and `screen_stocks` need no other change.

**Add a screener tool:** write the function in `tools/stock_screener.py`, register it on
`screening_agent` with `@screening_agent.tool`. `with_default_context(screening_agent)` must stay
at the bottom of that module — context providers are registered after tools.

**Add an agent:** new module in `agents/`, add an `AgentType` value, map it in `AGENT_MODEL_MAP`,
add a branch in `_run_sub_agent_stream`, and mention it in `ROUTER_SYSTEM_PROMPT` (otherwise the
router will never pick it).

**Change a model:** roles live in `llm_factory._BUILDERS` (`router`, `synthesis`, `claude`,
`openai_responses`); agents map to roles via `AGENT_MODEL_MAP`. Models are cached in `_REGISTRY`,
built lazily on first `get_model()`. Every knob is a `config.Settings` field — add settings there,
not as literals.

**Edit the UI:** it is `static/index.html`, plain HTML/CSS/JS, no build step. `api.py` serves it.
Do not inline it back into Python.

## Verifying a change

**There is no test suite.** `pytest` is configured but `tests/` does not exist. So verify by
running things — do not claim a refactor is safe because it imports.

```bash
source .venv/bin/activate
uvx ruff check src/
python -c "import stock_screening.api"          # Industry Literal + all wiring
```

For any change to the screener or its formatters, **differential-test it**: transcribe the old
implementation into a scratch script and compare output against the new one across all 500
companies and a spread of filter combos. This is how the refactor was validated, and it caught a
real regression (a falsy-vs-`None` revenue check) that only affected 2 of 500 companies.

```python
syms = sorted(p.stem for p in stock_screener.COMPANIES_DIR.glob("*.json"))
for s in syms:
    assert old_profile(s) == stock_screener.get_companies([s])
```

Live end-to-end (costs a few cents, needs keys in `.env`):

```bash
python -m stock_screening.agents.main_agent "3 IT services stocks with high ROE"   # screening path
python -m stock_screening.agents.main_agent "latest news on Infosys earnings"      # web search path
```

Check the API without spending tokens via `fastapi.testclient.TestClient(api.app)`: `GET /` should
return the UI, and `POST /chat/stream` with an empty message should stream a single error event.
