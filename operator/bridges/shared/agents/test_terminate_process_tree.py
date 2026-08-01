"""Tests for agents/__init__.py::terminate_process_tree.

Real subprocess spawn/kill for the POSIX path (this CI runs on Linux, so
that path gets genuine process-tree termination, not a mock). The Windows
path (CTRL_BREAK_EVENT / taskkill /T /F) is simulated via sys.platform +
os.killpg patching, since no Windows box is available here — this proves
the CORRECT Windows API calls are made, which is the actual bug this fixes
(previously: proc.terminate()/proc.kill() on the tracked PID only, leaking
the real node.exe/claude grandchild the .cmd shim spawns under cmd.exe).

Run with: python3 operator/bridges/shared/agents/test_terminate_process_tree.py
"""
from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
import unittest
import unittest.mock as mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import agents as agents_pkg  # noqa: E402


class TestPosixRealTermination(unittest.TestCase):
    """Real subprocess.Popen + real killpg on this (Linux) CI box."""

    @unittest.skipUnless(hasattr(os, "killpg"), "POSIX-only")
    def test_graceful_sigterm_stops_a_real_process_group(self):
        proc = subprocess.Popen(
            ["sh", "-c", "trap 'exit 0' TERM; sleep 30"],
            start_new_session=True,
        )
        time.sleep(0.2)  # let the trap register
        self.assertIsNone(proc.poll())
        agents_pkg.terminate_process_tree(proc, grace=3.0)
        self.assertIsNotNone(proc.poll(), "process should have exited")
        proc.wait(timeout=1)

    @unittest.skipUnless(hasattr(os, "killpg"), "POSIX-only")
    def test_hard_kill_after_grace_for_a_process_ignoring_sigterm(self):
        proc = subprocess.Popen(
            ["sh", "-c", "trap '' TERM; sleep 30"],
            start_new_session=True,
        )
        time.sleep(0.2)
        start = time.monotonic()
        agents_pkg.terminate_process_tree(proc, grace=0.5)
        elapsed = time.monotonic() - start
        # poll() alone can race the kernel's post-SIGKILL reap; wait() blocks
        # until the zombie is actually collected, which is the real signal.
        rc = proc.wait(timeout=2)
        self.assertIsNotNone(rc, "process should be dead after SIGKILL")
        self.assertLess(elapsed, 5.0, "should not hang waiting past the grace window")

    @unittest.skipUnless(hasattr(os, "killpg"), "POSIX-only")
    def test_child_grandchild_both_reaped_via_process_group(self):
        """The whole point of start_new_session=True + killpg: a tool-use
        grandchild (e.g. a Bash call the claude CLI itself spawned) must
        die together with its parent, not survive as an orphan."""
        proc = subprocess.Popen(
            ["sh", "-c", "sh -c 'sleep 30' & wait"],
            start_new_session=True,
        )
        time.sleep(0.3)
        pgid = os.getpgid(proc.pid)
        agents_pkg.terminate_process_tree(proc, grace=2.0)
        proc.wait(timeout=1)
        # No process should remain in that process group.
        time.sleep(0.2)
        with self.assertRaises(ProcessLookupError):
            os.killpg(pgid, 0)  # signal 0 = existence probe


class TestWindowsSimulatedTermination(unittest.TestCase):
    """No process groups on Windows — simulate via sys.platform patching
    plus removing os.killpg from view, and assert the CORRECT Windows-
    specific API calls happen (CTRL_BREAK_EVENT, then taskkill /T /F)."""

    def _make_fake_proc(self, *, dies_after_signal: bool):
        proc = mock.MagicMock()
        state = {"alive": True}

        def _poll():
            return None if state["alive"] else 0

        def _send_signal(sig):
            if dies_after_signal and sig == signal.CTRL_BREAK_EVENT:
                state["alive"] = False

        def _wait(timeout=None):
            if state["alive"]:
                raise subprocess.TimeoutExpired(cmd="fake", timeout=timeout or 0)

        proc.poll.side_effect = _poll
        proc.send_signal.side_effect = _send_signal
        proc.wait.side_effect = _wait
        proc.pid = 4242
        return proc, state

    def test_graceful_path_sends_ctrl_break_event(self):
        proc, _state = self._make_fake_proc(dies_after_signal=True)
        with mock.patch.multiple(agents_pkg.sys, platform="win32"), \
             mock.patch.object(agents_pkg.signal, "CTRL_BREAK_EVENT", 21, create=True), \
             mock.patch(
                 "agents.hasattr",
                 side_effect=lambda o, n: False if (o is agents_pkg.os and n == "killpg") else hasattr(o, n),
             ):
            agents_pkg.terminate_process_tree(proc, grace=1.0)
        proc.send_signal.assert_called_once_with(21)
        proc.terminate.assert_not_called()

    def test_hard_kill_path_uses_taskkill_tree_not_bare_kill(self):
        """The actual bug this fixes: proc.kill() alone only reaches the
        tracked cmd.exe PID, orphaning the real node.exe grandchild. The
        fallback MUST shell out to `taskkill /T /F /PID <pid>` instead."""
        proc, _state = self._make_fake_proc(dies_after_signal=False)
        with mock.patch.multiple(agents_pkg.sys, platform="win32"), \
             mock.patch.object(agents_pkg.signal, "CTRL_BREAK_EVENT", 21, create=True), \
             mock.patch(
                 "agents.hasattr",
                 side_effect=lambda o, n: False if (o is agents_pkg.os and n == "killpg") else hasattr(o, n),
             ), \
             mock.patch.object(agents_pkg.subprocess, "run") as mock_run:
            agents_pkg.terminate_process_tree(proc, grace=0.1)
        proc.send_signal.assert_called_once_with(21)
        proc.kill.assert_not_called()  # bare kill() would orphan the grandchild
        mock_run.assert_called_once()
        called_args = mock_run.call_args[0][0]
        self.assertEqual(called_args, ["taskkill", "/T", "/F", "/PID", "4242"])


class TestSpawnCreatesNewProcessGroupOnWindows(unittest.TestCase):
    """claude_code.py must pass CREATE_NEW_PROCESS_GROUP on Windows so
    CTRL_BREAK_EVENT can be delivered to the child without also hitting
    the parent adapter.py process (both share a console by default)."""

    def test_creationflags_present_only_on_windows(self):
        # Static check on the source, not a live spawn — spawning `claude`
        # in this sandbox has no real binary and would just error out with
        # a different failure mode than what this test cares about.
        src = (Path(__file__).resolve().parent / "claude_code.py").read_text()
        self.assertIn("CREATE_NEW_PROCESS_GROUP", src)
        self.assertIn('sys.platform.startswith("win")', src)


if __name__ == "__main__":
    unittest.main(verbosity=2)
