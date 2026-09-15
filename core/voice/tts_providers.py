"""
TTS Provider Abstraction — Quality-First with Fallback Strategy

Pattern: Primary provider (OpenAI, high-quality, paid) + fallback provider (Edge TTS, free).
Strategy: Try quality first; fallback to availability only on error.

Implements ADR-0201 (Budget/Fallback) + ADR-0445 (Resilience) patterns.
"""

import asyncio
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum
from typing import Optional, List
import os

logger = logging.getLogger(__name__)


class TTSProvider(Enum):
    """Available TTS providers"""
    OPENAI = "openai"
    EDGE_TTS = "edge_tts"


@dataclass
class AudioResult:
    """TTS synthesis result"""
    audio_bytes: bytes
    mime_type: str = "audio/mpeg"
    duration_ms: int = 0
    provider: str = "unknown"
    quality_score: float = 1.0  # 0.0 (low) to 1.0 (high)
    success: bool = True


class TTSProviderBase(ABC):
    """Abstract base for TTS providers"""

    def __init__(self, provider_name: str):
        self.provider_name = provider_name
        self.quality_score = 0.5  # Default quality

    @abstractmethod
    async def synthesize(
        self,
        text: str,
        voice: str = "en-US-AvaMultilingualNeural",
        language: str = "en-US"
    ) -> Optional[AudioResult]:
        """Synthesize text to speech. Returns AudioResult or None on failure."""
        pass

    @abstractmethod
    def is_available(self) -> bool:
        """Check if provider is available (credentials, deps, etc.)"""
        pass


class OpenAITTSProvider(TTSProviderBase):
    """OpenAI Text-to-Speech (High Quality, Paid)"""

    def __init__(self, api_key: Optional[str] = None):
        super().__init__("openai")
        self.api_key = api_key or os.getenv("OPENAI_API_KEY")
        self.quality_score = 0.95  # High quality

        try:
            import openai
            self.client = openai.AsyncOpenAI(api_key=self.api_key)
            self._imported = True
        except ImportError:
            self._imported = False
            logger.warning("OpenAI SDK not installed; OpenAI TTS unavailable")

    def is_available(self) -> bool:
        """Check if OpenAI API is configured"""
        return self._imported and bool(self.api_key)

    async def synthesize(
        self,
        text: str,
        voice: str = "en-US-AvaMultilingualNeural",
        language: str = "en-US"
    ) -> Optional[AudioResult]:
        """Synthesize using OpenAI Text-to-Speech API"""
        if not self.is_available():
            logger.warning("OpenAI TTS not available (missing API key or SDK)")
            return None

        try:
            # Map voice to OpenAI voice IDs
            voice_map = {
                "en-US-AvaMultilingualNeural": "nova",
                "en-US-AriaNeural": "shimmer",
                "en-US-GuyNeural": "onyx",
            }
            openai_voice = voice_map.get(voice, "nova")

            # Call OpenAI TTS API
            response = await self.client.audio.speech.create(
                model="tts-1-hd",  # High-definition model
                voice=openai_voice,
                input=text,
                speed=1.0
            )

            # Extract audio bytes
            audio_bytes = response.content if hasattr(response, 'content') else await response.read()

            return AudioResult(
                audio_bytes=audio_bytes,
                mime_type="audio/mpeg",
                duration_ms=int(len(text.split()) * 0.4 * 1000),  # Rough estimate
                provider="openai",
                quality_score=0.95,
                success=True
            )

        except Exception as e:
            logger.error(f"OpenAI TTS failed: {e}")
            return None


