"""Unit tests for task_manager.py."""
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_REPO / "core" / "console"))


import asyncio
import json
import tempfile
from pathlib import Path

import pytest

from corvin_console.task_manager import TaskManager, TaskStatus


@pytest.fixture
def tasks_dir():
    """Temporary tasks directory."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


@pytest.fixture
def tm(tasks_dir):
    """TaskManager instance with temp directory."""
    return TaskManager(tasks_dir)


def test_create_task(tm):
    """Test task creation and metadata persistence."""
    task_id = tm.create_task(
        chat_key="web:test",
        instruction="analyze this code",
        persona="code_reviewer",
    )

    assert task_id  # UUID generated
    assert tm._meta_path(task_id).exists()
    assert tm._events_path(task_id).exists()

    task = tm.get_task(task_id)
    assert task is not None
    assert task.task_id == task_id
    assert task.chat_key == "web:test"
    assert task.status == TaskStatus.PENDING
    assert task.input["instruction"] == "analyze this code"


def test_record_events(tm):
    """Test event recording and lifecycle transitions."""
    task_id = tm.create_task(chat_key="web:test", instruction="test")

    # Record started
    seq1 = tm.record_event(task_id, {"event": "task.started", "engine_id": "claude_code"})
    assert seq1 == 1  # seq 0 is task.created

    task = tm.get_task(task_id)
    assert task.status == TaskStatus.RUNNING
    assert task.started_at is not None

    # Record stream tokens
    tm.record_event(task_id, {"event": "stream_token", "chunk": "hello "})
    tm.record_event(task_id, {"event": "stream_token", "chunk": "world"})

    # Record completed
    seq_final = tm.record_event(task_id, {
        "event": "task.completed",
        "exit_code": 0,
        "summary": "completed successfully",
    })
    assert seq_final == 4

    task = tm.get_task(task_id)
    assert task.status == TaskStatus.COMPLETED
    assert task.exit_code == 0
    assert task.duration_ms is not None


def test_list_tasks(tm):
    """Test listing tasks by chat_key."""
    task_id_1 = tm.create_task(chat_key="web:chat1", instruction="task 1")
    task_id_2 = tm.create_task(chat_key="web:chat1", instruction="task 2")
    task_id_3 = tm.create_task(chat_key="web:chat2", instruction="task 3")

    tasks_chat1 = tm.list_tasks("web:chat1")
    assert len(tasks_chat1) == 2
    assert {t.task_id for t in tasks_chat1} == {task_id_1, task_id_2}

    tasks_chat2 = tm.list_tasks("web:chat2")
    assert len(tasks_chat2) == 1
    assert tasks_chat2[0].task_id == task_id_3

    # Most recent first
    assert tasks_chat1[0].created_at >= tasks_chat1[1].created_at


def test_cancel_task(tm):
    """Test task cancellation."""
    task_id = tm.create_task(chat_key="web:test", instruction="test")

    tm.record_event(task_id, {"event": "task.started"})

    ok = tm.cancel_task(task_id)
    assert ok

    task = tm.get_task(task_id)
    assert task.status == TaskStatus.CANCELLED

    # Cannot cancel completed task
    task_id_2 = tm.create_task(chat_key="web:test", instruction="test 2")
    tm.record_event(task_id_2, {"event": "task.started"})
    tm.record_event(task_id_2, {"event": "task.completed", "exit_code": 0})

    ok = tm.cancel_task(task_id_2)
    assert not ok  # Already completed


def test_reap_stale_running_finalizes_orphans(tm):
    """Boot reaper marks RUNNING/PENDING orphans as FAILED, frees the quota."""
    # Orphan 1: started but never terminated (the crash case).
    running_id = tm.create_task(chat_key="web:test", instruction="orphan")
    tm.record_event(running_id, {"event": "task.started"})
    # Orphan 2: created but never started.
    pending_id = tm.create_task(chat_key="web:test", instruction="never-started")

    # Quota sees the running orphan before the sweep.
    assert tm._count_running_tasks("web:test") == 1

    reaped = tm.reap_stale_running()
    assert set(reaped) == {running_id, pending_id}

    running = tm.get_task(running_id)
    pending = tm.get_task(pending_id)
    assert running.status == TaskStatus.FAILED
    assert pending.status == TaskStatus.FAILED
    assert running.ended_at is not None

    # Quota counter freed.
    assert tm._count_running_tasks("web:test") == 0

    # Reason is preserved in the append-only event log.
    events = [e for _, e in tm.events_since(running_id)]
    failed = [e for e in events if e["event"] == "task.failed"]
    assert failed and failed[-1]["error"] == "orphaned_on_restart"
    assert failed[-1]["reaped"] is True


def test_reap_stale_running_leaves_terminal_tasks_untouched(tm):
    """Reaper must not touch already-completed/failed/cancelled tasks."""
    done_id = tm.create_task(chat_key="web:test", instruction="done")
    tm.record_event(done_id, {"event": "task.started"})
    tm.record_event(done_id, {"event": "task.completed", "exit_code": 0})
    done_before = tm.get_task(done_id)

    reaped = tm.reap_stale_running()
    assert reaped == []

    done_after = tm.get_task(done_id)
    assert done_after.status == TaskStatus.COMPLETED
    assert done_after.ended_at == done_before.ended_at  # unchanged


def test_reap_stale_running_idempotent(tm):
    """A second sweep finds nothing (no double-finalization)."""
    rid = tm.create_task(chat_key="web:test", instruction="orphan")
    tm.record_event(rid, {"event": "task.started"})

    assert tm.reap_stale_running() == [rid]
    assert tm.reap_stale_running() == []  # already terminal
    assert tm.get_task(rid).status == TaskStatus.FAILED


def test_reap_never_reaps_a_task_whose_process_is_alive(tm, monkeypatch):
    """Regression (incident 2026-06-17): the reaper must NOT finalize a
    RUNNING task whose engine process is still alive — a long-running task
    that outlived a restart, or whose own E2E test booted adapter.main()."""
    rid = tm.create_task(chat_key="web:test", instruction="long-running")
    tm.record_event(rid, {"event": "task.started", "pid": 4242})

    # Simulate the engine process still being alive.
    monkeypatch.setattr(tm, "_task_pid_alive", lambda task_id: True)

    assert tm.reap_stale_running() == []
    assert tm.get_task(rid).status == TaskStatus.RUNNING  # untouched


def test_reap_finalizes_task_whose_process_is_dead(tm, monkeypatch):
    """A RUNNING task whose engine pid is gone IS a genuine orphan → reaped."""
    rid = tm.create_task(chat_key="web:test", instruction="orphan")
    tm.record_event(rid, {"event": "task.started", "pid": 4242})

    monkeypatch.setattr(tm, "_task_pid_alive", lambda task_id: False)

    assert tm.reap_stale_running() == [rid]
    assert tm.get_task(rid).status == TaskStatus.FAILED


def test_last_started_pid_extraction(tm):
    """_last_started_pid reads the pid from the task.started event; None if absent."""
    with_pid = tm.create_task(chat_key="web:test", instruction="a")
    tm.record_event(with_pid, {"event": "task.started", "pid": 13579})
    assert tm._last_started_pid(with_pid) == 13579

    without_pid = tm.create_task(chat_key="web:test", instruction="b")
    tm.record_event(without_pid, {"event": "task.started"})
    assert tm._last_started_pid(without_pid) is None


def test_pid_alive_false_when_process_gone(tm, monkeypatch):
    """_task_pid_alive returns False when os.kill reports the pid is gone."""
    import os as _os
    rid = tm.create_task(chat_key="web:test", instruction="dead")
    tm.record_event(rid, {"event": "task.started", "pid": 4242})

    def _gone(pid, sig):
        raise ProcessLookupError

    monkeypatch.setattr(_os, "kill", _gone)
    assert tm._task_pid_alive(rid) is False

    # No recorded pid → treated as not-alive so true orphans are still reaped.
    nopid = tm.create_task(chat_key="web:test", instruction="nopid")
    tm.record_event(nopid, {"event": "task.started"})
    assert tm._task_pid_alive(nopid) is False


def test_events_since():
    """Test events_since iterator (async simulation)."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tm = TaskManager(Path(tmpdir))
        task_id = tm.create_task(chat_key="web:test", instruction="test")

        tm.record_event(task_id, {"event": "task.started"})
        tm.record_event(task_id, {"event": "stream_token", "chunk": "a"})
        tm.record_event(task_id, {"event": "stream_token", "chunk": "b"})
        tm.record_event(task_id, {"event": "task.completed", "exit_code": 0})

        # Resume semantics: start_seq is the LAST SEEN seq (exclusive) —
        # start_seq=0 skips task.created (seq 0) and replays the rest.
        events = []
        for seq, event in tm.events_since(task_id, start_seq=0):
            events.append((seq, event["event"]))

        assert len(events) == 4  # everything after task.created
        assert events[0][1] == "task.started"
        assert events[1][1] == "stream_token"
        assert events[2][1] == "stream_token"
        assert events[3][1] == "task.completed"

        # Full replay from the beginning includes task.created.
        all_events = [e["event"] for _, e in tm.events_since(task_id, start_seq=None)]
        assert all_events[0] == "task.created"
        assert len(all_events) == 5

        # Skip to seq 2
        events_since_2 = list(tm.events_since(task_id, start_seq=1))
        assert len(events_since_2) == 3  # skip seq 0 and 1
        assert events_since_2[0][0] == 2  # First event is seq 2


def test_event_log_format(tm, tasks_dir):
    """Test that event log is valid JSONL."""
    task_id = tm.create_task(chat_key="web:test", instruction="test")
    tm.record_event(task_id, {"event": "task.started"})
    tm.record_event(task_id, {"event": "stream_token", "chunk": "hi"})

    events_path = tm._events_path(task_id)
    assert events_path.exists()

    # Verify it's valid JSONL (each line is valid JSON)
    with open(events_path) as f:
        for i, line in enumerate(f):
            try:
                event = json.loads(line)
                assert "seq" in event
                assert "timestamp" in event
                assert "event" in event
            except json.JSONDecodeError:
                pytest.fail(f"Line {i} is not valid JSON: {line}")
