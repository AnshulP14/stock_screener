# Context flow through agents and tools

## 1. Trace: where context comes from and goes

### Entry points

| Entry | Source of context |
|-------|--------------------|
| **chat_stream** (UI) | `history` from UI → `conversation_context = history[:-1][-CONVERSATION_CONTEXT_MESSAGES:]` (last 10 turns, excluding current message). A **conversation summary** (formatted context trimmed to `SUB_AGENT_CONTEXT_MAX_CHARS`) is built each turn and passed to sub-agents. |
| **route_query** (CLI) | `conversation_context=cli_history[-CONVERSATION_CONTEXT_MESSAGES:]` (same cap); same summary for sub-agents. |

### Flow

```
chat_stream(deps, user_message, history)
  → conversation_context = history[:-1][-10:]
  → route_query_stream(deps, user_message, conversation_context)
       │
       ├─ classify_intent(query, conversation_context)
       │    • Prompt = "Recent conversation:\n" + _format_conversation_context(ctx) + "\n\nCurrent user message: " + query
       │    • Router sees full prior turns (each message truncated to 500 chars)
       │    • Output: RoutingDecision (agent, tasks, success_criteria)
       │
       ├─ conversation_summary = _conversation_summary_for_sub_agent(conversation_context)  (trimmed to SUB_AGENT_CONTEXT_MAX_CHARS)
       ├─ execute_decision(deps, decision, conversation_summary)
       │    │
       │    ├─ run_screening_agent(deps, decision, conversation_summary=...)
       │    │    • Prompt = _decision_to_prompt(decision, prior_context=summary): "Relevant prior conversation: ..." + tasks + success criteria
       │    │    • message_history = deps.screening_history (per-agent multi-turn)
       │    │    • llm_service.run_agent(screening_agent, prompt, message_history=...) — deps NOT passed to .run()
       │    │    • Tools: RunContext[None] — no deps, no conversation context
       │    │    • Writes: deps.screening_history, deps.last_filters
       │    │
       │    └─ run_web_search_agent(deps, decision, conversation_summary=...)
       │         • Same: prompt includes prior conversation summary + tasks; message_history = deps.web_search_history
       │         • deps not passed to agent.run()
       │         • Writes: deps.web_search_history, deps.last_sources
       │
       └─ synthesize_response(query, decision, agent_output, agent_used, conversation_context)
            • Prompt includes: formatted conversation_context (trimmed to 3000 chars), query, tasks, agent output
            • Output: final message string
```

### System / global context

- **with_default_context(agent)** adds registered context providers to every agent’s system prompt (e.g. `datetime_context()` → current date/time IST).
- **Router / synthesis / screening / web_search** all get that same global context; only router and synthesis get **conversation** context.

### AgentDeps (shared state)

| Field | Written by | Read by |
|-------|------------|--------|
| screening_history | run_screening_agent(_stream) | next screening run (multi-iteration / follow-up) |
| web_search_history | run_web_search_agent(_stream) | next web_search run |
| last_filters | screening agent | chat_stream → format_final_response |
| last_sources | web_search agent (from messages) | chat_stream → format_final_response |

So: **conversation_context** is only used by **router** and **synthesis**. **Sub-agents (screening, web_search) and tools never see prior user/assistant turns**; they only see the router’s task list and their own message_history.

---

## 2. Suggested improvements

### 2.1 Pass conversation context (or a summary) to sub-agents

**Issue:** Follow-ups like “Compare the top 3” or “Verify that number” rely entirely on the router encoding everything into tasks. If the router omits a detail (e.g. “IT sector”, “P/E 23”), the sub-agent has no way to recover it.

**Suggestion:** Add an optional “conversation summary” or last N turns to the sub-agent prompt (e.g. in `_decision_to_prompt` or a wrapper), truncated (e.g. 500–1000 chars). Keep the main prompt as tasks + success criteria; add one line like “Relevant prior context: …” so sub-agents can resolve pronouns and references.

### 2.2 Centralize conversation formatting and length limits

**Implemented:** `_format_conversation_context(messages, *, max_chars=None, per_message_chars=500)` in main_agent. Constants in `constants.py`: `SYNTHESIS_CONTEXT_MAX_CHARS`, `SUB_AGENT_CONTEXT_MAX_CHARS`. Router uses no total cap; synthesis uses `max_chars=SYNTHESIS_CONTEXT_MAX_CHARS`; sub-agent summary uses `max_chars=SUB_AGENT_CONTEXT_MAX_CHARS`.

### 2.3 Pass AgentDeps into sub-agent runs (if tools need it)

**Issue:** `screening_agent` is declared with `deps_type=AgentDeps`, but `_run_agent_loop` and streaming runners only pass `message_history` (and `model_settings` for web_search) to `agent.run()`. So tools use `RunContext[None]` and cannot access shared state.

**Suggestion:** If tools should ever use deps (e.g. “last screening result”, “current conversation”), pass `deps=deps` in `llm_service.run_agent(..., deps=deps)` and in the streaming path, and change tools to `RunContext[AgentDeps]`. If tools do not need deps, drop `deps_type=AgentDeps` from screening_agent and use `deps_type=None` (or the default) so the type matches usage.

### 2.4 Per-agent history lifecycle

**Issue:** `screening_history` and `web_search_history` live on the same `AgentDeps` instance across user turns. So when the user switches topic (e.g. screening → “latest TCS news”), the screening agent’s next run still sees the previous screening conversation. That can be useful for “continue” behavior but can also leak unrelated context.

**Suggestion:** Consider clearing or capping per-agent history when the router chooses a different agent than the previous turn (e.g. clear the other agent’s history, or cap each to last K turns). Alternatively, document that cross-session state is intentional and keep as-is.

### 2.5 Token/cost and observability

**Issue:** Conversation context is appended to router and synthesis prompts; variable context size affects token count and cost. No explicit logging of context length or token estimates.

**Suggestion:** Log length of formatted context (and optionally token estimate) in classify_intent and synthesize_response. Optionally add a simple tokenizer-based length check and trim to a `SYNTHESIS_CONTEXT_MAX_CHARS` (or token) limit so cost stays bounded.

---

## 3. Summary table

| Component | Receives conversation context? | Receives deps? | Notes |
|-----------|--------------------------------|----------------|--------|
| Router (classify_intent) | Yes (in prompt) | No | Formatted, per-msg 500 char |
| Synthesis | Yes (in prompt, SYNTHESIS_CONTEXT_MAX_CHARS) | No | |
| Screening agent | Yes (prior-context summary, SUB_AGENT_CONTEXT_MAX_CHARS) | No (deps not passed to .run()) | Uses deps.screening_history; prompt includes "Relevant prior conversation" |
| Web search agent | Yes (same summary) | No | Uses deps.web_search_history; prompt includes prior conversation |
| Tools (screen_stocks, etc.) | No | No (RunContext[None]) | |
