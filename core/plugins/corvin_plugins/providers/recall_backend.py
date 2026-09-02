"""Recall backend provider - user conversation history.

Singleton registry for conversation recall and context retrieval.
"""

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional, Protocol
import threading

_logger = logging.getLogger(__name__)

_lock = threading.Lock()
_active_backend: Optional['RecallBackend'] = None


@dataclass(frozen=True)
class ConversationTurn:
    """A single turn in a conversation."""
    turn_id: str
    tenant_id: str
    user_id: str
    role: str  # "user", "assistant"
    content: str
    timestamp: str


class RecallBackend(Protocol):
    """Protocol for recall backends."""

    async def store_turn(self, turn: ConversationTurn) -> bool:
        """Store a conversation turn."""
        ...

    async def get_turns(self, tenant_id: str, user_id: str, limit: int = 10) -> list[ConversationTurn]:
        """Get recent conversation turns."""
        ...

    async def search_turns(self, tenant_id: str, user_id: str, query: str) -> list[ConversationTurn]:
        """Search conversation history."""
        ...

    async def clear_turns(self, tenant_id: str, user_id: str) -> int:
        """Clear conversation history (GDPR Art. 17)."""
        ...

    async def health_check(self) -> bool:
        """Check backend health."""
        ...


class DefaultRecallBackend:
    """Default in-process recall backend."""

    def __init__(self):
        """Initialize the recall backend."""
        self._turns: list[ConversationTurn] = []
        self._lock = threading.Lock()

    async def store_turn(self, turn: ConversationTurn) -> bool:
        """Store a conversation turn."""
        try:
            with self._lock:
                self._turns.append(turn)
                _logger.debug(f"Turn stored: {turn.turn_id}")
            return True
        except Exception as e:
            _logger.error(f"Failed to store turn: {e}")
            return False

    async def get_turns(self, tenant_id: str, user_id: str, limit: int = 10) -> list[ConversationTurn]:
        """Get recent conversation turns."""
        try:
            with self._lock:
                matching = [t for t in self._turns
                            if t.tenant_id == tenant_id and t.user_id == user_id]
                return sorted(matching, key=lambda x: x.timestamp, reverse=True)[:limit]
        except Exception as e:
            _logger.error(f"Failed to get turns: {e}")
            return []

    async def search_turns(self, tenant_id: str, user_id: str, query: str) -> list[ConversationTurn]:
        """Search conversation history."""
        try:
            with self._lock:
                return [t for t in self._turns
                        if t.tenant_id == tenant_id
                        and t.user_id == user_id
                        and query.lower() in t.content.lower()]
        except Exception:
            return []

    async def clear_turns(self, tenant_id: str, user_id: str) -> int:
        """Clear conversation history (GDPR Art. 17)."""
        try:
            with self._lock:
                before = len(self._turns)
                self._turns = [t for t in self._turns
                               if not (t.tenant_id == tenant_id and t.user_id == user_id)]
                deleted = before - len(self._turns)
                _logger.info(f"Cleared {deleted} turns for {user_id}")
                return deleted
        except Exception as e:
            _logger.error(f"Failed to clear turns: {e}")
            return 0

    async def health_check(self) -> bool:
        """Check backend health."""
        return True


def get_active() -> RecallBackend:
    """Get the currently active recall backend."""
    global _active_backend
    with _lock:
        if _active_backend is None:
            _active_backend = DefaultRecallBackend()
        return _active_backend


def set_active(backend: RecallBackend) -> None:
    """Set the active recall backend (for testing)."""
    global _active_backend
    with _lock:
        _active_backend = backend
