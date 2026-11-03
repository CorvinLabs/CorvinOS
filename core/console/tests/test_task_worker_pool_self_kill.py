"""Windows: a worker the console killed itself (signal_abort -> taskkill /F, rc 1)
is remembered so its exit is classed as an interruption, not a genuine FAILED."""
from __future__ import annotations

from corvin_console import task_worker_pool as twp


class _Proc:
    pid = 0
    terminated = False

    def terminate(self):
        self.terminated = True


def test_signal_abort_marks_self_kill_on_windows(monkeypatch):
    monkeypatch.setattr(twp.sys, "platform", "win32")
    monkeypatch.setattr(twp, "_active_procs", {"t1": _Proc()})
    monkeypatch.setattr(twp, "_self_killed", set())
    import corvin_core.process_signals as ps
    monkeypatch.setattr(ps, "kill_tree_windows", lambda pid: True)
    assert twp.signal_abort("t1") is True
    assert "t1" in twp._self_killed


def test_signal_abort_posix_unchanged(monkeypatch):
    monkeypatch.setattr(twp.sys, "platform", "linux")
    proc = _Proc()
    monkeypatch.setattr(twp, "_active_procs", {"t2": proc})
    monkeypatch.setattr(twp, "_self_killed", set())
    assert twp.signal_abort("t2") is True
    assert proc.terminated and twp._self_killed == set()


def test_unknown_task_not_marked(monkeypatch):
    monkeypatch.setattr(twp.sys, "platform", "win32")
    monkeypatch.setattr(twp, "_active_procs", {})
    monkeypatch.setattr(twp, "_self_killed", set())
    assert twp.signal_abort("nope") is False
    assert twp._self_killed == set()
