"""
Speech-to-Text availability for the Voice Summary routes.

What is real here: :func:`is_stt_available` asks the platform's own STT stack
(``stt.provider_status()``, the same introspection the Console voice-status
panel shows) whether a provider can transcribe right now.

What is NOT real: :class:`STTEngine` with ``STTProvider.MOCK``. It returns one
of four canned sentences picked by a hash of the input - it transcribes
nothing. It exists for unit tests and must be constructed explicitly; no
production path selects it. Until 2026-11-19 ``get_stt_engine()`` defaulted to
the mock, a missing Google credential or package silently fell back to it, and
``/chat/voice/health`` therefore reported "healthy" on an install with no
speech-to-text at all (review round 3, R3-NEW-30).
"""

import asyncio
import logging
import threading
import time
from typing import Optional, Tuple
from enum import Enum
import os

logger = logging.getLogger(__name__)


#: Seconds an availability verdict is reused. ``provider_status()`` imports
#: packages and stats model files; the health route may be polled.
_AVAILABILITY_TTL_S = 30.0
_availability: Optional[Tuple[float, bool]] = None
_availability_lock = threading.Lock()


def _probe_platform_stt() -> bool:
    """True when at least one real STT provider reports ``ready``.

    BLOCKING (imports, file stats) - async callers use ``asyncio.to_thread``.
    Any failure to ask means "not available": nothing is assumed."""
    try:
        from stt import provider_status  # type: ignore[import-not-found]  # noqa: PLC0415
    except Exception:  # noqa: BLE001 - the voice stack is not importable here
        try:
            # voice.py puts the voice scripts on sys.path at import time.
            from ..routes import voice as _voice  # noqa: F401,PLC0415
            from stt import provider_status  # type: ignore[import-not-found]  # noqa: PLC0415
        except Exception:  # noqa: BLE001
            return False
    try:
        status = provider_status()
    except Exception:  # noqa: BLE001
        logger.warning("STT provider status unavailable", exc_info=True)
        return False
    return any(bool((info or {}).get("ready")) for info in status.values()
               if isinstance(info, dict))


def is_stt_available(*, max_age_s: float = _AVAILABILITY_TTL_S) -> bool:
    """Whether a real speech-to-text provider is usable right now (cached for
    ``max_age_s``). BLOCKING on a cache miss. Never answered from the mock."""
    global _availability
    now = time.monotonic()
    with _availability_lock:
        if _availability is not None and now - _availability[0] < max_age_s:
            return _availability[1]
    verdict = _probe_platform_stt()
    with _availability_lock:
        _availability = (time.monotonic(), verdict)
    return verdict


class STTProvider(str, Enum):
    GOOGLE_CLOUD = "google_cloud"
    MOCK = "mock"


class STTResult:
    def __init__(self, transcript: str, confidence: float, provider: str):
        self.transcript = transcript
        self.confidence = confidence
        self.provider = provider


