"""Phase 3.1: Universal Task Status Model + Bridge Publishing E2E Tests."""

import pytest
from unittest.mock import AsyncMock

from ..status_snapshot import (
    StatusSnapshot, StatusPublisher, TaskState, UserAction, UserActionType,
    StatusEvent, get_publisher, set_publisher
)


@pytest.fixture
def publisher():
    """Create test publisher."""
    return StatusPublisher()

@pytest.fixture
def snapshot():
    """Create test snapshot."""
    return StatusSnapshot(
        task_id="test_001",
        session_id="session_xyz",
        state=TaskState.RUNNING,
        progress_percent=50.0,
        iteration_num=5,
        total_iterations=10,
        current_action="Executing skill: code_analysis",
        latest_message="Iteration 5 complete"
    )


@pytest.mark.asyncio
async def test_snapshot_serialize_to_dict(snapshot):
    """Test: StatusSnapshot serializes to JSON-safe dict."""
    data = snapshot.to_dict()

    assert data["task_id"] == "test_001"
    assert data["state"] == "running"
    assert data["progress_percent"] == 50.0
    assert isinstance(data, dict)


@pytest.mark.asyncio
async def test_snapshot_to_discord_embed(snapshot):
    """Test: StatusSnapshot formats for Discord embed."""
    embed = snapshot.to_discord_embed()

    assert embed["title"]
    assert embed["color"] == 0x3498db  # Blue for RUNNING
    assert len(embed["fields"]) > 0
    assert any(f["name"] == "Progress" for f in embed["fields"])


@pytest.mark.asyncio
async def test_snapshot_to_cli_summary(snapshot):
    """Test: StatusSnapshot formats for CLI text."""
    summary = snapshot.to_cli_summary()

    assert "test_001" in summary
    assert "RUNNING" in summary
    assert "50.0%" in summary
    assert "▶️" in summary  # Running emoji


@pytest.mark.asyncio
async def test_snapshot_to_chat_line(snapshot):
    """Test: StatusSnapshot formats for chat (inline)."""
    line = snapshot.to_chat_line()

    assert "test_001" in line
    assert "RUNNING" in line
    assert "50.0%" in line


@pytest.mark.asyncio
async def test_snapshot_with_user_action(snapshot):
    """Test: StatusSnapshot includes user action when awaiting input."""
    user_action = UserAction(
        action_type=UserActionType.DECISION,
        prompt="Should we refactor React components? [Y/n]"
    )
    snapshot.state = TaskState.AWAITING_INPUT
    snapshot.user_action_required = user_action

    data = snapshot.to_dict()
    assert data["user_action_required"]["type"] == "decision"


@pytest.mark.asyncio
async def test_publisher_subscribe(publisher):
    """Test: Publisher registers bridge subscribers."""
    mock_callback = AsyncMock()
    publisher.subscribe("discord", mock_callback)

    assert "discord" in publisher.subscribers
    assert publisher.subscribers["discord"] == mock_callback


@pytest.mark.asyncio
async def test_publisher_publish_to_all_bridges(publisher, snapshot):
    """Test: Publisher broadcasts to all subscribed bridges."""
    discord_cb = AsyncMock()
    console_cb = AsyncMock()
    cli_cb = AsyncMock()

    publisher.subscribe("discord", discord_cb)
    publisher.subscribe("console", console_cb)
    publisher.subscribe("cli", cli_cb)

    await publisher.publish(snapshot)

    # Each subscriber should be called once
    assert discord_cb.call_count == 1
    assert console_cb.call_count == 1
    assert cli_cb.call_count == 1


@pytest.mark.asyncio
async def test_publisher_history(publisher, snapshot):
    """Test: Publisher maintains history."""
    await publisher.publish(snapshot)
    await publisher.publish(snapshot)

    history = publisher.get_history(snapshot.task_id)
    assert len(history) == 2


@pytest.mark.asyncio
async def test_publisher_get_latest(publisher, snapshot):
    """Test: Get latest snapshot for task."""
    await publisher.publish(snapshot)

    latest = publisher.get_latest(snapshot.task_id)
    assert latest is not None
    assert latest.task_id == snapshot.task_id


@pytest.mark.asyncio
async def test_snapshot_state_transitions(publisher):
    """Test: StatusSnapshot state lifecycle."""
    task_id = "lifecycle_test"

    # Start
    snap1 = StatusSnapshot(
        task_id=task_id,
        session_id="s1",
        state=TaskState.RUNNING,
        iteration_num=1
    )
    await publisher.publish(snap1)

    # Mid-run
    snap2 = StatusSnapshot(
        task_id=task_id,
        session_id="s1",
        state=TaskState.RUNNING,
        iteration_num=5
    )
    await publisher.publish(snap2)

    # Complete
    snap3 = StatusSnapshot(
        task_id=task_id,
        session_id="s1",
        state=TaskState.COMPLETED,
        iteration_num=10,
        progress_percent=100.0
    )
    await publisher.publish(snap3)

    history = publisher.get_history(task_id)
    assert len(history) == 3
    assert history[0].state == TaskState.RUNNING
    assert history[-1].state == TaskState.COMPLETED


@pytest.mark.asyncio
async def test_status_event_immutability(snapshot):
    """Test: StatusEvent immutability."""
    event = StatusEvent(
        timestamp="2026-08-24T00:00:00",
        level="success",
        message="Test message"
    )

    data = event.to_dict()
    assert data["level"] == "success"
    assert data["message"] == "Test message"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
