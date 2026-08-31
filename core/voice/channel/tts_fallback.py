"""TTS (Text-to-Speech) service with graceful fallback to text.

Handles TTS service failures, network degradation, and text fallback.

ADR-0352: Bidirectional Voice Channel
"""

import asyncio
import logging
from typing import Optional, Callable, Literal

logger = logging.getLogger(__name__)


class TTSFallbackStrategy:
    """Manages TTS failures and fallback to text."""

    FALLBACK_MODE_VOICE = "voice"
    FALLBACK_MODE_TEXT = "text"
    FALLBACK_MODE_NONE = "none"

    def __init__(
        self,
        tts_timeout_seconds: float = 5.0,
        tts_retry_count: int = 2,
        text_fallback_enabled: bool = True,
    ):
        """Initialize TTS fallback strategy.

        Args:
            tts_timeout_seconds: Max time to wait for TTS service
            tts_retry_count: Number of retries before fallback
            text_fallback_enabled: Allow degradation to text
        """
        self.tts_timeout_seconds = tts_timeout_seconds
        self.tts_retry_count = tts_retry_count
        self.text_fallback_enabled = text_fallback_enabled
        self.metrics = {
            "tts_attempts": 0,
            "tts_successes": 0,
            "tts_failures": 0,
            "tts_timeouts": 0,
            "text_fallback_count": 0,
            "none_fallback_count": 0,
            "avg_tts_latency_ms": 0.0,
            "tts_latencies": [],
        }

    async def synthesize_with_fallback(
        self,
        text: str,
        tts_service,
        on_text_fallback: Optional[Callable] = None,
    ) -> tuple[Literal["voice", "text", "none"], Optional[bytes]]:
        """Attempt TTS synthesis with fallback to text.

        Args:
            text: Text to synthesize
            tts_service: TTS service instance
            on_text_fallback: Callback when falling back to text

        Returns:
            (fallback_mode, audio_bytes or None)
            - "voice": TTS succeeded, audio_bytes is valid
            - "text": TTS failed, use text-based question
            - "none": Both failed, use default
        """
        import time

        self.metrics["tts_attempts"] += 1
        last_error = None

        # Attempt TTS with retries
        for attempt in range(self.tts_retry_count):
            try:
                start = time.time()
                audio = await asyncio.wait_for(
                    tts_service.synthesize(text),
                    timeout=self.tts_timeout_seconds,
                )
                latency_ms = (time.time() - start) * 1000
                self.metrics["tts_successes"] += 1
                self.metrics["tts_latencies"].append(latency_ms)
                if self.metrics["tts_latencies"]:
                    self.metrics["avg_tts_latency_ms"] = sum(
                        self.metrics["tts_latencies"]
                    ) / len(self.metrics["tts_latencies"])

                logger.debug(f"TTS succeeded (attempt {attempt + 1}, latency={latency_ms:.1f}ms)")
                return ("voice", audio)

            except asyncio.TimeoutError:
                self.metrics["tts_timeouts"] += 1
                last_error = "timeout"
                logger.warning(f"TTS timeout (attempt {attempt + 1})")

            except Exception as e:
                self.metrics["tts_failures"] += 1
                last_error = str(e)
                logger.warning(f"TTS failed (attempt {attempt + 1}): {e}")

        # All retries exhausted, fall back
        if self.text_fallback_enabled:
            self.metrics["text_fallback_count"] += 1
            logger.info(f"TTS exhausted; falling back to text (reason: {last_error})")
            if on_text_fallback:
                await on_text_fallback(text, last_error)
            return ("text", None)
        else:
            self.metrics["none_fallback_count"] += 1
            logger.warning("TTS exhausted; no fallback available")
            return ("none", None)

    async def get_metrics(self) -> dict:
        """Return TTS metrics."""
        return {
            "tts_attempts": self.metrics["tts_attempts"],
            "tts_successes": self.metrics["tts_successes"],
            "tts_failures": self.metrics["tts_failures"],
            "tts_timeouts": self.metrics["tts_timeouts"],
            "text_fallback_count": self.metrics["text_fallback_count"],
            "none_fallback_count": self.metrics["none_fallback_count"],
            "avg_tts_latency_ms": self.metrics["avg_tts_latency_ms"],
            "success_rate_pct": (
                (self.metrics["tts_successes"] / self.metrics["tts_attempts"] * 100)
                if self.metrics["tts_attempts"] > 0
                else 0.0
            ),
        }


