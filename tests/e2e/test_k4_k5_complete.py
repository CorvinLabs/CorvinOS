import pytest
import asyncio
from core.voice.natural_voice_integration import NaturalVoiceIntegration, VoiceContext


@pytest.mark.asyncio
async def test_voice_streaming():
    """k=4: Voice streaming works."""
    voice = NaturalVoiceIntegration()
    chunks = []
    async for chunk in voice.stream_response("Hello world", "chat1"):
        chunks.append(chunk)
    assert len(chunks) > 0
    assert any(c.get("type") == "tts_chunk" for c in chunks)


@pytest.mark.asyncio
async def test_voice_interruption():
    """k=4: Voice interruption works."""
    voice = NaturalVoiceIntegration()
    voice.streaming_active = True
    result = []
    async for event in voice.interrupt("chat1"):
        result.append(event)
    assert result[0]["type"] == "interrupt"


@pytest.mark.asyncio
async def test_voice_context():
    """k=4: Voice context tracking works."""
    voice = NaturalVoiceIntegration()
    await voice.record_user_turn("hello", "chat1", tone="friendly")
    context = voice.get_context("chat1")
    assert context is not None
    assert len(context.last_3_turns) == 1

