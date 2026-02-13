# Jeeves Migration Guide

Step-by-step guide for migrating stock_screening from direct agent execution to jeeves-core orchestration.

## Migration Phases

### Phase 1: Infrastructure Setup

**Goal**: Install and configure jeeves-core and supporting infrastructure.

#### Steps

1. **Install jeeves-core**:
   ```bash
   git clone https://github.com/Jeeves-Cluster-Organization/jeeves-core.git
   cd jeeves-core
   cargo build --release
   ```

2. **Install Python dependencies**:
   ```bash
   pip install -e ".[jeeves]"
   # Or: uv sync --extra jeeves
   ```

3. **Set up Redis**:
   ```bash
   # Install Redis (macOS)
   brew install redis
   
   # Start Redis
   redis-server
   ```

4. **Configure environment**:
   ```bash
   # .env file
   JEEVES_ENABLED=true
   JEEVES_KERNEL_URL=localhost:50051
   JEEVES_REDIS_URL=redis://localhost:6379
   ```

5. **Start jeeves-core kernel**:
   ```bash
   cd jeeves-core
   ./target/release/jeeves-core
   ```

#### Verification

```python
from stock_screening.integration import get_kernel_client

kernel = get_kernel_client()
assert kernel is not None, "Jeeves kernel client not available"
```

### Phase 2: Agent Wrapping

**Goal**: Wrap existing agents to run via jeeves adapter (with fallback to direct execution).

#### Steps

1. **Update agent calls to use adapter**:

   **Before** (`src/stock_screening/agents/main_agent.py`):
   ```python
   from stock_screening.models.llm_service import llm_service
   
   result = await llm_service.run_agent(screening_agent, prompt)
   ```

   **After**:
   ```python
   from stock_screening.integration import get_agent_adapter
   
   adapter = get_agent_adapter(use_jeeves=True)
   result = await adapter.run_agent(
       agent_type="screening",
       prompt=prompt,
       session_id=session_id,
       user_id=user_id,
   )
   ```

2. **Add session management**:

   ```python
   from stock_screening.integration import get_session_manager
   
   session_mgr = get_session_manager()
   session_id = await session_mgr.create_session(user_id=user_id)
   ```

3. **Test with fallback** (disable jeeves to verify direct execution still works):
   ```python
   adapter = get_agent_adapter(use_jeeves=False)
   ```

#### Files to Modify

- `src/stock_screening/agents/main_agent.py`: Use adapter for agent calls
- `src/stock_screening/agents/screening_agent.py`: (No changes, adapter handles wrapping)
- `src/stock_screening/agents/web_search_agent.py`: (No changes)

### Phase 3: State Migration

**Goal**: Replace in-memory `AgentDeps` with Redis-backed sessions.

#### Steps

1. **Update `AgentDeps` to use session manager**:

   **Before** (`src/stock_screening/agents/base.py`):
   ```python
   @dataclass
   class AgentDeps:
       screening_history: list[Any] = field(default_factory=list)
       web_search_history: list[Any] = field(default_factory=list)
   ```

   **After** (hybrid approach - keep AgentDeps but sync with Redis):
   ```python
   from stock_screening.integration import get_session_manager
   
   @dataclass
   class AgentDeps:
       session_id: str | None = None
       # ... existing fields ...
       
       async def sync_to_redis(self):
           if self.session_id:
               session_mgr = get_session_manager()
               await session_mgr.update_session(
                   self.session_id,
                   {
                       "screening_history": self.screening_history,
                       "web_search_history": self.web_search_history,
                   }
               )
   ```

2. **Update UI to use sessions**:

   **Before** (`src/stock_screening/ui/screening_ui.py`):
   ```python
   deps_state = gr.State(AgentDeps())
   ```

   **After**:
   ```python
   from stock_screening.integration import get_session_manager
   
   async def get_or_create_session(user_id: str):
       session_mgr = get_session_manager()
       # Get existing session or create new
       session_id = await session_mgr.create_session(user_id)
       deps = AgentDeps(session_id=session_id)
       return deps
   ```

#### Files to Modify