class STTFallbackStrategy:
    """Manages STT (Speech-to-Text) failures and confidence validation."""

    def __init__(
        self,
        stt_timeout_seconds: float = 10.0,
        confidence_threshold_low: float = 0.50,
        confidence_threshold_high: float = 0.85,
        clarification_attempts: int = 2,
    ):
        """Initialize STT fallback strategy.

        Args:
            stt_timeout_seconds: Max time to wait for speech capture
            confidence_threshold_low: Below this, ask clarification
            confidence_threshold_high: Above this, accept immediately
            clarification_attempts: Max times to ask for clarification
        """
        self.stt_timeout_seconds = stt_timeout_seconds
        self.confidence_threshold_low = confidence_threshold_low
        self.confidence_threshold_high = confidence_threshold_high
        self.clarification_attempts = clarification_attempts
        self.metrics = {
            "stt_attempts": 0,
            "stt_successes": 0,
            "stt_timeouts": 0,
            "stt_failures": 0,
            "high_confidence_count": 0,
            "low_confidence_count": 0,
            "clarification_needed_count": 0,
            "avg_stt_latency_ms": 0.0,
            "stt_latencies": [],
        }

    async def capture_with_fallback(
        self,
        stt_service,
        max_duration_seconds: int = 10,
    ) -> Optional[bytes]:
        """Capture speech with fallback to default.

        Args:
            stt_service: STT service instance
            max_duration_seconds: Max duration to listen

        Returns:
            Audio bytes or None on failure/timeout
        """
        self.metrics["stt_attempts"] += 1

        try:
            import time

            start = time.time()
            audio = await asyncio.wait_for(
                stt_service.capture_speech(max_duration_seconds=max_duration_seconds),
                timeout=max_duration_seconds,
            )
            latency_ms = (time.time() - start) * 1000
            self.metrics["stt_successes"] += 1
            self.metrics["stt_latencies"].append(latency_ms)
            if self.metrics["stt_latencies"]:
                self.metrics["avg_stt_latency_ms"] = sum(
                    self.metrics["stt_latencies"]
                ) / len(self.metrics["stt_latencies"])

            return audio

        except asyncio.TimeoutError:
            self.metrics["stt_timeouts"] += 1
            logger.warning("STT timeout: no speech captured")
            return None

        except Exception as e:
            self.metrics["stt_failures"] += 1
            logger.error(f"STT capture failed: {e}")
            return None

    async def validate_confidence(
        self,
        text: str,
        confidence: float,
    ) -> tuple[bool, str]:
        """Validate transcription confidence.

        Args:
            text: Transcribed text
            confidence: STT confidence score [0.0-1.0]

        Returns:
            (is_valid, decision_reason)
        """
        if confidence >= self.confidence_threshold_high:
            self.metrics["high_confidence_count"] += 1
            return (True, "high_confidence")

        elif confidence < self.confidence_threshold_low:
            self.metrics["low_confidence_count"] += 1
            self.metrics["clarification_needed_count"] += 1
            return (False, "low_confidence_needs_clarification")

        else:
            # Medium confidence: accept but note
            return (True, "medium_confidence_accepted")

    async def get_metrics(self) -> dict:
        """Return STT metrics."""
        return {
            "stt_attempts": self.metrics["stt_attempts"],
            "stt_successes": self.metrics["stt_successes"],
            "stt_timeouts": self.metrics["stt_timeouts"],
            "stt_failures": self.metrics["stt_failures"],
            "high_confidence_count": self.metrics["high_confidence_count"],
            "low_confidence_count": self.metrics["low_confidence_count"],
            "clarification_needed_count": self.metrics["clarification_needed_count"],
            "avg_stt_latency_ms": self.metrics["avg_stt_latency_ms"],
            "success_rate_pct": (
                (self.metrics["stt_successes"] / self.metrics["stt_attempts"] * 100)
                if self.metrics["stt_attempts"] > 0
                else 0.0
            ),
        }
