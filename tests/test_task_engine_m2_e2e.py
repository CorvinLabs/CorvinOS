"""E2E tests for Task Engine M2 (ADR-0081).

Tests: task creation → queue → dequeue → pubsub broadcast.
"""
import asyncio
import json
import tempfile
from pathlib import Path

import pytest

from corvin_console.task_queue import TaskQueue, TaskStatus
from corvin_console.task_pubsub import TaskPubSub


@pytest.fixture
def tmp_tenant_dir():
    with tempfile.TemporaryDirectory() as td:
        tenant_dir = Path(td) / "tenants" / "_default" / "global"
        tenant_dir.mkdir(parents=True)
        yield tenant_dir


@pytest.fixture
def task_queue(tmp_tenant_dir):
    return TaskQueue(tmp_tenant_dir)


@pytest.fixture
def pubsub():
    return TaskPubSub()


@pytest.mark.asyncio
async def test_task_creation_to_dequeue(task_queue):
    """Test: Create task → Dequeue → Verify status changes."""
    # Create task
    task_id = task_queue.enqueue(
        tenant_id="_default",
        chat_key="web:session_1",
        instruction="Hello, task!",
        ttl_seconds=3600,
    )
    assert task_id

    # Verify pending in queue
    task = task_queue.get_task(task_id)
    assert task.status == TaskStatus.PENDING

    # Dequeue (marks as RUNNING)
    task = task_queue.dequeue("_default")
    assert task is not None
    assert task.status == TaskStatus.RUNNING

    # Queue should now be empty
    task2 = task_queue.dequeue("_default")
    assert task2 is None


@pytest.mark.asyncio
async def test_pubsub_broadcast(pubsub):
    """Test: Publish event → Subscribe → Receive."""
    events_received = []

    # Start subscriber task
    async def subscriber():
        async for event in pubsub.subscribe("_default"):
            events_received.append(event)
            if len(events_received) >= 3:
                break

    sub_task = asyncio.create_task(subscriber())

    # Give subscriber time to start
    await asyncio.sleep(0.1)

    # Publish events
    await pubsub.publish("_default", "task-1", {"event": "progress", "pct": 10})
    await pubsub.publish("_default", "task-1", {"event": "progress", "pct": 50})
    await pubsub.publish("_default", "task-1", {"event": "completed", "exit_code": 0})

    # Wait for subscriber
    await asyncio.wait_for(sub_task, timeout=5)

    # Verify all events received
    assert len(events_received) == 3
    assert events_received[0]["task_id"] == "task-1"
    assert events_received[0]["event"] == "progress"
    assert events_received[2]["event"] == "completed"


@pytest.mark.asyncio
async def test_multi_subscriber_isolation(pubsub):
    """Test: Events only go to subscribed tenant."""
    acme_events = []
    beta_events = []

    async def acme_sub():
        async for event in pubsub.subscribe("acme"):
            acme_events.append(event)
            if len(acme_events) >= 2:
                break

    async def beta_sub():
        async for event in pubsub.subscribe("beta"):
            beta_events.append(event)
            if len(beta_events) >= 1:
                break

    acme_task = asyncio.create_task(acme_sub())
    beta_task = asyncio.create_task(beta_sub())

    await asyncio.sleep(0.1)

    # Publish to acme
    await pubsub.publish("acme", "task-1", {"event": "progress"})
    await pubsub.publish("acme", "task-1", {"event": "completed"})

    # Publish to beta
    await pubsub.publish("beta", "task-2", {"event": "completed"})

    await asyncio.wait_for(asyncio.gather(acme_task, beta_task), timeout=5)

    # Verify isolation
    assert len(acme_events) == 2
    assert len(beta_events) == 1
    assert acme_events[0]["task_id"] == "task-1"
    assert beta_events[0]["task_id"] == "task-2"


@pytest.mark.asyncio
async def test_task_lifecycle_with_pubsub(task_queue, pubsub):
    """Test: Create → Queue → Dequeue → Status changes broadcast."""
    received = []

    async def monitor():
        async for event in pubsub.subscribe("_default"):
            received.append(event)
            if len(received) >= 2:
                break

    monitor_task = asyncio.create_task(monitor())
    await asyncio.sleep(0.05)

    # Create task
    task_id = task_queue.enqueue("_default", "web:s1", "test", 3600)

    # Dequeue (marks RUNNING, should broadcast)
    task = task_queue.dequeue("_default")
    await pubsub.publish("_default", task_id, {"event": "task.started"})

    # Mark completed
    task_queue.update_status(task_id, TaskStatus.COMPLETED, exit_code=0)
    await pubsub.publish("_default", task_id, {"event": "task.completed", "exit_code": 0})

    await asyncio.wait_for(monitor_task, timeout=5)

    # Verify events received
    assert len(received) >= 2
    assert any(e["event"] == "task.started" for e in received)
    assert any(e["event"] == "task.completed" for e in received)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
