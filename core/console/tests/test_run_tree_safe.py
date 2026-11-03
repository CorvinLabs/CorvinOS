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


# The parent exits at once, leaving an orphaned grandchild that holds the pipes:
# taskkill /T on the dead parent cannot reach it, so the bounded drain gives up.
_ORPHAN_HOLDER = (
    "import subprocess, sys\n"
    "subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(40)'])\n"
)


@pytest.mark.skipif(os.name != "nt", reason="the hang is Windows-specific")
def test_timeout_with_orphaned_pipe_holder_does_not_hang():
    # Regression: closing the pipes while communicate()'s reader thread is blocked
    # in read() deadlocked on the buffer lock (hung until the orphan exited).
    t0 = time.monotonic()
    with pytest.raises(subprocess.TimeoutExpired):
        run_tree_safe([sys.executable, "-c", _ORPHAN_HOLDER], capture_output=True,
                      timeout=2, drain_timeout=1)
    assert time.monotonic() - t0 < 20


def test_respects_patched_subprocess_run(monkeypatch):
    sentinel = subprocess.CompletedProcess(["x"], 0, "mocked", "")
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: sentinel)
    assert run_tree_safe(["x"], timeout=1) is sentinel


def test_a_run_patched_before_the_lazy_import_is_still_honoured(monkeypatch):
    """Callers import process_signals lazily; a test that patched subprocess.run FIRST
    must still get its fake, not a real spawn (house_rules retry test, round 8)."""
    import importlib
    import subprocess as sp

    calls = []

    def _fake_run(argv, **kw):
        calls.append(argv)
        return sp.CompletedProcess(argv, 0, "faked", "")

    monkeypatch.setattr(sp, "run", _fake_run)
    import corvin_core.process_signals as ps
    ps = importlib.reload(ps)  # snapshot taken while the fake is installed
    try:
        out = ps.run_tree_safe(["definitely-not-a-real-binary"], timeout=5,
                               capture_output=True, text=True)
        assert out.stdout == "faked" and calls == [["definitely-not-a-real-binary"]]
    finally:
        monkeypatch.undo()
        importlib.reload(ps)
