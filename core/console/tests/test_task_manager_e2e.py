"""E2E tests for ADR-0080 M1 task lifecycle.

Tests:
  1. Create task via TaskManager
  2. Record events
  3. Replay events via events_since() (SSE simulation)
  4. Verify Last-Event-ID resume works
"""
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_REPO / "core" / "console"))


import asyncio
import json
import tempfile
from pathlib import Path

from corvin_console.task_manager import TaskManager, TaskStatus


def test_e2e_task_lifecycle():
    """E2E: Create task → record events → query with Last-Event-ID."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tasks_dir = Path(tmpdir)
        tm = TaskManager(tasks_dir)

        # 1. Create task (mimics stream_turn start)
        task_id = tm.create_task(
            chat_key="web:test-sid",
            instruction="analyze this code",
            persona="assistant",
            turn_number=0,
        )
        print(f"  ✓ Created task: {task_id[:8]}...")

        # 2. Simulate task.started event
        tm.record_event(task_id, {
            "event": "task.started",
            "engine": "claude",
            "turn": 0,
        })
        print(f"  ✓ Recorded task.started")

        # 3. Simulate stream tokens (would happen mid-turn)
        tm.record_event(task_id, {
            "event": "stream_token",
            "chunk": "Here is ",
        })
        tm.record_event(task_id, {
            "event": "stream_token",
            "chunk": "an analysis...",
        })
        print(f"  ✓ Recorded 2 stream_token events")

        # 4. Simulate task completion
        tm.record_event(task_id, {
            "event": "task.completed",
            "exit_code": 0,
            "summary": "50 chars output",
        })
        print(f"  ✓ Recorded task.completed")

        # 5. Verify task status
        task = tm.get_task(task_id)
        assert task.status == TaskStatus.COMPLETED
        assert task.exit_code == 0
        assert task.started_at is not None
        assert task.ended_at is not None
        print(f"  ✓ Task status correct: {task.status}")

        # 6. Simulate SSE client: fetch all events
        events = list(tm.events_since(task_id, start_seq=None))
        assert len(events) == 5  # created + started + 2 tokens + completed
        assert events[0][0] == 0  # seq 0
        assert events[0][1]["event"] == "task.created"
        assert events[1][1]["event"] == "task.started"
        assert events[2][1]["event"] == "stream_token"
        assert events[3][1]["event"] == "stream_token"
        assert events[4][1]["event"] == "task.completed"
        print(f"  ✓ Fetched all {len(events)} events in order")

        # 7. Simulate tab disconnect + reconnect (Last-Event-ID=1)
        events_since_1 = list(tm.events_since(task_id, start_seq=1))
        assert len(events_since_1) == 3  # skip 0 and 1, get 2,3,4
        assert events_since_1[0][0] == 2  # First event is seq 2
        assert events_since_1[0][1]["event"] == "stream_token"
        assert events_since_1[2][0] == 4  # Last event is seq 4
        assert events_since_1[2][1]["event"] == "task.completed"
        print(f"  ✓ Reconnect with Last-Event-ID=1 returned {len(events_since_1)} new events")

        # 8. Verify task.events list includes all output events
        assert len(task.output_events) == 5
        print(f"  ✓ Task.output_events has all {len(task.output_events)} events")


def test_e2e_multiple_tasks_per_session():
    """E2E: Multiple tasks in one chat session."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tasks_dir = Path(tmpdir)
        tm = TaskManager(tasks_dir)

        # Create 3 tasks for the same session
        task_ids = []
        for i in range(3):
            tid = tm.create_task(
                chat_key="web:sid-1",
                instruction=f"task {i}",
                turn_number=i,
            )
            tm.record_event(tid, {"event": "task.started"})
            tm.record_event(tid, {"event": "task.completed", "exit_code": 0})
            task_ids.append(tid)
            print(f"  ✓ Created and completed task {i}: {tid[:8]}...")

        # List tasks for the session
        tasks = tm.list_tasks("web:sid-1")
        assert len(tasks) == 3
        # Newest first
        assert tasks[0].task_id == task_ids[2]
        assert tasks[1].task_id == task_ids[1]
        assert tasks[2].task_id == task_ids[0]
        print(f"  ✓ Listed {len(tasks)} tasks in reverse chronological order")


def test_e2e_cancel_task():
    """E2E: Cancel a running task."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tasks_dir = Path(tmpdir)
        tm = TaskManager(tasks_dir)

        task_id = tm.create_task(chat_key="web:test", instruction="long-running")
        tm.record_event(task_id, {"event": "task.started"})
        tm.record_event(task_id, {"event": "stream_token", "chunk": "halfway..."})

        # Simulate user cancel
        ok = tm.cancel_task(task_id)
        assert ok
        print(f"  ✓ Cancelled task {task_id[:8]}...")

        task = tm.get_task(task_id)
        assert task.status == TaskStatus.CANCELLED
        assert len(task.output_events) == 4  # created + started + token + cancelled
        print(f"  ✓ Task status is CANCELLED")


def test_event_log_persistence():
    """E2E: Event log survives reopen of TaskManager."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tasks_dir = Path(tmpdir)

        # Create task with first TM instance
        tm1 = TaskManager(tasks_dir)
        task_id = tm1.create_task(chat_key="web:test", instruction="test")
        tm1.record_event(task_id, {"event": "task.started"})
        tm1.record_event(task_id, {"event": "task.completed", "exit_code": 0})
        events_1 = list(tm1.events_since(task_id, start_seq=None))
        print(f"  ✓ Created and recorded {len(events_1)} events")

        # Reopen with second TM instance (simulates process restart)
        tm2 = TaskManager(tasks_dir)
        task = tm2.get_task(task_id)
        assert task is not None
        assert task.status == TaskStatus.COMPLETED
        events_2 = list(tm2.events_since(task_id, start_seq=None))
        assert len(events_2) == len(events_1)
        assert events_2[0][1]["event"] == "task.created"
        assert events_2[-1][1]["event"] == "task.completed"
        print(f"  ✓ Reopened TaskManager: read same {len(events_2)} events")


if __name__ == "__main__":
    print("\n=== E2E Task Lifecycle Test ===")
    test_e2e_task_lifecycle()

    print("\n=== E2E Multiple Tasks Per Session ===")
    test_e2e_multiple_tasks_per_session()

    print("\n=== E2E Cancel Task ===")
    test_e2e_cancel_task()

    print("\n=== E2E Event Log Persistence ===")
    test_event_log_persistence()

    print("\n✅ All E2E tests passed!")
