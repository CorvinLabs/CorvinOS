"""Speech-to-text backend provider - audio transcription.

Singleton registry for speech transcription services.
"""

import logging
from dataclasses import dataclass
from typing import Optional, Protocol
import threading

_logger = logging.getLogger(__name__)

_lock = threading.Lock()
_active_backend: Optional['STTBackend'] = None


@dataclass(frozen=True)
class TranscriptionResult:
    """Result of speech-to-text transcription."""
    audio_id: str
    tenant_id: str
    user_id: str
    text: str
    confidence: float
    language: str = "en"
    metadata: dict = None


class STTBackend(Protocol):
    """Protocol for STT backends."""

    async def transcribe_audio(self, audio_bytes: bytes, language: str = "en") -> TranscriptionResult:
        """Transcribe audio to text."""
        ...

    async def health_check(self) -> bool:
        """Check backend health."""
        ...


class DefaultSTTBackend:
    """Default in-process STT backend (stub - returns empty)."""

    def __init__(self):
        """Initialize the STT backend."""
        self._lock = threading.Lock()

    async def transcribe_audio(self, audio_bytes: bytes, language: str = "en") -> TranscriptionResult:
        """Transcribe audio to text."""
        try:
            # Default implementation: return empty transcription
            # Real implementation would call external STT service
            return TranscriptionResult(
                audio_id="unknown",
                tenant_id="unknown",
                user_id="unknown",
                text="",  # Empty: requires actual STT service
                confidence=0.0,
                language=language
            )
        except Exception as e:
            _logger.error(f"Transcription failed: {e}")
            raise

    async def health_check(self) -> bool:
        """Check backend health."""
        return True


def get_active() -> STTBackend:
    """Get the currently active STT backend."""
    global _active_backend
    with _lock:
        if _active_backend is None:
            _active_backend = DefaultSTTBackend()
        return _active_backend


def set_active(backend: STTBackend) -> None:
    """Set the active STT backend (for testing)."""
    global _active_backend
    with _lock:
        _active_backend = backend
