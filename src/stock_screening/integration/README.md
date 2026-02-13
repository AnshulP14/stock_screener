# Jeeves Integration Module

This module provides integration with jeeves-core (Rust microkernel) and jeeves-airframe (Python infrastructure) for distributed agent orchestration, resource management, and production-ready deployment.

## Modules

### `jeeves_quota.py`

Defines resource quotas for agent execution:
- `ResourceQuota`: Dataclass with limits (LLM calls, tokens, hops, iterations)
- `get_quota_for_user()`: Get quota for a user (with per-user overrides)
- `set_user_quota()`: Set custom quota for a user

### `kernel_client.py`

Python wrapper for jeeves-core kernel client:
- `JeevesKernelClient`: Wrapper for kernel gRPC client
- `get_kernel_client()`: Get global kernel client instance
- Gracefully handles missing jeeves dependencies (returns None if unavailable)

### `agent_adapter.py`

Adapter for running agents via jeeves or directly:
- `AgentAdapter`: Adapter class that routes to jeeves or direct execution
- `get_agent_adapter()`: Get global adapter instance
- Falls back to direct execution if jeeves unavailable

## Usage

```python
from stock_screening.integration import (
    get_agent_adapter,
    get_kernel_client,
    ResourceQuota,
    get_quota_for_user,
)

# Run agent via adapter (uses jeeves if available)
adapter = get_agent_adapter(use_jeeves=True)
result = await adapter.run_agent(
    agent_type="screening",
    prompt="Find value stocks",
    session_id="session_123",
    user_id="user_456",
)

# Get quota for user
quota = get_quota_for_user("user_456")

# Access kernel client directly
kernel = get_kernel_client()
if kernel:
    process_id = await kernel.create_process(...)
```

## Integration Points

1. **Agent Execution**: `agent_adapter.py` wraps agent calls
2. **State Management**: Redis-backed via jeeves-airframe (when enabled)
3. **Resource Quotas**: Enforced by jeeves-core kernel
4. **Session Management**: Handled by jeeves OrchestrationService

## Configuration

Set environment variables:
- `JEEVES_ENABLED=true`: Enable jeeves integration
- `JEEVES_KERNEL_URL=localhost:50051`: Kernel gRPC URL
- `JEEVES_REDIS_URL=redis://localhost:6379`: Redis URL

See `docs/jeeves-integration.md` for full documentation.
