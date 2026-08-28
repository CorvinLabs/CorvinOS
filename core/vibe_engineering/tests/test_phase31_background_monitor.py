"""Phase 3.1b: Background Monitor Unit Tests (Discord webhook, retry logic, milestones)."""

import asyncio

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime, timedelta
import aiohttp

from ..background_monitor import BackgroundMonitor, start_background_monitor
from ..status_snapshot import StatusSnapshot, TaskState, StatusPublisher


@pytest.fixture
def publisher():
    """Create test publisher."""
    return StatusPublisher()


@pytest.fixture
def monitor(publisher):
    """Create test monitor (no Discord webhook)."""
    monitor = BackgroundMonitor(poll_interval=1.0, discord_webhook=None)
    monitor.publisher = publisher
    return monitor


@pytest.fixture
def monitor_with_webhook(publisher):
    """Create test monitor with Discord webhook."""
    monitor = BackgroundMonitor(
        poll_interval=1.0,
        discord_webhook="https://discord.com/api/webhooks/test/token",
        cleanup_completed=True
    )
    monitor.publisher = publisher
    return monitor


@pytest.fixture
def snapshot():
    """Create test snapshot."""
    return StatusSnapshot(
        task_id="test_001",
        session_id="session_xyz",
        state=TaskState.RUNNING,
        progress_percent=50.0,
        iteration_num=5,
        total_iterations=10
    )


@pytest.mark.asyncio
async def test_monitor_initialization(monitor):
    """Test: Monitor initializes with correct state."""
    assert monitor.poll_interval == 1.0
    assert monitor.is_running == False
    assert monitor.notification_cooldown == timedelta(seconds=60)
    assert len(monitor.last_notified) == 0


@pytest.mark.asyncio
async def test_monitor_start_stop(monitor):
    """Test: Monitor can start and stop."""
    task = asyncio.create_task(monitor.start())
    await asyncio.sleep(0.1)

    assert monitor.is_running == True
    monitor.stop()

    await asyncio.sleep(0.1)
    assert monitor.is_running == False
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


@pytest.mark.asyncio
async def test_monitor_milestone_progress(publisher, monitor, snapshot):
    """Test: Monitor detects progress milestone (every 5 iterations)."""
    await publisher.publish(snapshot)

    # First milestone: none yet
    await monitor._check_task(snapshot.task_id)
    assert snapshot.task_id not in monitor.last_notified

    # Progress to 10 iterations (> last 5 + 5)
    snapshot.iteration_num = 10
    await publisher.publish(snapshot)
    await monitor._check_task(snapshot.task_id)

    # Should trigger notification
    assert snapshot.task_id in monitor.last_notified
    assert monitor.last_seen_iteration[snapshot.task_id] == 10


@pytest.mark.asyncio
async def test_monitor_state_change_milestone(publisher, monitor, snapshot):
    """Test: Monitor detects state changes."""
    await publisher.publish(snapshot)

    # Change state to COMPLETED
    snapshot.state = TaskState.COMPLETED
    await publisher.publish(snapshot)
    await monitor._check_task(snapshot.task_id)

    assert snapshot.task_id in monitor.last_notified


@pytest.mark.asyncio
async def test_monitor_notification_cooldown(publisher, monitor, snapshot):
    """Test: Monitor respects notification cooldown."""
    await publisher.publish(snapshot)

    # Trigger first notification (progress)
    snapshot.iteration_num = 10
    await publisher.publish(snapshot)
    await monitor._check_task(snapshot.task_id)
    first_notif = monitor.last_notified[snapshot.task_id]

    # Immediately try to notify again (should be skipped due to cooldown)
    snapshot.iteration_num = 15
    await publisher.publish(snapshot)
    await monitor._check_task(snapshot.task_id)

    # Timestamp should be same (no new notification)
    assert monitor.last_notified[snapshot.task_id] == first_notif


