"""Jeeves kernel client wrapper.

Provides Python interface to jeeves-core kernel via gRPC.
"""

import logging
from typing import Any

logger = logging.getLogger(__name__)

# Try to import jeeves-airframe kernel client
try:
    from jeeves_infra.kernel_client import KernelClient as JeevesKernelClientBase
    JEEVES_AVAILABLE = True
except ImportError:
    JEEVES_AVAILABLE = False
    JeevesKernelClientBase = None
    logger.warning(
        "jeeves_infra not available. Install with: pip install jeeves-infra"
    )


class JeevesKernelClient:
    """Wrapper for jeeves-core kernel client.

    Provides a simplified interface for creating processes, managing sessions,
    and enforcing resource quotas.
    """

    def __init__(self, kernel_url: str = "localhost:50051"):
        """Initialize kernel client.

        Args:
            kernel_url: gRPC URL for jeeves-core kernel (default: localhost:50051).
        """
        if not JEEVES_AVAILABLE:
            raise RuntimeError(
                "jeeves_infra is not installed. Install with: pip install jeeves-infra"
            )
        self._client = JeevesKernelClientBase(kernel_url)
        self._kernel_url = kernel_url

    async def create_process(
        self,
        agent_type: str,
        session_id: str,
        initial_state: dict[str, Any] | None = None,
        quota: dict[str, int] | None = None,
    ) -> str:
        """Create a new agent process.

        Args:
            agent_type: Type of agent ("screening", "web_search", etc.).
            session_id: Session identifier.
            initial_state: Initial state/envelope data.
            quota: Resource quota dict (from ResourceQuota.to_dict()).

        Returns:
            Process ID.
        """
        if not JEEVES_AVAILABLE:
            raise RuntimeError("Jeeves kernel client not available")
        # Implementation would call jeeves-core KernelService.CreateProcess
        # This is a placeholder - actual implementation depends on jeeves-airframe API
        logger.info(
            f"Creating process: agent_type={agent_type}, session_id={session_id}"
        )
        # Placeholder return - actual implementation needed
        return f"proc_{agent_type}_{session_id}"

    async def schedule_process(self, process_id: str) -> None:
        """Schedule a process for execution.

        Args:
            process_id: Process ID returned by create_process.
        """
        if not JEEVES_AVAILABLE:
            raise RuntimeError("Jeeves kernel client not available")
        logger.info(f"Scheduling process: {process_id}")
        # Placeholder - actual implementation needed

    async def get_process_state(self, process_id: str) -> dict[str, Any]:
        """Get current state of a process.

        Args:
            process_id: Process ID.

        Returns:
            Process state/envelope data.
        """
        if not JEEVES_AVAILABLE:
            raise RuntimeError("Jeeves kernel client not available")
        logger.info(f"Getting process state: {process_id}")
        # Placeholder - actual implementation needed
        return {}

    async def terminate_process(self, process_id: str) -> None:
        """Terminate a process.

        Args:
            process_id: Process ID.
        """
        if not JEEVES_AVAILABLE:
            raise RuntimeError("Jeeves kernel client not available")
        logger.info(f"Terminating process: {process_id}")
        # Placeholder - actual implementation needed


# Global kernel client instance (lazy initialization)
_kernel_client: JeevesKernelClient | None = None


def get_kernel_client() -> JeevesKernelClient | None:
    """Get global kernel client instance.

    Returns:
        JeevesKernelClient instance if jeeves is available, None otherwise.
    """
    global _kernel_client
    if not JEEVES_AVAILABLE:
        return None
    if _kernel_client is None:
        from stock_screening.config import get_settings

        settings = get_settings()
        kernel_url = getattr(settings, "jeeves_kernel_url", "localhost:50051")
        _kernel_client = JeevesKernelClient(kernel_url)
    return _kernel_client
