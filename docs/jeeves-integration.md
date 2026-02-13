# Jeeves Integration Guide

This document explains how to integrate jeeves-core and jeeves-airframe with the stock_screening project for production deployment with resource management, distributed execution, and multi-user support.

## Overview

**Jeeves** provides:
- **jeeves-core**: Rust microkernel for agent orchestration with process lifecycle, resource quotas, rate limiting, and interrupt handling
- **jeeves-airframe**: Python infrastructure layer with HTTP/WebSocket gateway, Redis state backend, and observability

## Architecture

```
User Request
    ↓
FastAPI/WebSocket Gateway (jeeves-airframe)
    ↓
Python Agent Runtime (jeeves-airframe)
    ↓ IPC (TCP + msgpack)
jeeves-core Kernel (Rust)
    ↓
Agent Process Execution (with quotas & rate limiting)
```

## Installation

### Prerequisites

1. **jeeves-core**: Rust binary (must be built from [jeeves-core repository](https://github.com/Jeeves-Cluster-Organization/jeeves-core))
2. **Redis**: For distributed state backend
3. **Python dependencies**: Install jeeves integration extras

```bash
# Install Python dependencies
pip install -e ".[jeeves]"

# Or with uv
uv sync --extra jeeves
```

### Setup

1. **Start jeeves-core kernel**:
   ```bash
   # Build jeeves-core (from jeeves-core repo)
   cargo build --release
   
   # Run kernel (default port: 50051)
   ./target/release/jeeves-core
   
   # Or custom port
   JEEVES_GRPC_PORT=50052 ./target/release/jeeves-core
   ```

2. **Start Redis** (if using distributed state):
   ```bash
   redis-server
   ```

3. **Configure environment variables**:
   ```bash
   # Enable jeeves integration
   export JEEVES_ENABLED=true
   export JEEVES_KERNEL_URL=localhost:50051
   export JEEVES_REDIS_URL=redis://localhost:6379
   ```

## Usage

### Basic Integration

The integration provides an adapter layer that works with or without jeeves:

```python
from stock_screening.integration import get_agent_adapter

# Get adapter (automatically uses jeeves if available and enabled)
adapter = get_agent_adapter(use_jeeves=True)

# Run agent via jeeves (or direct execution if jeeves unavailable)
result = await adapter.run_agent(
    agent_type="screening",
    prompt="Find value stocks with low P/E",
    session_id="session_123",
    user_id="user_456",
)
```

### Resource Quotas

Define resource limits per user/session:

```python
from stock_screening.integration import ResourceQuota, set_user_quota

# Set custom quota for premium user
premium_quota = ResourceQuota(
    max_llm_calls=100,
    max_output_tokens=200_000,
    max_agent_hops=20,
    max_iterations=50,
)
set_user_quota("premium_user_123", premium_quota)
```

### Kernel Client

Direct access to jeeves-core kernel:

```python
from stock_screening.integration import get_kernel_client

kernel = get_kernel_client()
if kernel:
    process_id = await kernel.create_process(
        agent_type="screening",
        session_id="session_123",
        initial_state={"prompt": "..."},
        quota={"max_llm_calls": 50, ...},
    )
    await kernel.schedule_process(process_id)
```

## Migration Path

### Phase 1: Infrastructure Setup

1. Install jeeves-core and jeeves-airframe
2. Set up Redis
3. Configure environment variables

### Phase 2: Agent Wrapping

Agents can be gradually migrated to use jeeves:

```python
# Current: Direct execution
from stock_screening.agents.main_agent import route_query

# With jeeves: Via adapter
from stock_screening.integration import get_agent_adapter
adapter = get_agent_adapter()
result = await adapter.run_agent(...)
```

### Phase 3: State Migration

Replace in-memory `AgentDeps` with Redis-backed state:

```python
# Current: In-memory state
from stock_screening.agents.base import AgentDeps
deps = AgentDeps()

# With jeeves: Redis-backed sessions
# State managed by jeeves-airframe via OrchestrationService
```

### Phase 4: Gateway Migration

Replace Gradio UI with FastAPI/WebSocket gateway:

```python
# Current: Gradio UI
from stock_screening.ui.screening_ui import demo
demo.launch()

# With jeeves: FastAPI gateway (via jeeves-airframe)
from jeeves_infra.gateway import create_gateway_app
app = create_gateway_app()
# Run with uvicorn
```

## Benefits

### 1. Resource Management

- **Cost control**: Enforce limits on LLM calls and tokens
- **Budget protection**: Prevent runaway costs
- **Quota enforcement**: Per-user resource limits

### 2. Distributed Architecture

- **Multi-user support**: Concurrent sessions
- **Horizontal scaling**: Scale agents across processes/machines
- **Fault isolation**: Process failures don't crash entire system

### 3. Production Features

- **Observability**: Metrics and tracing
- **Rate limiting**: Per-user sliding window limits
- **Interrupt handling**: Human-in-the-loop (clarification, confirmation)

## Configuration

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `JEEVES_ENABLED` | `false` | Enable jeeves integration |
| `JEEVES_KERNEL_URL` | `localhost:50051` | jeeves-core kernel gRPC URL |
| `JEEVES_REDIS_URL` | `None` | Redis URL for distributed state |

### Resource Quotas

Default quotas (can be overridden per user):

- `max_llm_calls`: 50
- `max_output_tokens`: 100,000
- `max_agent_hops`: 10
- `max_iterations`: 20

## Fallback Behavior

If jeeves is not available or disabled, the integration falls back to direct agent execution (current behavior). This allows gradual migration:

1. **Development**: Use direct execution (no jeeves)
2. **Staging**: Enable jeeves for testing
3. **Production**: Full jeeves integration with quotas and rate limiting

## Troubleshooting

### jeeves-core not running

```
RuntimeError: Jeeves kernel client not available
```

**Solution**: Start jeeves-core kernel:
```bash
./target/release/jeeves-core
```

### Redis connection failed

```
ConnectionError: Could not connect to Redis
```

**Solution**: Start Redis server:
```bash
redis-server
```

### Import errors

```
ImportError: No module named 'jeeves_infra'
```

**Solution**: Install jeeves integration extras:
```bash
pip install -e ".[jeeves]"
```

## References

- [jeeves-core](https://github.com/Jeeves-Cluster-Organization/jeeves-core): Rust microkernel
- [jeeves-airframe](https://github.com/Jeeves-Cluster-Organization/jeeves-airframe): Python infrastructure layer
