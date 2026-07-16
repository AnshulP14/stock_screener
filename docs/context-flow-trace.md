# Context flow

What each LLM stage can actually see. Useful when a follow-up question ("compare the top 3",
"verify that number") behaves oddly — the cause is almost always something below.

## Flow

```
route_query_stream(deps, query, conversation_context, force_agent)
  │
  ├─ classify_intent(query, conversation_context)
  │    Prompt: "Recent conversation:" + formatted context + "Current user message:" + query
  │    Sees all prior turns, each truncated to 500 chars. No total cap.
  │    → RoutingDecision(agent, tasks, success_criteria)
  │
  ├─ _run_sub_agent_stream(deps, decision, summary, max_iterations)
  │    summary = formatted context capped at SUB_AGENT_CONTEXT_MAX_CHARS (1000)
  │    Prompt = _decision_to_prompt(): "Relevant prior conversation:" + tasks + success criteria
  │    message_history = deps.screening_history | deps.web_search_history (persists across turns)
  │    Loops until output.completed or MAX_ITERATIONS; each retry appends follow_up_suggestion.
  │    Writes: the history it used, plus deps.last_filters | deps.last_sources
  │
  └─ synthesize_response(query, decision, agent_output, agent_used, conversation_context)
       Context capped at SYNTHESIS_CONTEXT_MAX_CHARS (3000).
       Must not contradict agent_output or drop its [[n]](url) citation links.
```

## Who sees what

| Stage | Conversation context | Notes |
|-------|----------------------|-------|
| Router | Full prior turns (500 chars each) | Encodes what sub-agents need into `tasks` |
| Screening agent | Summary only (1000 chars) | Plus its own `message_history` |
| Web search agent | Summary only (1000 chars) | Plus its own `message_history` |
| Synthesis | Capped at 3000 chars | Formats the sub-agent's output |
| Tools | None | `RunContext[None]`; pure reads of `data/` |

Every agent also gets the registered context providers (e.g. current date) in its system prompt
via `with_default_context`.

## Consequences worth knowing

- **The router is the bottleneck for follow-ups.** Sub-agents never see the raw conversation, only
  the task list plus a 1000-char summary. If the router omits "IT sector" or "P/E 23.15" from the
  tasks, the sub-agent cannot recover it. This is why `ROUTER_SYSTEM_PROMPT` insists on copying
  exact numbers into verification tasks.
- **Per-agent history persists across topic switches.** `deps.screening_history` survives even when
  the next turn routes to web_search, so a later screening turn still sees the older screening
  conversation. Intentional for "continue" behaviour; clear `AgentDeps` to reset.
- **Tools get no deps.** The agents are declared without `deps_type` because nothing passes
  `deps=` to `.run()`. If a tool ever needs shared state, add `deps_type` *and* pass `deps=`
  through `llm_service` — doing only one is silently useless.
