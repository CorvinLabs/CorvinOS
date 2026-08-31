"""Phase 3d: Hermes + Event Bus E2E Tests."""

import pytest
from unittest.mock import AsyncMock, MagicMock

from ..hermes_bridge import HermesBridge, HermesResponse, HermesRequest
from ..event_broadcaster import EventBroadcaster, StatusLevel, StatusEvent, ConsoleNotifier, DiscordNotifier


@pytest.fixture
def hermes_bridge():
    return HermesBridge()

@pytest.fixture
def event_broadcaster():
    return EventBroadcaster()


@pytest.mark.asyncio
async def test_hermes_fallback_timeout(hermes_bridge):
    """Test: Hermes fallback handles timeout errors."""
    error = TimeoutError("Task timed out after 30s")
    context = {"current_skill": "code_analysis"}

    response = await hermes_bridge.diagnose(error, context)

    assert response.primary_strategy == "retry"
    assert response.confidence > 0
    assert "timeout" in response.reason.lower() or "transient" in response.reason.lower()


@pytest.mark.asyncio
async def test_hermes_fallback_complexity(hermes_bridge):
    """Test: Hermes fallback handles complexity errors."""
    error = ValueError("Task too complex for single skill")
    context = {"current_skill": "code_analysis"}

    response = await hermes_bridge.diagnose(error, context)

    assert response.primary_strategy == "decompose"
    assert response.confidence > 0


@pytest.mark.asyncio
async def test_hermes_strategy_mapping(hermes_bridge):
    """Test: Hermes response maps to Recovery strategy."""
    hermes_response = HermesResponse(
        primary_strategy="decompose_task",
        confidence=0.8,
        reason="Task too large"
    )

    strategy = hermes_bridge.map_to_recovery_strategy(hermes_response)
    assert strategy == "decompose"


@pytest.mark.asyncio
async def test_event_broadcaster_direct_listener(event_broadcaster):
    """Test: EventBroadcaster calls direct listeners (no Event Bus)."""
    called = []

    async def listener(level, message, metadata):
        called.append((level, message))

    event_broadcaster.add_listener(listener)

    await event_broadcaster.broadcast(
        StatusLevel.SUCCESS,
        "Task complete",
        task_id="test_001",
        persona_id="default"
    )

    assert len(called) == 1
    assert called[0] == ("success", "Task complete")


@pytest.mark.asyncio
async def test_status_event_serialization(event_broadcaster):
    """Test: StatusEvent serializes to JSON-safe dict."""
    event = StatusEvent(
        level=StatusLevel.INFO,
        message="Starting task",
        task_id="test_001",
        persona_id="default",
        metadata={"items": 10}
    )

    event_dict = event.to_dict()

    assert event_dict["level"] == "info"
    assert event_dict["message"] == "Starting task"
    assert event_dict["task_id"] == "test_001"
    assert event_dict["metadata"]["items"] == 10


@pytest.mark.asyncio
async def test_console_notifier(event_broadcaster):
    """Test: ConsoleNotifier handles events (no crash with None console)."""
    notifier = ConsoleNotifier(console_api=None)

    # Should not crash
    event_dict = {
        "level": "info",
        "message": "Task started",
        "task_id": "test_001"
    }
    await notifier.on_status_event(event_dict)


@pytest.mark.asyncio
async def test_discord_notifier_emoji(event_broadcaster):
    """Test: DiscordNotifier generates correct emoji."""
    # `_emoji_for_level` lives on DiscordNotifier; the test instantiated
    # ConsoleNotifier, which has no such method — so it asserted nothing about
    # the class named in its own title.
    notifier = DiscordNotifier(webhook_url=None)

    # Emoji generation
    assert notifier._emoji_for_level("info") == "ℹ️"
    assert notifier._emoji_for_level("success") == "✅"
    assert notifier._emoji_for_level("warning") == "⚠️"
    assert notifier._emoji_for_level("error") == "❌"


@pytest.mark.asyncio
async def test_hermes_error_classification(hermes_bridge):
    """Test: Hermes classifies error types."""
    test_cases = [
        (TimeoutError("timeout"), "timeout"),
        (MemoryError("memory"), "resource"),
        (ValueError("validation"), "validation"),
        (ConnectionError("connection"), "network"),
    ]

    for error, expected_type in test_cases:
        error_type = hermes_bridge._classify_error(error)
        assert error_type == expected_type


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
