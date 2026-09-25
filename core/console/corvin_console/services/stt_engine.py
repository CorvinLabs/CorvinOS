"""
Speech-to-Text Engine (Phase 2b)
Abstraction layer for STT providers with graceful fallback

Supports:
- Google Cloud Speech-to-Text (production)
- Local mock STT (development/testing)
- Fallback mode when STT unavailable

@date 2026-09-25
@phase Phase 2b: Real STT Integration
"""

import asyncio
import logging
from typing import Optional, Tuple
from enum import Enum
import os

logger = logging.getLogger(__name__)


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
    Speech-to-Text engine with multi-provider support.
    Phase 2b: MVP with mock provider. Phase 2c: Real Google Cloud integration.
    """

    def __init__(self, provider: STTProvider = STTProvider.MOCK):
        self.provider = provider
        self.available = True
        self._validate_provider()

    def _validate_provider(self):
        """Check if provider is available"""
        if self.provider == STTProvider.GOOGLE_CLOUD:
            # Phase 2c: Check for Google Cloud credentials
            has_credentials = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS") is not None
            self.available = has_credentials
            if not has_credentials:
                logger.warning("Google Cloud STT credentials not found - using mock STT")
                self.provider = STTProvider.MOCK
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
            logger.warning("google-cloud-speech not installed - using mock STT")
            return await self._transcribe_mock(audio_data)
        except Exception as e:
            logger.error(f"Google Cloud STT error: {e}")
            return None, f"STT error: {str(e)}"

    async def _transcribe_mock(self, audio_data: bytes) -> Tuple[Optional[STTResult], Optional[str]]:
        """
        Mock STT for Phase 2b testing.
        Returns a simulated transcript with confidence.
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


def get_stt_engine(provider: STTProvider = STTProvider.MOCK) -> STTEngine:
    """Get or create STT engine instance"""
    global _stt_engine
    if _stt_engine is None:
        _stt_engine = STTEngine(provider=provider)
    return _stt_engine


def is_stt_available() -> bool:
    """Check if STT is available"""
    engine = get_stt_engine()
    return engine.available
