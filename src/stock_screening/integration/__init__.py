"""Jeeves integration module for stock_screening.

Provides integration with jeeves-core (Rust microkernel) and jeeves-airframe (Python infrastructure)
for distributed agent orchestration, resource management, and production-ready deployment.
"""

from stock_screening.integration.agent_adapter import (
    AgentAdapter,
    get_agent_adapter,
)
from stock_screening.integration.jeeves_quota import (
    DEFAULT_RESOURCE_QUOTA,
    ResourceQuota,
    get_quota_for_user,
    set_user_quota,
)
from stock_screening.integration.kernel_client import (
    JeevesKernelClient,
    get_kernel_client,
)
from stock_screening.integration.session_manager import (
    SessionManager,
    get_session_manager,
)

__all__ = [
    "AgentAdapter",
    "get_agent_adapter",
    "DEFAULT_RESOURCE_QUOTA",
    "ResourceQuota",
    "get_quota_for_user",
    "set_user_quota",
    "JeevesKernelClient",
    "get_kernel_client",
    "SessionManager",
    "get_session_manager",
]
