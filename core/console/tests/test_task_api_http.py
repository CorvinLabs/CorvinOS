"""HTTP API integration tests for task endpoints (M1 MVP).

Tests:
  1. POST /sessions → create session
  2. GET /sessions/{sid}/tasks → list tasks (empty initially)
  3. Simulate task creation via TaskManager
  4. GET /sessions/{sid}/tasks → list tasks
  5. GET /sessions/{sid}/tasks/{task_id} → get task
  6. GET /sessions/{sid}/tasks/{task_id}/events → SSE stream
"""
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_REPO / "core" / "console"))


import json
import tempfile
from pathlib import Path

from corvin_console.task_manager import TaskManager, TaskStatus


def test_http_task_api_mock():
    """Mock HTTP behavior (integration test without FastAPI server)."""
    with tempfile.TemporaryDirectory() as tmpdir:
        # Simulate session workdir
        session_workdir = Path(tmpdir) / "session_1"
        session_workdir.mkdir(parents=True, exist_ok=True)
        tasks_dir = session_workdir / "tasks"

        tm = TaskManager(tasks_dir)
        chat_key = "web:session_1"

        # GET /sessions/{sid}/tasks — empty initially
        tasks_empty = tm.list_tasks(chat_key)
        assert len(tasks_empty) == 0
        print("  ✓ GET /tasks: empty list")

        # Create a task (simulates POST /messages)
        task_id = tm.create_task(
            chat_key=chat_key,
            instruction="test instruction",
            persona="assistant",
        )
        print(f"  ✓ Task created: {task_id[:8]}...")

        # Record some events
        tm.record_event(task_id, {"event": "task.started", "engine": "claude"})
        tm.record_event(task_id, {"event": "stream_token", "chunk": "hello"})
        tm.record_event(task_id, {"event": "task.completed", "exit_code": 0})
        print("  ✓ Recorded task.started, stream_token, task.completed")

        # GET /sessions/{sid}/tasks — should list 1 task
        tasks = tm.list_tasks(chat_key)
        assert len(tasks) == 1
        task = tasks[0]
        assert task.task_id == task_id
        assert task.status == TaskStatus.COMPLETED
        print(f"  ✓ GET /tasks: returned 1 task with status {task.status.value}")

        # GET /sessions/{sid}/tasks/{task_id} — get metadata
        task_detail = tm.get_task(task_id)
        assert task_detail is not None
        assert task_detail.input["instruction"] == "test instruction"
        assert task_detail.exit_code == 0
        print("  ✓ GET /tasks/{task_id}: returned task metadata")

        # GET /sessions/{sid}/tasks/{task_id}/events (no Last-Event-ID = all events)
        events = list(tm.events_since(task_id, start_seq=None))
        assert len(events) == 4  # created + started + token + completed

        # Verify event sequence format (what SSE endpoint would return)
        sse_lines = []
        for seq, event in events:
            sse_lines.append(f"id: {seq}")
            sse_lines.append(f"data: {json.dumps(event)}")
            sse_lines.append("")
        assert len(sse_lines) > 0
        print(f"  ✓ SSE events: {len(events)} events, {len(sse_lines)} SSE lines")

        # Simulate Last-Event-ID=1 (reconnect after seq 1)
        events_since_1 = list(tm.events_since(task_id, start_seq=1))
        assert len(events_since_1) == 2  # skip 0,1 get 2,3
        assert events_since_1[0][0] == 2  # First seq is 2
        assert events_since_1[1][0] == 3  # Last seq is 3
        print(f"  ✓ SSE reconnect with Last-Event-ID=1: returned {len(events_since_1)} new events")

        # DELETE /sessions/{sid}/tasks/{task_id} — create cancellable task
        task_id_cancel = tm.create_task(chat_key=chat_key, instruction="task to cancel")
        tm.record_event(task_id_cancel, {"event": "task.started"})
        ok = tm.cancel_task(task_id_cancel)
        assert ok
        task_cancel = tm.get_task(task_id_cancel)
        assert task_cancel.status == TaskStatus.CANCELLED
        print(f"  ✓ DELETE /tasks/{{task_id}}: cancel works")


if __name__ == "__main__":
    print("\n=== HTTP Task API Mock Test ===")
    test_http_task_api_mock()
    print("\n✅ HTTP mock test passed!")
