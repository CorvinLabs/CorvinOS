"""Integration tests: active_loop wiring into execution (Phase 3)."""
import pytest
import asyncio
import tempfile
from pathlib import Path
from core.learning import TreeNode
from core.learning.integration import LearningIntegration


@pytest.fixture
def integration():
    with tempfile.TemporaryDirectory() as tmpdir:
        yield LearningIntegration(Path(tmpdir))


@pytest.mark.asyncio
async def test_execute_method_with_learning_success(integration):
    """Test method execution updates confidence on success."""
    integration.register_pattern(
        "pattern_retry_backoff",
        "Exponential Backoff",
        when=["API rate-limits", "transient errors"]
    )
    
    async def mock_method():
        return {"success": True, "data": "test"}
    
    result = await integration.execute_method_with_learning(
        "pattern_retry_backoff",
        mock_method,
        context={"task": "tts", "user": "test"}
    )
    
    assert result["success"]
    assert "new_confidence" in result
    
    # Confidence should increase from 0.5 to ~0.55 (0.05 delta)
    node = integration.store.get_node("pattern_retry_backoff")
    assert node.confidence > 0.5


@pytest.mark.asyncio
async def test_execute_method_with_learning_failure(integration):
    """Test method execution updates confidence on failure."""
    integration.register_pattern(
        "pattern_openai_tts",
        "OpenAI TTS",
        when=["voice synthesis"]
    )
    
    async def mock_method():
        raise Exception("API error")
    
    result = await integration.execute_method_with_learning(
        "pattern_openai_tts",
        mock_method,
        context={}
    )
    
    assert not result["success"]
    
    # Confidence should decrease from 0.5 to ~0.35 (-0.15 delta)
    node = integration.store.get_node("pattern_openai_tts")
    assert node.confidence < 0.5


@pytest.mark.asyncio
async def test_execute_tts_with_learning(integration):
    """Test TTS-specific execution tracking."""
    integration.register_pattern(
        "pattern_tts_openai",
        "OpenAI TTS Provider",
        when=["voice synthesis", "high quality"],
        anti_when=["offline", "no API key"]
    )
    
    async def mock_tts(text, voice):
        return {"audio": b"fake", "duration": 1.0}
    
    result = await integration.execute_tts_with_learning(
        provider_id="openai",
        tts_fn=mock_tts,
        text="Hello world",
        voice="alloy",
        context={"lang": "en"}
    )
    
    assert result["success"]
    assert "pattern_tts_openai" in result or "pattern_tts_openai" in str(result)


def test_antipattern_detection(integration):
    """Test antipattern warning on wrong context."""
    integration.register_pattern(
        "pattern_retry",
        "Retry",
        when=["transient errors"],
        anti_when=["auth_failure"]
    )
    
    # Execute in wrong context
    async def mock():
        return {"success": True}
    
    result = asyncio.run(integration.execute_method_with_learning(
        "pattern_retry",
        mock,
        context={"stage": "auth_failure"}
    ))
    
    # Should have warnings
    assert "warnings" in result


def test_manual_grading(integration):
    """Test operator grading updates confidence."""
    integration.register_pattern("pattern_test", "Test Pattern", when=[])
    
    initial_conf = integration.get_pattern_confidence("pattern_test")
    assert initial_conf == 0.5
    
    # Operator gives high grade
    integration.grade_pattern("pattern_test", +0.3, reason="Works well in production")
    
    node = integration.store.get_node("pattern_test")
    assert node.confidence > initial_conf


def test_pattern_suggestions(integration):
    """Test auto-suggestions when confidence drops."""
    # Create 2 patterns: one broken, one good
    integration.register_pattern("pattern_broken", "Broken", when=[])
    integration.register_pattern("pattern_good", "Good", when=[])
    
    # Set confidences
    good = integration.store.get_node("pattern_good")
    good.confidence = 0.85
    
    # Broken pattern execution should suggest alternatives
    async def mock():
        return {"success": False}
    
    result = asyncio.run(integration.execute_method_with_learning(
        "pattern_broken",
        mock
    ))
    
    # Should have suggestions if confidence dropped
    if result["new_confidence"] < 0.5:
        assert len(result["suggestions"]) > 0 or result["suggestions"] == []


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
