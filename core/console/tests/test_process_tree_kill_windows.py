"""Windows: stopping an agent CLI ends its whole process tree, and pid probes never use os.kill.

On Windows ``os.kill``/``Popen.kill`` is TerminateProcess on ONE pid. A CLI started through an
npm ``.cmd`` shim is cmd.exe -> node.exe, so the console's "stop" ended cmd.exe and the engine
kept running. ``os.kill(pid, 0)`` is no probe there either (signal 0 is CTRL_C_EVENT).
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from corvin_core import process_signals  # noqa: E402
from corvin_core.task_manager import _os_pid_exists  # noqa: E402

pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows process-tree semantics")

# A parent that starts a long-sleeping grandchild and prints its pid, like cmd.exe -> node.exe.
_TREE = (
    "import subprocess, sys, time\n"
    "c = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
    "print(c.pid, flush=True)\n"
    "time.sleep(60)\n"
)


def _tree() -> tuple[subprocess.Popen, int]:
    proc = subprocess.Popen([sys.executable, "-c", _TREE], stdout=subprocess.PIPE, text=True)
    grandchild = int(proc.stdout.readline())
    return proc, grandchild


def _gone(pid: int, timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not _os_pid_exists(pid):
            return True
        time.sleep(0.1)
    return False


@pytest.fixture(autouse=True)
def _no_audit(monkeypatch):
    monkeypatch.setattr(process_signals, "record_signal", lambda **_k: None)


def test_hard_kill_reaches_the_grandchild():
    proc, grandchild = _tree()
    try:
        process_signals.hard_kill(proc, reason="test")
        proc.wait(timeout=10)
        assert _gone(grandchild), "grandchild survived hard_kill (only the top pid was ended)"
    finally:
        process_signals.kill_tree_windows(grandchild)


def test_send_sigterm_reaches_the_grandchild():
    import signal

    proc, grandchild = _tree()
    try:
        process_signals.send_signal(proc.pid, signal.SIGTERM, reason="test")
        proc.wait(timeout=10)
        assert _gone(grandchild), "grandchild survived SIGTERM"
    finally:
        process_signals.kill_tree_windows(grandchild)


def test_kill_tree_on_a_gone_pid_reports_false():
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    proc.wait()
    assert process_signals.kill_tree_windows(proc.pid) is False


def test_pid_probe_is_non_destructive_for_this_process():
    assert _os_pid_exists(os.getpid()) is True