class EdgeTTSProvider(TTSProviderBase):
    """Microsoft Edge Text-to-Speech (Free, Reliable Fallback)"""

    def __init__(self):
        super().__init__("edge_tts")
        self.quality_score = 0.75  # Good quality, but lower than OpenAI

        try:
            import edge_tts
            self.edge_tts = edge_tts
            self._imported = True
        except ImportError:
            self._imported = False
            logger.warning("edge_tts not installed; Edge TTS unavailable")

    def is_available(self) -> bool:
        """Edge TTS is always available if imported (no API key needed)"""
        return self._imported

    async def synthesize(
        self,
        text: str,
        voice: str = "en-US-AvaMultilingualNeural",
        language: str = "en-US"
    ) -> Optional[AudioResult]:
        """Synthesize using Microsoft Edge TTS"""
        if not self.is_available():
            logger.warning("Edge TTS not available (SDK not installed)")
            return None

        try:
            # Create TTS communicator
            communicate = self.edge_tts.Communicate(
                text=text,
                voice=voice,
                language=language
            )

            # Collect audio data
            audio_chunks = []
            async for chunk in communicate.stream():
                if chunk["type"] == "audio":
                    audio_chunks.append(chunk["data"])

            audio_bytes = b"".join(audio_chunks)

            return AudioResult(
                audio_bytes=audio_bytes,
                mime_type="audio/mpeg",
                duration_ms=int(len(text.split()) * 0.4 * 1000),  # Rough estimate
                provider="edge_tts",
                quality_score=0.75,
                success=True
            )

        except Exception as e:
            logger.error(f"Edge TTS failed: {e}")
            return None


class QualityFirstTTSManager:
    """
    Quality-First TTS Manager with Fallback Strategy.

    Pattern: Try high-quality provider first; fallback to availability provider on failure.
    - Primary: OpenAI (high quality, paid)
    - Fallback: Edge TTS (good quality, free, always available)
    """

    def __init__(self):
        self.primary = OpenAITTSProvider()
        self.fallback = EdgeTTSProvider()
        self.last_provider_used = None

    async def synthesize(
        self,
        text: str,
        voice: str = "en-US-AvaMultilingualNeural",
        language: str = "en-US",
        force_provider: Optional[TTSProvider] = None
    ) -> Optional[AudioResult]:
        """
        Synthesize speech with quality-first fallback.

        Strategy:
        1. If force_provider set, use only that provider
        2. Otherwise: try primary (OpenAI), fallback to edge on failure
        3. Log which provider was used
        """

        # Force provider mode (debugging/testing)
        if force_provider == TTSProvider.OPENAI:
            return await self.primary.synthesize(text, voice, language)
        elif force_provider == TTSProvider.EDGE_TTS:
            return await self.fallback.synthesize(text, voice, language)

        # Quality-First strategy: try primary, fallback on failure
        if self.primary.is_available():
            logger.info("TTS: Trying primary provider (OpenAI)...")
            result = await self.primary.synthesize(text, voice, language)
            if result and result.success:
                self.last_provider_used = "openai"
                logger.info("✅ TTS: OpenAI synthesis succeeded")
                return result
            logger.warning("⚠️ TTS: OpenAI synthesis failed, attempting fallback...")

        # Fallback to Edge TTS
        if self.fallback.is_available():
            logger.info("TTS: Trying fallback provider (Edge TTS)...")
            result = await self.fallback.synthesize(text, voice, language)
            if result and result.success:
                self.last_provider_used = "edge_tts"
                logger.info("✅ TTS: Edge TTS synthesis succeeded (fallback)")
                return result

        # Both providers failed
        logger.error("❌ TTS: All providers failed; no audio available")
        return None

    def get_status(self) -> dict:
        """Return provider status"""
        return {
            "primary_available": self.primary.is_available(),
            "fallback_available": self.fallback.is_available(),
            "last_provider_used": self.last_provider_used,
            "primary_quality": self.primary.quality_score,
            "fallback_quality": self.fallback.quality_score,
        }


# Global singleton instance
_tts_manager = None


def get_tts_manager() -> QualityFirstTTSManager:
    """Get or create global TTS manager (singleton)"""
    global _tts_manager
    if _tts_manager is None:
        _tts_manager = QualityFirstTTSManager()
    return _tts_manager
