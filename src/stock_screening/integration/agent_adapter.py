"""Adapter for wrapping stock_screening agents as jeeves processes.

Provides compatibility layer between current Pydantic AI agents and jeeves-core process model.
"""

import logging
from typing import Any

from stock_screening.integration.jeeves_quota import ResourceQuota, get_quota_for_user
from stock_screening.integration.kernel_client import get_kernel_client

logger = logging.getLogger(__name__)


class AgentAdapter:
    """Adapter for running agents via jeeves-core or directly (fallback).

    When jeeves is available, agents run as managed processes with resource quotas.
    When jeeves is not available, agents run directly (current behavior).
    """

    def __init__(self, use_jeeves: bool = True):
        """Initialize adapter.

        Args:
            use_jeeves: If True, use jeeves-core when available; otherwise always use direct execution.
        """
        self.use_jeeves = use_jeeves
        self._kernel_client = get_kernel_client() if use_jeeves else None

    async def run_agent(
        self,
        agent_type: str,
        prompt: str,
        session_id: str | None = None,
        user_id: str | None = None,
        message_history: list[Any] | None = None,
        **kwargs: Any,
    ) -> Any:
        """Run an agent via jeeves or directly.

        Args:
            agent_type: Type of agent ("screening", "web_search", "router", "synthesis").
            prompt: Agent prompt.
            session_id: Session identifier (for jeeves).
            user_id: User identifier (for quota lookup).
            message_history: Message history for agent.
            **kwargs: Additional arguments passed to agent.

        Returns:
            Agent result.
        """
        # If jeeves is not available or disabled, use direct execution
        if not self._kernel_client:
            return await self._run_direct(agent_type, prompt, message_history, **kwargs)

        # Use jeeves-core for execution
        quota = get_quota_for_user(user_id)
        process_id = await self._kernel_client.create_process(
            agent_type=agent_type,
            session_id=session_id or "default",
            initial_state={"prompt": prompt, "message_history": message_history},
            quota=quota.to_dict(),
        )
        await self._kernel_client.schedule_process(process_id)
        # Wait for completion and get result
        # This is simplified - actual implementation would poll or use async callbacks
        state = await self._kernel_client.get_process_state(process_id)
        return state.get("result")

    async def _run_direct(
        self,
        agent_type: str,
        prompt: str,
        message_history: list[Any] | None = None,
        **kwargs: Any,
    ) -> Any:
        """Run agent directly (current behavior, no jeeves).

        Args:
            agent_type: Type of agent.
            prompt: Agent prompt.
            message_history: Message history.
            **kwargs: Additional arguments.

        Returns:
            Agent result.
        """
        from stock_screening.models.llm_service import llm_service
        from stock_screening.agents.screening_agent import screening_agent
        from stock_screening.agents.web_search_agent import web_search_agent
        from stock_screening.agents.main_agent import router_agent, synthesis_agent

        # Map agent_type to actual agent instance
        agent_map = {
            "screening": screening_agent,
            "web_search": web_search_agent,
            "router": router_agent,
            "synthesis": synthesis_agent,
        }

        agent = agent_map.get(agent_type)
        if not agent:
            raise ValueError(f"Unknown agent type: {agent_type}")

        kwargs_clean = {k: v for k, v in kwargs.items() if v is not None}
        if message_history:
            kwargs_clean["message_history"] = message_history

        return await llm_service.run_agent(agent, prompt, **kwargs_clean)


# Global adapter instance
_adapter: AgentAdapter | None = None


def get_agent_adapter(use_jeeves: bool = True) -> AgentAdapter:
    """Get global agent adapter instance.

    Args:
        use_jeeves: If True, use jeeves when available.

    Returns:
        AgentAdapter instance.
    """
    global _adapter
    if _adapter is None:
        _adapter = AgentAdapter(use_jeeves=use_jeeves)
    return _adapter
