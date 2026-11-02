"""Pid-liveness probes must be non-destructive and correct on every platform.

``os.kill(pid, 0)`` is only a probe on POSIX. On Windows signal 0 is CTRL_C_EVENT: the call
reports live processes (this one included) as gone, so a live claim/lock was reaped.
Covers corvin_workflows.checkpoint, critical_fixes_roundk2.ExclusiveQueueLock and
core.infrastructure.subprocess_isolation.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[3]
for _p in (_REPO, _REPO / "core" / "console", _REPO / "core" / "workflows",
           _REPO / "corvin_operator" / "context_engineering"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))


def _dead_pid() -> int:
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    proc.wait()
    return proc.pid


def test_checkpoint_pid_alive_sees_this_process_and_a_dead_one():
    from corvin_workflows import checkpoint

    assert checkpoint._pid_alive(os.getpid()) is True
    assert checkpoint._pid_alive(_dead_pid()) is False


def test_queue_lock_held_by_this_process_is_not_stale(tmp_path):
    from critical_fixes_roundk2 import ExclusiveQueueLock

    lock = tmp_path / "q.lock"
    lock.write_text(str(os.getpid()))
    assert ExclusiveQueueLock._is_lock_stale(lock) is False
    lock.write_text(str(_dead_pid()))
    assert ExclusiveQueueLock._is_lock_stale(lock) is True


def test_subprocess_boundary_spawns_and_sees_its_child():
    from core.infrastructure.subprocess_isolation import SubprocessBoundary

    boundary = SubprocessBoundary()
    proc = boundary.spawn_isolated(
        [sys.executable, "-c", "import time; time.sleep(30)"], tenant_id="t")
    try:
        assert boundary.enforce_isolation(proc) is True
    finally:
        subprocess.run(["taskkill", "/F", "/PID", str(proc.process_id)], capture_output=True) \
            if os.name == "nt" else os.kill(proc.process_id, 9)
