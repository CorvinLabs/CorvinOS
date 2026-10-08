"""ADR-2236 D4: a task whose process still owns background children is not done.

Driven through the real ``TaskManager`` (file-backed, tenant-scoped): the completion of
a task with open children is deferred — status stays RUNNING, no learning outcome is
emitted for a task that is still working — and the real completion that follows
(children closed) is accepted exactly as before.
"""
from __future__ import annotations

import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_REPO = _HERE.parents[2]
for _p in (str(_REPO / "core" / "console"), str(_REPO)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from corvin_core.task_manager import TaskManager, TaskStatus  # noqa: E402


def _outcomes(monkeypatch):
    calls: list[dict] = []
    import core.learning.outcome_sink as sink

    monkeypatch.setattr(sink, "emit_task_outcome", lambda **kw: calls.append(kw) or True)
    return calls


def _running(tmp_path):
    tm = TaskManager(tmp_path / "tasks")
    tid = tm.create_task(chat_key="web:s1", instruction="x", check_quota=False,
                         tenant_id="acme", engine="native")
    tm.record_event(tid, {"event": "task.started"})
    return tm, tid


def test_completion_with_open_children_is_deferred(tmp_path, monkeypatch):
    calls = _outcomes(monkeypatch)
    tm, tid = _running(tmp_path)
    tm.record_event(tid, {"event": "task.completed", "exit_code": 0, "children_open": 2})
    t = tm.get_task(tid)
    assert t.status == TaskStatus.RUNNING and t.ended_at is None
    assert calls == [], "a learning outcome was emitted for a task that is still working"
    deferred = [e for e in tm._read_events(tid) if e["event"] == "task.completion_deferred"]
    assert len(deferred) == 1 and deferred[0]["children_open"] == 2


def test_completion_after_the_children_ended_is_accepted(tmp_path, monkeypatch):
    calls = _outcomes(monkeypatch)
    tm, tid = _running(tmp_path)
    tm.record_event(tid, {"event": "task.completed", "exit_code": 0, "children_open": 1})
    tm.record_event(tid, {"event": "task.completed", "exit_code": 0, "children_open": 0})
    t = tm.get_task(tid)
    assert t.status == TaskStatus.COMPLETED and t.ended_at is not None
    assert len(calls) == 1 and calls[0]["status"] == "completed"


def test_events_without_the_field_behave_exactly_as_before(tmp_path, monkeypatch):
    calls = _outcomes(monkeypatch)
    tm, tid = _running(tmp_path)
    tm.record_event(tid, {"event": "task.completed", "exit_code": 0})
    assert tm.get_task(tid).status == TaskStatus.COMPLETED and len(calls) == 1


def test_a_failure_with_open_children_is_still_recorded(tmp_path, monkeypatch):
    """Only COMPLETION is gated: a task that failed must be allowed to say so."""
    _outcomes(monkeypatch)
    tm, tid = _running(tmp_path)
    tm.record_event(tid, {"event": "task.failed", "exit_code": 1, "children_open": 3})
    assert tm.get_task(tid).status == TaskStatus.FAILED


def test_garbage_in_the_field_does_not_block_completion(tmp_path, monkeypatch):
    _outcomes(monkeypatch)
    tm, tid = _running(tmp_path)
    tm.record_event(tid, {"event": "task.completed", "exit_code": 0, "children_open": "many"})
    assert tm.get_task(tid).status == TaskStatus.COMPLETED
