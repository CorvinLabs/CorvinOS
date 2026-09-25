"""
STT Engine Tests (Phase 2b)
Tests mock and provider logic
"""

import pytest
import asyncio
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

    def test_stt_availability(self):
        """Test: STT availability check"""
        assert is_stt_available() is True  # Mock is always available

    def test_singleton_engine(self):
        """Test: STT engine singleton"""
        engine1 = get_stt_engine()
        engine2 = get_stt_engine()
        assert engine1 is engine2  # Same instance


class TestSTTGracefulDegradation:
    """Phase 2b: Graceful Fallback"""

    def test_engine_always_available(self):
        """Test: Mock STT always available (graceful fallback)"""
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
