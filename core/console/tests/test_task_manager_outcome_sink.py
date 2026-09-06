"""``TaskManager.record_event`` hands finished tasks to the learning outcome sink.

The tenant comes from the task's OWN metadata (``create_task(tenant_id=...)``),
never from the environment; a task created without one records nothing.
"""
from __future__ import annotations

import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_REPO = _HERE.parents[2]
for _p in (str(_REPO / "core" / "console"), str(_REPO)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from corvin_core.task_manager import TaskManager  # noqa: E402


def _capture(monkeypatch):
    calls: list[dict] = []
    import core.learning.outcome_sink as sink

    def fake(**kw):
        calls.append(kw)
        return bool(kw.get("tenant_id"))

    monkeypatch.setattr(sink, "emit_task_outcome", fake)
    return calls


def test_completed_task_reports_outcome_with_its_own_tenant(tmp_path: Path, monkeypatch):
    calls = _capture(monkeypatch)
    tm = TaskManager(tmp_path / "tasks")
    task_id = tm.create_task(chat_key="web:s1", instruction="hi", check_quota=False, tenant_id="acme-corp", engine="native")
    tm.record_event(task_id, {"event": "task.started"})
    tm.record_event(task_id, {"event": "task.completed", "exit_code": 0})
    assert len(calls) == 1
    c = calls[0]
    assert c["tenant_id"] == "acme-corp" and c["task_id"] == task_id
    assert c["status"] == "completed" and c["exit_code"] == 0
    assert c["engine"] == "native"
    assert isinstance(c["duration_ms"], int)


def test_failed_task_reports_failure(tmp_path: Path, monkeypatch):
    calls = _capture(monkeypatch)
    tm = TaskManager(tmp_path / "tasks")
    task_id = tm.create_task(chat_key="web:s1", instruction="hi", check_quota=False, tenant_id="_default")
    tm.record_event(task_id, {"event": "task.started"})
    tm.record_event(task_id, {"event": "task.failed", "exit_code": 2})
    assert calls[0]["status"] == "failed" and calls[0]["exit_code"] == 2


def test_task_without_tenant_records_nothing(tmp_path: Path, monkeypatch):
    calls = _capture(monkeypatch)
    tm = TaskManager(tmp_path / "tasks")
    task_id = tm.create_task(chat_key="web:s1", instruction="hi", check_quota=False)
    tm.record_event(task_id, {"event": "task.completed", "exit_code": 0})
    assert calls and calls[0]["tenant_id"] is None  # sink drops it; lifecycle unaffected
    assert tm.get_task(task_id).status.value == "completed"


def test_sink_exception_never_breaks_lifecycle(tmp_path: Path, monkeypatch):
    import core.learning.outcome_sink as sink

    def boom(**_kw):
        raise RuntimeError("sink down")

    monkeypatch.setattr(sink, "emit_task_outcome", boom)
    tm = TaskManager(tmp_path / "tasks")
    task_id = tm.create_task(chat_key="web:s1", instruction="hi", check_quota=False, tenant_id="_default")
    tm.record_event(task_id, {"event": "task.completed", "exit_code": 0})
    assert tm.get_task(task_id).status.value == "completed"