class STTEngine:
    """
    Speech-to-Text engine. The provider is ALWAYS chosen by the caller:

    * ``GOOGLE_CLOUD`` - real; unavailable (never mocked) without credentials
      or without the ``google-cloud-speech`` package.
    * ``MOCK`` - canned sentences for unit tests. Not a transcription.
    """

    def __init__(self, provider: STTProvider):
        self.provider = provider
        self.available = True
        self._validate_provider()

    def _validate_provider(self):
        """Check if provider is available"""
        if self.provider == STTProvider.GOOGLE_CLOUD:
            # No credentials -> unavailable. It used to switch itself to the
            # mock provider and keep reporting available.
            self.available = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS") is not None
            if not self.available:
                logger.warning("Google Cloud STT credentials not found - STT unavailable")
        else:
            self.available = True

    async def transcribe(self, audio_data: bytes) -> Tuple[Optional[STTResult], Optional[str]]:
        """
        Transcribe audio to text.

        Args:
            audio_data: Raw audio bytes (or mock transcript for Phase 2b)

        Returns:
            (STTResult, error_message)
            If error: (None, error_message)
        """
        if not self.available:
            return None, "STT provider unavailable - graceful fallback active"

        try:
            if self.provider == STTProvider.GOOGLE_CLOUD:
                return await self._transcribe_google_cloud(audio_data)
            else:
                return await self._transcribe_mock(audio_data)
        except Exception as e:
            logger.error(f"STT transcription error: {e}")
            return None, f"STT error: {str(e)}"

    async def _transcribe_google_cloud(self, audio_data: bytes) -> Tuple[Optional[STTResult], Optional[str]]:
        """
        Transcribe using Google Cloud Speech-to-Text API (Phase 3b).
        Requires GOOGLE_APPLICATION_CREDENTIALS or ADC.
        """
        try:
            from google.cloud import speech_v1
            from google.api_core import exceptions as google_exceptions

            client = speech_v1.SpeechClient()
            config = speech_v1.RecognitionConfig(
                encoding=speech_v1.RecognitionConfig.AudioEncoding.LINEAR16,
                sample_rate_hertz=16000,
                language_code="en-US",
            )
            audio = speech_v1.RecognitionAudio(content=audio_data)

            try:
                response = client.recognize(config=config, audio=audio)
            except google_exceptions.GoogleAPIError as e:
                logger.error(f"Google Cloud STT API error: {e}")
                return None, f"Google Cloud STT API error: {str(e)}"

            if response.results:
                result = response.results[0]
                if result.alternatives:
                    transcript = result.alternatives[0].transcript
                    confidence = result.alternatives[0].confidence
                    return STTResult(transcript, confidence, "google_cloud"), None

            return None, "No transcription results from Google Cloud STT"
        except ImportError:
            logger.warning("google-cloud-speech not installed - STT unavailable")
            return None, "STT provider unavailable: google-cloud-speech is not installed"
        except Exception as e:
            logger.error(f"Google Cloud STT error: {e}")
            return None, f"STT error: {str(e)}"

    async def _transcribe_mock(self, audio_data: bytes) -> Tuple[Optional[STTResult], Optional[str]]:
        """
        TEST DOUBLE. Returns one of four canned sentences chosen by a hash of
        the input and an invented confidence. Reached only through an engine
        constructed with ``STTProvider.MOCK``.
        """
        # Phase 2b: Mock implementation
        # In real usage, audio_data would contain raw audio bytes
        # For now, we simulate it

        await asyncio.sleep(0.5)  # Simulate processing

        # Mock transcript (in real usage, this would come from the audio)
        mock_transcripts = [
            "Can you help me route requests efficiently?",
            "This code needs to be more modular.",
            "Show me how the learning system works.",
            "What's the best strategy for optimization?",
        ]

        # Simple heuristic: longer audio → higher confidence
        confidence = min(0.95, 0.7 + (len(audio_data) % 100) / 100)

        transcript = mock_transcripts[hash(audio_data) % len(mock_transcripts)]

        return STTResult(transcript, confidence, self.provider.value), None

    async def transcribe_stream(self, audio_chunks):
        """
        Streaming transcription (for live/continuous recording).
        Phase 2c: Implement streaming mode.
        """
        accumulated_transcript = ""

        for chunk in audio_chunks:
            result, error = await self.transcribe(chunk)
            if error:
                logger.error(f"Streaming STT error: {error}")
                continue
            if result:
                accumulated_transcript += " " + result.transcript

        return accumulated_transcript.strip()


# Singleton instance
_stt_engine: Optional[STTEngine] = None


def get_stt_engine(provider: STTProvider = STTProvider.GOOGLE_CLOUD) -> STTEngine:
    """Get or create the STT engine instance. The default is the REAL provider
    (unavailable without credentials); the mock must be asked for by name."""
    global _stt_engine
    if _stt_engine is None:
        _stt_engine = STTEngine(provider=provider)
    return _stt_engine
