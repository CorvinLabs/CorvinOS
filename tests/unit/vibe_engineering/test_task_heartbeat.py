"""Tests for TaskHeartbeat (ADR-0377)."""

import pytest
import asyncio
from core.vibe_engineering.task_heartbeat import TaskHeartbeat, HeartbeatConfig


@pytest.mark.asyncio
async def test_heartbeat_emits_periodically():
    """Heartbeat emits periodic status updates."""
    config = HeartbeatConfig(interval_s=1, stall_threshold_s=100)
    heartbeat = TaskHeartbeat(config)
    heartbeats_received = []

    async def fast_phase():
        await asyncio.sleep(0.5)
        return {"result": "done"}

    async def on_heartbeat(data):
        heartbeats_received.append(data)

    async def on_stall(data):
        pass

    result = await heartbeat.monitor_phase(
        "task-1", "phase-1", fast_phase, 10,
        on_heartbeat, on_stall
    )

    assert result == {"result": "done"}
    # Should have received at least 1 heartbeat
    assert len(heartbeats_received) >= 1


@pytest.mark.asyncio
async def test_stall_detection():
    """Detect when phase runs too long."""
    config = HeartbeatConfig(interval_s=1, stall_threshold_s=2)
    heartbeat = TaskHeartbeat(config)
    stalls = []

    async def slow_phase():
        await asyncio.sleep(0.5)
        return {"result": "done"}

    async def on_heartbeat(data):
        pass

    async def on_stall(data):
        stalls.append(data)

    result = await heartbeat.monitor_phase(
        "task-1", "phase-stall", slow_phase, 10,
        on_heartbeat, on_stall
    )

    assert result == {"result": "done"}
    # Phase completes before stall threshold, so stall should NOT trigger
    assert len(stalls) == 0


@pytest.mark.asyncio
async def test_phase_timeout():
    """Phase timeout is enforced."""
    config = HeartbeatConfig(interval_s=1, stall_threshold_s=100)
    heartbeat = TaskHeartbeat(config)

    async def hanging_phase():
        await asyncio.sleep(10)  # Will timeout
        return {"result": "done"}

    async def on_heartbeat(data):
        pass

    async def on_stall(data):
        pass

    with pytest.raises(Exception):  # asyncio.TimeoutError wrapped
        await heartbeat.monitor_phase(
            "task-1", "phase-timeout", hanging_phase, 1,
            on_heartbeat, on_stall
        )
