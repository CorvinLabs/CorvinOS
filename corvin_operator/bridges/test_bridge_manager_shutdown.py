"""Tests for bridge_manager.py's cross-platform graceful-shutdown helpers.

Real subprocess spawn/signal/wait for the POSIX path (this CI runs on
Linux). The Windows path (CREATE_NEW_PROCESS_GROUP at spawn,
CTRL_BREAK_EVENT for graceful stop, taskkill /T /F for the hard-kill
escalation) is simulated via sys.platform + signal.CTRL_BREAK_EVENT
patching, since no Windows box is available here.

Run with: python3 operator/bridges/test_bridge_manager_shutdown.py
"""
from __future__ import annotations

import subprocess
import sys
import time
import unittest
import unittest.mock as mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import bridge_manager  # noqa: E402


class TestPosixRealGracefulSignal(unittest.TestCase):
    """Real subprocess.Popen + real SIGTERM delivery on this Linux box."""

    def test_graceful_signal_stops_a_real_process_that_traps_term(self):
        # A shell's `trap` on a signal it isn't currently blocked on is
        # commonly DEFERRED until its foreground child (here: `sleep 30`)
        # exits — so a plain `sh -c "trap ...; sleep 30"` would not react
        # to SIGTERM promptly and this test would time out for a reason
        # that has nothing to do with bridge_manager's own code. Python's
        # own signal handling interrupts a blocking call immediately, so
        # it is used here as a reliable graceful-shutdown test target.
        proc = subprocess.Popen([
            sys.executable, "-c",
            "import signal, time, sys\n"
            "signal.signal(signal.SIGTERM, lambda *a: sys.exit(0))\n"
            "time.sleep(30)\n",
        ])
        time.sleep(0.3)
        self.assertIsNone(proc.poll())
        bridge_manager._graceful_signal(proc)
        rc = proc.wait(timeout=3)
        self.assertIsNotNone(rc)

    def test_hard_kill_stops_a_process_ignoring_term(self):
        proc = subprocess.Popen(["sh", "-c", "trap '' TERM; sleep 30"])
        time.sleep(0.2)
        bridge_manager._hard_kill(proc)
        rc = proc.wait(timeout=3)
        self.assertIsNotNone(rc)


class TestWindowsSimulatedShutdown(unittest.TestCase):
    """No SIGTERM delivery on Windows — simulate via sys.platform +
    signal.CTRL_BREAK_EVENT patching and assert the CORRECT Windows-
    specific API calls happen instead of the POSIX-only ones."""

    def test_graceful_signal_sends_ctrl_break_event_not_terminate(self):
        proc = mock.MagicMock()
        with mock.patch.object(bridge_manager.sys, "platform", "win32"), \
             mock.patch.object(bridge_manager.signal, "CTRL_BREAK_EVENT", 21, create=True):
            bridge_manager._graceful_signal(proc)
        proc.send_signal.assert_called_once_with(21)
        proc.terminate.assert_not_called()

    def test_hard_kill_uses_taskkill_tree_not_bare_kill(self):
        """The actual bug this fixes: p.kill() alone only reaches the
        tracked PID on Windows, orphaning any grandchild (e.g. the real
        node.exe process under the claude.cmd shim's cmd.exe wrapper, or
        a claude subprocess adapter.py itself owns)."""
        proc = mock.MagicMock()
        proc.pid = 9999
        with mock.patch.object(bridge_manager.sys, "platform", "win32"), \
             mock.patch.object(bridge_manager.subprocess, "run") as mock_run:
            bridge_manager._hard_kill(proc)
        proc.kill.assert_not_called()
        mock_run.assert_called_once()
        called_args = mock_run.call_args[0][0]
        self.assertEqual(called_args, ["taskkill", "/T", "/F", "/PID", "9999"])

    def test_graceful_signal_falls_back_to_terminate_on_posix(self):
        proc = mock.MagicMock()
        with mock.patch.object(bridge_manager.sys, "platform", "linux"):
            bridge_manager._graceful_signal(proc)
        proc.terminate.assert_called_once()
        proc.send_signal.assert_not_called()

    def test_hard_kill_falls_back_to_kill_on_posix(self):
        proc = mock.MagicMock()
        with mock.patch.object(bridge_manager.sys, "platform", "linux"):
            bridge_manager._hard_kill(proc)
        proc.kill.assert_called_once()


class TestSourceHasCreateNewProcessGroupOnWindowsSpawn(unittest.TestCase):
    def test_start_fg_passes_creationflags_on_windows(self):
        src = Path(bridge_manager.__file__).read_text()
        self.assertIn("CREATE_NEW_PROCESS_GROUP", src)
        self.assertIn('sys.platform.startswith("win")', src)


if __name__ == "__main__":
    unittest.main(verbosity=2)