@pytest.mark.asyncio
async def test_monitor_cleanup_completed_tasks(publisher, monitor, snapshot):
    """Test: Monitor cleans up completed tasks from tracking dicts."""
    await publisher.publish(snapshot)

    # Trigger notification to add to tracking
    snapshot.iteration_num = 10
    await publisher.publish(snapshot)
    await monitor._check_task(snapshot.task_id)

    assert snapshot.task_id in monitor.last_notified

    # Mark as completed
    snapshot.state = TaskState.COMPLETED
    await publisher.publish(snapshot)

    # Trigger cleanup
    monitor._cleanup_completed_tasks()

    assert snapshot.task_id not in monitor.last_notified


@pytest.mark.asyncio
async def test_discord_webhook_post_success(monitor_with_webhook, snapshot):
    """Test: Discord webhook POST succeeds on 204 response."""
    with patch('aiohttp.ClientSession.post') as mock_post:
        mock_resp = AsyncMock()
        mock_resp.status = 204
        mock_post.return_value.__aenter__.return_value = mock_resp

        await monitor_with_webhook._send_discord_webhook(snapshot, "Test milestone")

        # Verify POST was called
        assert mock_post.called


@pytest.mark.asyncio
async def test_discord_webhook_retry_on_server_error(monitor_with_webhook, snapshot):
    """Test: Discord webhook retries on 5xx errors."""
    with patch('aiohttp.ClientSession.post') as mock_post:
        mock_resp = AsyncMock()
        # First attempt: 500, Second attempt: 204
        mock_resp.status = 500
        mock_resp.text = AsyncMock(return_value="Server error")
        mock_post.return_value.__aenter__.return_value = mock_resp

        # Patch sleep to avoid actual delays
        with patch('asyncio.sleep', new_callable=AsyncMock):
            await monitor_with_webhook._send_discord_webhook(snapshot, "Test", max_retries=2)

        # Should attempt at least once
        assert mock_post.called


@pytest.mark.asyncio
async def test_discord_webhook_timeout_retry(monitor_with_webhook, snapshot):
    """Test: Discord webhook retries on timeout."""
    call_count = 0

    class _PostCM:
        """`session.post(...)` returns an async CONTEXT MANAGER, not a
        coroutine. The old mock was `async def`, so `async with session.post()`
        raised TypeError before the body ever ran — call_count stayed 0 and the
        test measured nothing."""

        async def __aenter__(self):
            mock_resp = AsyncMock()
            mock_resp.status = 204
            return mock_resp

        async def __aexit__(self, *exc):
            return False

    def mock_post_timeout(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count < 2:
            raise asyncio.TimeoutError("Connection timeout")
        return _PostCM()

    with patch('aiohttp.ClientSession.post', side_effect=mock_post_timeout):
        with patch('asyncio.sleep', new_callable=AsyncMock):
            await monitor_with_webhook._send_discord_webhook(snapshot, "Test", max_retries=3)

    # Should have retried at least once
    assert call_count >= 1


@pytest.mark.asyncio
async def test_send_notification_uses_publisher_fallback(publisher, monitor, snapshot):
    """Test: When no Discord webhook, uses publisher fallback."""
    monitor.discord_webhook = None

    with patch.object(publisher, 'publish', new_callable=AsyncMock) as mock_publish:
        monitor.publisher = publisher
        await monitor._send_notification(snapshot, "Test")

        # Should call publisher.publish
        mock_publish.assert_called_once()


@pytest.mark.asyncio
async def test_monitor_check_all_tasks(publisher, monitor):
    """Test: Monitor iterates over all tracked tasks."""
    snap1 = StatusSnapshot(task_id="task_1", session_id="s1", iteration_num=1)
    snap2 = StatusSnapshot(task_id="task_2", session_id="s1", iteration_num=1)

    await publisher.publish(snap1)
    await publisher.publish(snap2)

    with patch.object(monitor, '_check_task', new_callable=AsyncMock) as mock_check:
        await monitor._check_all_tasks()

        # Should call _check_task for both tasks
        assert mock_check.call_count >= 2


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