- `src/stock_screening/agents/base.py`: Add session_id to AgentDeps
- `src/stock_screening/ui/screening_ui.py`: Use session manager
- `src/stock_screening/agents/main_agent.py`: Sync state to Redis

### Phase 4: Gateway Migration

**Goal**: Replace Gradio UI with FastAPI/WebSocket gateway (via jeeves-airframe).

#### Steps

1. **Create FastAPI gateway** (`src/stock_screening/api/gateway.py`):

   ```python
   from fastapi import FastAPI, WebSocket
   from stock_screening.integration import get_agent_adapter, get_session_manager
   
   app = FastAPI()
   
   @app.post("/api/chat")
   async def chat(request: ChatRequest):
       adapter = get_agent_adapter(use_jeeves=True)
       session_mgr = get_session_manager()
       session_id = await session_mgr.create_session(request.user_id)
       
       result = await adapter.run_agent(
           agent_type="router",
           prompt=request.message,
           session_id=session_id,
           user_id=request.user_id,
       )
       return {"response": result}
   
   @app.websocket("/ws/chat")
   async def chat_stream(websocket: WebSocket):
       await websocket.accept()
       # Implement streaming via jeeves-airframe patterns
   ```

2. **Keep Gradio for development** (optional):

   ```python
   # src/stock_screening/ui/screening_ui.py
   # Keep existing Gradio UI for local development
   # Production uses FastAPI gateway
   ```

#### Files to Create

- `src/stock_screening/api/gateway.py`: FastAPI gateway
- `src/stock_screening/api/models.py`: Request/response models

### Phase 5: Observability & Production

**Goal**: Add metrics, tracing, and production features.

#### Steps

1. **Add resource quotas**:

   ```python
   from stock_screening.integration import ResourceQuota, set_user_quota
   
   # Set quotas per user tier
   set_user_quota("free_user", ResourceQuota(max_llm_calls=10))
   set_user_quota("premium_user", ResourceQuota(max_llm_calls=100))
   ```

2. **Configure rate limiting** (via jeeves-core):

   ```python
   # Rate limiting configured in jeeves-core kernel
   # Per-user sliding window limits
   ```

3. **Add observability** (via jeeves-airframe):

   ```python
   from jeeves_infra.observability import setup_metrics, setup_tracing
   
   setup_metrics()
   setup_tracing()
   ```

4. **Add interrupt handling**:

   ```python
   # Interrupts handled by jeeves-core:
   # - Clarification: Agent requests user input
   # - Confirmation: Agent seeks approval
   # - ResourceExhausted: Quota exceeded
   ```

## Testing Strategy

### Unit Tests

Test adapter fallback behavior:

```python
def test_adapter_fallback():
    adapter = get_agent_adapter(use_jeeves=False)
    result = await adapter.run_agent("screening", "test prompt")
    assert result is not None
```

### Integration Tests

Test with jeeves enabled:

```python
@pytest.mark.asyncio
async def test_jeeves_integration():
    # Requires jeeves-core running
    adapter = get_agent_adapter(use_jeeves=True)
    result = await adapter.run_agent(
        "screening",
        "test prompt",
        session_id="test_session",
    )
    assert result is not None
```

## Rollback Plan

If issues arise:

1. **Disable jeeves**: Set `JEEVES_ENABLED=false`
2. **Fallback to direct execution**: Adapter automatically falls back
3. **Keep Gradio UI**: Continue using existing UI
4. **Gradual re-enable**: Re-enable jeeves feature by feature

## Timeline Estimate

- **Phase 1**: 1-2 days (infrastructure setup)
- **Phase 2**: 2-3 days (agent wrapping)
- **Phase 3**: 2-3 days (state migration)
- **Phase 4**: 3-5 days (gateway migration)
- **Phase 5**: 2-3 days (observability)

**Total**: ~2-3 weeks for full migration

## Benefits After Migration

- ✅ Multi-user support
- ✅ Resource quotas and cost control
- ✅ Distributed execution
- ✅ Production-ready API gateway
- ✅ Observability (metrics, tracing)
- ✅ Rate limiting
- ✅ Fault isolation
