"""PLAN bg-task P2: the drain sweep ends leftover processes of this unit, and only those.

Runs against real processes. The cgroup is never read here: ``sweep_unit_leftovers`` takes an
injected ``cgroup.procs`` file, so the test cannot touch the operator's own sessions.
"""
from __future__ import annotations

import os
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

_SHARED = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_SHARED))

import unit_sweep  # noqa: E402


def _sleeper() -> subprocess.Popen:
    # own session, like the tool processes the engines start
    return subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"], start_new_session=True)


@unittest.skipIf(sys.platform.startswith("win"), "POSIX signals")
class UnitSweepTest(unittest.TestCase):
    def test_leftover_in_the_unit_is_terminated_gracefully(self):
        child = _sleeper()
        with tempfile.TemporaryDirectory() as tmp:
            procs = Path(tmp) / "cgroup.procs"
            procs.write_text(f"{os.getpid()}\n{child.pid}\n", encoding="utf-8")
            n = unit_sweep.sweep_unit_leftovers(grace_s=5.0, procs_file=procs)
        self.assertEqual(n, 1)                       # the adapter itself is never counted or signalled
        self.assertEqual(child.wait(timeout=10), -signal.SIGTERM)

    def test_stubborn_process_is_killed_after_the_grace_period(self):
        stubborn = subprocess.Popen(
            [sys.executable, "-c",
             "import signal,time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(120)"],
            start_new_session=True)
        time.sleep(0.5)                              # let the interpreter install its handler
        unit_sweep.terminate_pids({stubborn.pid}, grace_s=0.5)
        self.assertEqual(stubborn.wait(timeout=10), -signal.SIGKILL)

    def test_a_missing_cgroup_is_a_no_op(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(unit_sweep.sweep_unit_leftovers(procs_file=Path(tmp) / "absent"), 0)

    def test_never_signals_our_own_pid(self):
        self.assertEqual(unit_sweep.terminate_pids({os.getpid()}, grace_s=0.1), 0)


if __name__ == "__main__":
    unittest.main()
