"""Summary backend provider - content summarization.

Singleton registry for text/conversation summarization.
"""

import logging
from typing import Optional, Protocol
import threading

_logger = logging.getLogger(__name__)

_lock = threading.Lock()
_active_backend: Optional['SummaryBackend'] = None


class SummaryBackend(Protocol):
    """Protocol for summary backends."""

    async def summarize_text(self, text: str, max_length: int = 100) -> str:
        """Summarize text content."""
        ...

    async def summarize_conversation(self, turns: list[dict], max_length: int = 200) -> str:
        """Summarize a conversation."""
        ...

    async def health_check(self) -> bool:
        """Check backend health."""
        ...


class DefaultSummaryBackend:
    """Default in-process summary backend (simple truncation)."""

    def __init__(self):
        """Initialize the summary backend."""
        self._cache: dict[str, str] = {}
        self._lock = threading.Lock()

    async def summarize_text(self, text: str, max_length: int = 100) -> str:
        """Summarize text (simple truncation)."""
        try:
            if len(text) <= max_length:
                return text
            return text[:max_length] + "..."
        except Exception as e:
            _logger.error(f"Summarization failed: {e}")
            return ""

    async def summarize_conversation(self, turns: list[dict], max_length: int = 200) -> str:
        """Summarize a conversation."""
        try:
            summary_parts = []
            for turn in turns[:5]:  # Take first 5 turns
                role = turn.get("role", "unknown")
                content = turn.get("content", "")
                summary_parts.append(f"{role}: {content[:50]}")
            summary = " | ".join(summary_parts)
            if len(summary) > max_length:
                summary = summary[:max_length] + "..."
            return summary
        except Exception as e:
            _logger.error(f"Conversation summarization failed: {e}")
            return ""

    async def health_check(self) -> bool:
        """Check backend health."""
        return True


def get_active() -> SummaryBackend:
    """Get the currently active summary backend."""
    global _active_backend
    with _lock:
        if _active_backend is None:
            _active_backend = DefaultSummaryBackend()
        return _active_backend


def set_active(backend: SummaryBackend) -> None:
    """Set the active summary backend (for testing)."""
    global _active_backend
    with _lock:
        _active_backend = backend
