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

    # 2026-10-21: the sweep with no injected procs file read the REAL cgroup of whoever ran
    # it. test_adapter_skill_inject.py starts adapter.py from pytest, terminate() drained it,
    # and the sweep SIGTERMed pytest, the shell and the Claude Code session (exit 143, three
    # times that night). The guard: outside a service's main process nothing is signalled.
    # This test proves itself — without the guard it would kill its own runner.
    def test_sweep_outside_a_service_main_process_signals_nothing(self):
        self.assertFalse(unit_sweep.running_as_service_main(),
                         "a test runner must never count as a service main process")
        child = _sleeper()
        try:
            self.assertEqual(unit_sweep.sweep_unit_leftovers(grace_s=0.2), 0)
            time.sleep(0.3)
            self.assertIsNone(child.poll(), "a sibling in the runner's cgroup was signalled")
        finally:
            child.kill()
            child.wait(timeout=5)

    def test_service_main_detection_needs_a_systemd_parent_and_a_service_leaf(self):
        me = os.getpid()                             # /proc/<me>/comm is "python…", not "systemd"
        self.assertFalse(unit_sweep.running_as_service_main(
            ppid=me, cgroup_path="/user.slice/user-1000.slice/user@1000.service/app.slice/x.service"))
        try:
            manager = next(p for p in (1, os.getppid())
                           if Path(f"/proc/{p}/comm").read_text().strip() == "systemd")
        except (StopIteration, OSError):
            manager = 1
        if Path(f"/proc/{manager}/comm").read_text().strip() != "systemd":
            self.skipTest("no systemd parent available to probe")
        self.assertTrue(unit_sweep.running_as_service_main(
            ppid=manager, cgroup_path="/user.slice/user-1000.slice/user@1000.service/app.slice/x.service"))
        self.assertFalse(unit_sweep.running_as_service_main(
            ppid=manager, cgroup_path="/user.slice/user-1000.slice/user@1000.service/app.slice/run-abc.scope"))
        self.assertFalse(unit_sweep.running_as_service_main(ppid=manager, cgroup_path=""))


if __name__ == "__main__":
    unittest.main()
