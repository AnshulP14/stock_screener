"""Session management for jeeves integration.

Manages user sessions and state persistence via Redis (when jeeves enabled)
or in-memory (fallback).
"""

import logging
from typing import Any
from uuid import uuid4

logger = logging.getLogger(__name__)

# Try to import Redis client
try:
    import redis.asyncio as redis
    REDIS_AVAILABLE = True
except ImportError:
    REDIS_AVAILABLE = False
    redis = None
    logger.warning("Redis not available. Sessions will be in-memory only.")


class SessionManager:
    """Manages user sessions with Redis backend (or in-memory fallback)."""

    def __init__(self, redis_url: str | None = None):
        """Initialize session manager.

        Args:
            redis_url: Redis URL (e.g., "redis://localhost:6379"). If None, uses in-memory storage.
        """
        self._redis_client: redis.Redis | None = None
        self._in_memory_sessions: dict[str, dict[str, Any]] = {}
        self._use_redis = False

        if redis_url and REDIS_AVAILABLE:
            try:
                self._redis_client = redis.from_url(redis_url)
                self._use_redis = True
                logger.info(f"Session manager using Redis: {redis_url}")
            except Exception as e:
                logger.warning(f"Failed to connect to Redis: {e}. Using in-memory storage.")
                self._use_redis = False

    async def create_session(self, user_id: str | None = None) -> str:
        """Create a new session.

        Args:
            user_id: Optional user identifier.

        Returns:
            Session ID.
        """
        session_id = str(uuid4())
        session_data = {
            "session_id": session_id,
            "user_id": user_id,
            "created_at": None,  # Would use timestamp in real implementation
            "state": {},
        }

        if self._use_redis and self._redis_client:
            await self._redis_client.setex(
                f"session:{session_id}",
                3600,  # 1 hour TTL
                str(session_data),  # Would serialize properly in real implementation
            )
        else:
            self._in_memory_sessions[session_id] = session_data

        logger.info(f"Created session: {session_id} (user: {user_id})")
        return session_id

    async def get_session(self, session_id: str) -> dict[str, Any] | None:
        """Get session data.

        Args:
            session_id: Session ID.

        Returns:
            Session data dict, or None if not found.
        """
        if self._use_redis and self._redis_client:
            data = await self._redis_client.get(f"session:{session_id}")
            if data:
                # Would deserialize properly in real implementation
                return eval(data) if isinstance(data, bytes) else data
            return None
        else:
            return self._in_memory_sessions.get(session_id)

    async def update_session(
        self, session_id: str, updates: dict[str, Any]
    ) -> None:
        """Update session data.

        Args:
            session_id: Session ID.
            updates: Dict of updates to apply.
        """
        session = await self.get_session(session_id)
        if not session:
            raise ValueError(f"Session not found: {session_id}")

        session.update(updates)

        if self._use_redis and self._redis_client:
            await self._redis_client.setex(
                f"session:{session_id}",
                3600,
                str(session),  # Would serialize properly in real implementation
            )
        else:
            self._in_memory_sessions[session_id] = session

    async def delete_session(self, session_id: str) -> None:
        """Delete a session.

        Args:
            session_id: Session ID.
        """
        if self._use_redis and self._redis_client:
            await self._redis_client.delete(f"session:{session_id}")
        else:
            self._in_memory_sessions.pop(session_id, None)
        logger.info(f"Deleted session: {session_id}")

    async def close(self) -> None:
        """Close Redis connection (if using Redis)."""
        if self._redis_client:
            await self._redis_client.aclose()


# Global session manager instance
_session_manager: SessionManager | None = None


def get_session_manager() -> SessionManager:
    """Get global session manager instance.

    Returns:
        SessionManager instance.
    """
    global _session_manager
    if _session_manager is None:
        from stock_screening.config import get_settings

        settings = get_settings()
        redis_url = getattr(settings, "jeeves_redis_url", None)
        _session_manager = SessionManager(redis_url=redis_url)
    return _session_manager
