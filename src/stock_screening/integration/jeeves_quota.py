"""Resource quota definitions for jeeves-core integration.

Defines resource limits per user/session to prevent cost overruns and enforce budgets.
"""

from dataclasses import dataclass


@dataclass
class ResourceQuota:
    """Resource quota for agent execution.

    Limits are enforced by jeeves-core kernel to prevent runaway costs and infinite loops.
    """

    max_llm_calls: int = 50
    """Maximum number of LLM API calls per session."""

    max_output_tokens: int = 100_000
    """Maximum total output tokens generated per session."""

    max_agent_hops: int = 10
    """Maximum pipeline depth (agent → agent → ...)."""

    max_iterations: int = 20
    """Maximum iterations per agent execution (prevents infinite loops)."""

    def to_dict(self) -> dict[str, int]:
        """Convert quota to dictionary for jeeves-core API."""
        return {
            "max_llm_calls": self.max_llm_calls,
            "max_output_tokens": self.max_output_tokens,
            "max_agent_hops": self.max_agent_hops,
            "max_iterations": self.max_iterations,
        }


# Default quota for all users (can be overridden per user)
DEFAULT_RESOURCE_QUOTA = ResourceQuota(
    max_llm_calls=50,
    max_output_tokens=100_000,
    max_agent_hops=10,
    max_iterations=20,
)

# Per-user quota overrides (example: premium users get higher limits)
_USER_QUOTAS: dict[str, ResourceQuota] = {}


def get_quota_for_user(user_id: str | None = None) -> ResourceQuota:
    """Get resource quota for a user.

    Args:
        user_id: User identifier. If None, returns default quota.

    Returns:
        ResourceQuota instance for the user.
    """
    if user_id and user_id in _USER_QUOTAS:
        return _USER_QUOTAS[user_id]
    return DEFAULT_RESOURCE_QUOTA


def set_user_quota(user_id: str, quota: ResourceQuota) -> None:
    """Set custom quota for a user.

    Args:
        user_id: User identifier.
        quota: ResourceQuota instance.
    """
    _USER_QUOTAS[user_id] = quota
