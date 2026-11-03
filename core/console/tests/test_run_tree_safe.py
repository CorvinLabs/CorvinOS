"""run_tree_safe: a timed-out command whose grandchild holds the pipes must not hang
(CPython's subprocess.run does an unbounded communicate() after kill() on Windows)."""
from __future__ import annotations

import os
import subprocess
import sys
import time

import pytest

from corvin_core.process_signals import run_tree_safe

_HOLDER = (
    "import subprocess, sys, time\n"
    "subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
    "time.sleep(60)\n"
)


def test_success_matches_subprocess_run():
    r = run_tree_safe([sys.executable, "-c", "import sys; print(sys.stdin.read().upper())"],
                      input="abc", capture_output=True, text=True, timeout=30)
    assert r.returncode == 0 and r.stdout.strip() == "ABC"


def test_check_raises():
    with pytest.raises(subprocess.CalledProcessError):
        run_tree_safe([sys.executable, "-c", "raise SystemExit(3)"], timeout=30, check=True)


@pytest.mark.skipif(os.name != "nt", reason="the hang is Windows-specific")
def test_timeout_with_pipe_holding_grandchild_does_not_hang():
    t0 = time.monotonic()
    with pytest.raises(subprocess.TimeoutExpired):
        run_tree_safe([sys.executable, "-c", _HOLDER], capture_output=True, text=True, timeout=2)
    assert time.monotonic() - t0 < 15


def test_respects_patched_subprocess_run(monkeypatch):
    sentinel = subprocess.CompletedProcess(["x"], 0, "mocked", "")
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: sentinel)
    assert run_tree_safe(["x"], timeout=1) is sentinel
