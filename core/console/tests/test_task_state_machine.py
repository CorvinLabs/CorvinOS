"""Transition guard, read mapping and the new INTERRUPTED state (PLAN-0958 P1).

The guard lives in ``TaskManager.record_event``: a forbidden transition raises
``InvalidTransitionError`` BEFORE any event is appended. Stored events are
append-only; a pre-P1 orphan ``task.failed`` is READ as interrupted and is
never rewritten.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_CONSOLE = Path(__file__).resolve().parents[1]
if str(_CONSOLE) not in sys.path:
    sys.path.insert(0, str(_CONSOLE))

from corvin_core.task_states import (  # noqa: E402
    ALLOWED_TRANSITIONS,
    TERMINAL_STATES,
    InvalidTransitionError,
    TaskState,
    check_transition,
)
from corvin_core.task_manager import TaskManager, TaskStatus  # noqa: E402
from corvin_console.task_queue import TaskQueue  # noqa: E402
from corvin_console.task_queue import TaskStatus as QueueTaskStatus  # noqa: E402


@pytest.fixture
def tm(tmp_path):
    return TaskManager(tmp_path / "tasks")


def _task(tm: TaskManager, *events: dict) -> str:
    tid = tm.create_task(chat_key="web:t", instruction="x")
    for ev in events:
        tm.record_event(tid, ev)
    return tid


def test_task_status_is_the_canonical_enum():
    assert TaskStatus is TaskState
    assert QueueTaskStatus is TaskState
    # BGT-14 (2026-10-21): waiting_retry / resuming / needs_user / abandoned had no writer —
    # no event in EVENT_TARGET_STATE, no queue or manager call site — so they were removed.
    assert {s.value for s in TaskState} == {
        "pending", "running", "completed", "failed", "cancelled", "interrupted",
    }


def test_every_non_initial_state_has_a_writer():
    """A state nobody can write is a dead end in the table and a lie in the docs."""
    from corvin_core.task_states import EVENT_TARGET_STATE
    writable = set(EVENT_TARGET_STATE.values()) | {TaskState.PENDING}
    assert writable == set(TaskState)


def test_every_state_has_a_transition_row_and_terminal_states_have_no_exit():
    assert set(ALLOWED_TRANSITIONS) == set(TaskState)
    for state in TERMINAL_STATES:
        assert ALLOWED_TRANSITIONS[state] == frozenset(), state


def test_failed_cannot_run_again():
    with pytest.raises(InvalidTransitionError):
        check_transition(TaskState.FAILED, TaskState.RUNNING)


def test_interrupted_cannot_run_again_only_cancel_leaves_it():
    with pytest.raises(InvalidTransitionError):
        check_transition(TaskState.INTERRUPTED, TaskState.RUNNING)
    assert ALLOWED_TRANSITIONS[TaskState.INTERRUPTED] == frozenset({TaskState.CANCELLED})


def test_same_state_write_is_an_idempotent_no_op():
    check_transition(TaskState.CANCELLED, TaskState.CANCELLED)


def test_cancelled_to_anything_is_refused_and_writes_nothing(tm):
    tid = _task(tm, {"event": "task.started"}, {"event": "task.cancelled"})
    before = len(list(tm.events_since(tid)))
    with pytest.raises(InvalidTransitionError) as exc:
        tm.record_event(tid, {"event": "task.completed", "exit_code": 0})
    assert "cancelled -> completed" in str(exc.value)
    assert len(list(tm.events_since(tid))) == before  # refusal left the log untouched
    assert tm.get_task(tid).status == TaskStatus.CANCELLED


def test_completed_to_failed_is_refused(tm):
    tid = _task(tm, {"event": "task.started"}, {"event": "task.completed", "exit_code": 0})
    with pytest.raises(InvalidTransitionError):
        tm.record_event(tid, {"event": "task.failed", "exit_code": 1})
    assert tm.get_task(tid).status == TaskStatus.COMPLETED


def test_interrupted_task_cannot_be_restarted_by_task_started(tm):
    tid = _task(tm, {"event": "task.started"}, {"event": "task.interrupted", "error": "x"})
    with pytest.raises(InvalidTransitionError):
        tm.record_event(tid, {"event": "task.started"})
    assert tm.get_task(tid).status == TaskStatus.INTERRUPTED


def test_interrupted_is_not_terminal_and_can_still_be_cancelled(tm):
    tid = _task(tm, {"event": "task.started"}, {"event": "task.interrupted", "error": "x"})
    assert tm.get_task(tid).ended_at is None
    assert tm.cancel_task(tid) is True
    assert tm.get_task(tid).status == TaskStatus.CANCELLED


def test_legacy_orphan_failure_is_read_as_interrupted_and_not_rewritten(tm):
    tid = _task(tm, {"event": "task.started"})
    # Simulate a pre-P1 log: the old reaper wrote task.failed/orphaned_on_restart.
    tm._write_event(tid, {"event": "task.failed", "exit_code": -1,
                          "error": "orphaned_on_restart", "reaped": True})
    meta = json.loads(tm._meta_path(tid).read_text(encoding="utf-8"))
    meta["status"] = "failed"
    tm._meta_path(tid).write_text(json.dumps(meta), encoding="utf-8")

    assert tm.get_task(tid).status == TaskStatus.INTERRUPTED
    summaries = tm.list_task_summaries("web:t")
    assert summaries[0]["status"] == "interrupted"
    # The append-only record keeps the legacy spelling.
    legacy = [e for _, e in tm.events_since(tid) if e["event"] == "task.failed"]
    assert legacy and legacy[-1]["error"] == "orphaned_on_restart"


def test_an_ordinary_failure_is_still_failed(tm):
    tid = _task(tm, {"event": "task.started"}, {"event": "task.failed", "exit_code": 1})
    assert tm.get_task(tid).status == TaskStatus.FAILED


def test_queue_refuses_to_move_a_terminal_task(tmp_path):
    q = TaskQueue(tmp_path / "global")
    tid = q.enqueue("_default", "web:t", "x", check_quota=False)
    q.update_status(tid, TaskState.CANCELLED)
    with pytest.raises(InvalidTransitionError):
        q.update_status(tid, TaskState.INTERRUPTED)
    q.update_status(tid, TaskState.CANCELLED)  # same state: idempotent
    assert q.get_task(tid).status == TaskState.CANCELLED
