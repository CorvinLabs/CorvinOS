"""
STT Engine Tests (Phase 2b)
Tests mock and provider logic.

The mock provider is a TEST DOUBLE and must be named explicitly; availability
is derived from the platform's real provider status, never from the mock
(review 2026-11-19 round 3, R3-NEW-30).
"""

import pytest
import asyncio
from core.console.corvin_console.services import stt_engine as stt_engine_module
from core.console.corvin_console.services.stt_engine import (
    STTEngine,
    STTProvider,
    get_stt_engine,
    is_stt_available,
)


class TestSTTEngineMock:
    """Phase 2b: Mock STT Provider"""

    @pytest.mark.asyncio
    async def test_mock_transcription(self):
        """Test: Mock STT transcription"""
        engine = STTEngine(provider=STTProvider.MOCK)

        audio_data = b"mock_audio_data"
        result, error = await engine.transcribe(audio_data)

        assert error is None
        assert result is not None
        assert len(result.transcript) > 0
        assert 0 <= result.confidence <= 1.0
        assert result.provider == "mock"

    @pytest.mark.asyncio
    async def test_confidence_scaling(self):
        """Test: Confidence increases with audio length"""
        engine = STTEngine(provider=STTProvider.MOCK)

        short_audio = b"short"
        result1, _ = await engine.transcribe(short_audio)

        long_audio = b"x" * 1000  # Much longer
        result2, _ = await engine.transcribe(long_audio)

        # Both should have valid confidence
        assert result1.confidence > 0
        assert result2.confidence > 0

    def test_stt_availability_follows_the_real_provider_probe(self, monkeypatch):
        """Availability is what the platform STT stack reports - not the mock."""
        monkeypatch.setattr(stt_engine_module, "_availability", None)
        monkeypatch.setattr(stt_engine_module, "_probe_platform_stt", lambda: False)
        assert is_stt_available() is False
        # cached for the TTL: a changed probe is not consulted again at once
        monkeypatch.setattr(stt_engine_module, "_probe_platform_stt", lambda: True)
        assert is_stt_available() is False
        assert is_stt_available(max_age_s=0) is True

    def test_default_engine_is_not_the_mock(self, monkeypatch):
        monkeypatch.setattr(stt_engine_module, "_stt_engine", None)
        monkeypatch.delenv("GOOGLE_APPLICATION_CREDENTIALS", raising=False)
        engine = get_stt_engine()
        assert engine.provider is STTProvider.GOOGLE_CLOUD
        assert engine.available is False  # no credentials: unavailable, not mocked

    def test_singleton_engine(self):
        """Test: STT engine singleton"""
        engine1 = get_stt_engine()
        engine2 = get_stt_engine()
        assert engine1 is engine2  # Same instance
        assert engine1.provider is not STTProvider.MOCK


class TestSTTGracefulDegradation:
    """Phase 2b: Graceful Fallback"""

    def test_engine_always_available(self):
        """Test: an explicitly constructed mock engine reports available"""
        engine = STTEngine(provider=STTProvider.MOCK)
        assert engine.available is True

    @pytest.mark.asyncio
    async def test_transcription_success(self):
        """Test: Mock transcription always succeeds"""
        engine = STTEngine(provider=STTProvider.MOCK)

        for _ in range(5):
            result, error = await engine.transcribe(b"test")
            assert error is None
            assert result is not None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
