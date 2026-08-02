"""Tests for bridge_manager.py::ensure_windows_autostart -- registers a
bridge channel for Scheduled-Task restart-forever supervision on Windows.

Reported live: a fresh Windows install (or a pip upgrade) left the bridge
NOT running after a reboot/relogin, or dead-and-never-restarted after a
crash. start_channel_detached() (the Console "Start bridge" button's engine)
genuinely detaches the daemon+adapter from the caller's terminal, but a
detached process is still a ONE-SHOT spawn -- nothing supervises it.
Console autostart was already registered by default; bridge autostart was
opt-in-only (a separate `bridge.ps1 install-autostart` command nobody knew
to run). ensure_windows_autostart() closes that gap by registering the same
Scheduled-Task supervision automatically, every time a channel is started.

subprocess.run (the actual powershell.exe invocation) is mocked -- no
Windows box is available in this environment, matching the pattern already
used in test_bridge_manager_shutdown.py for this file's other Windows-only
paths. What IS verified for real: the exact argv built, the idempotent-skip
cache, and (via source inspection) that start_channel_detached() actually
calls this function -- not just that the function exists in isolation.

Run with: python3 operator/bridges/test_bridge_manager_windows_autostart.py
"""
from __future__ import annotations

import subprocess
import sys
import unittest
import unittest.mock as mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import bridge_manager  # noqa: E402


class TestEnsureWindowsAutostart(unittest.TestCase):
    def setUp(self):
        bridge_manager._WINDOWS_AUTOSTART_ATTEMPTED.clear()

    def tearDown(self):
        bridge_manager._WINDOWS_AUTOSTART_ATTEMPTED.clear()

    def test_noop_on_posix(self):
        with mock.patch.object(bridge_manager.sys, "platform", "linux"), \
             mock.patch.object(bridge_manager.subprocess, "run") as run_mock:
            result = bridge_manager.ensure_windows_autostart("discord")
        self.assertTrue(result["ok"])
        self.assertEqual(result.get("skipped"), "not windows")
        run_mock.assert_not_called()

    def test_missing_bridge_ps1_returns_error_without_spawning(self):
        with mock.patch.object(bridge_manager.sys, "platform", "win32"), \
             mock.patch.object(bridge_manager, "_BRIDGE_DIR", Path("/nonexistent-dir-xyz")), \
             mock.patch.object(bridge_manager.subprocess, "run") as run_mock:
            result = bridge_manager.ensure_windows_autostart("discord")
        self.assertFalse(result["ok"])
        self.assertIn("bridge.ps1", result["error"])
        run_mock.assert_not_called()

    def test_calls_powershell_with_correct_argv(self):
        with mock.patch.object(bridge_manager.sys, "platform", "win32"), \
             mock.patch.object(bridge_manager, "_BRIDGE_DIR", Path(__file__).resolve().parent), \
             mock.patch.object(bridge_manager.subprocess, "run") as run_mock:
            run_mock.return_value = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
            result = bridge_manager.ensure_windows_autostart("discord")
        self.assertTrue(result["ok"])
        run_mock.assert_called_once()
        argv = run_mock.call_args[0][0]
        self.assertEqual(argv[0], "powershell.exe")
        self.assertIn("-NoProfile", argv)
        self.assertIn("-ExecutionPolicy", argv)
        self.assertIn("Bypass", argv)
        self.assertIn(str(bridge_manager._BRIDGE_DIR / "bridge.ps1"), argv)
        self.assertIn("install-autostart", argv)
        self.assertIn("discord", argv)
        # channel must be the LAST positional arg, not swallowed by an
        # earlier flag's own value.
        self.assertEqual(argv[-1], "discord")

    def test_nonzero_exit_reports_failure(self):
        with mock.patch.object(bridge_manager.sys, "platform", "win32"), \
             mock.patch.object(bridge_manager, "_BRIDGE_DIR", Path(__file__).resolve().parent), \
             mock.patch.object(bridge_manager.subprocess, "run") as run_mock:
            run_mock.return_value = subprocess.CompletedProcess(
                args=[], returncode=1, stdout="", stderr="Access is denied.",
            )
            result = bridge_manager.ensure_windows_autostart("discord")
        self.assertFalse(result["ok"])

    def test_subprocess_exception_is_caught_not_raised(self):
        with mock.patch.object(bridge_manager.sys, "platform", "win32"), \
             mock.patch.object(bridge_manager, "_BRIDGE_DIR", Path(__file__).resolve().parent), \
             mock.patch.object(bridge_manager.subprocess, "run", side_effect=OSError("no such file")):
            result = bridge_manager.ensure_windows_autostart("discord")
        self.assertFalse(result["ok"])
        self.assertIn("error", result)

    def test_second_call_for_same_channel_skips_the_subprocess_spawn(self):
        with mock.patch.object(bridge_manager.sys, "platform", "win32"), \
             mock.patch.object(bridge_manager, "_BRIDGE_DIR", Path(__file__).resolve().parent), \
             mock.patch.object(bridge_manager.subprocess, "run") as run_mock:
            run_mock.return_value = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
            first = bridge_manager.ensure_windows_autostart("discord")
            second = bridge_manager.ensure_windows_autostart("discord")
        self.assertTrue(first["ok"])
        self.assertTrue(second["ok"])
        self.assertTrue(second.get("already_attempted"))
        run_mock.assert_called_once()

    def test_different_channel_still_spawns(self):
        with mock.patch.object(bridge_manager.sys, "platform", "win32"), \
             mock.patch.object(bridge_manager, "_BRIDGE_DIR", Path(__file__).resolve().parent), \
             mock.patch.object(bridge_manager.subprocess, "run") as run_mock:
            run_mock.return_value = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
            bridge_manager.ensure_windows_autostart("discord")
            bridge_manager.ensure_windows_autostart("telegram")
        self.assertEqual(run_mock.call_count, 2)


class TestStartChannelDetachedCallsAutostart(unittest.TestCase):
    """Reachability proof (e2e-wiring-proof discipline): the function must
    not just exist in isolation -- start_channel_detached() (the Console
    "Start bridge" button's real entry point) must actually call it."""

    def test_start_channel_detached_calls_ensure_windows_autostart(self):
        src = (Path(__file__).resolve().parent / "bridge_manager.py").read_text()
        start_idx = src.index("def start_channel_detached(")
        next_def_idx = src.index("\ndef ", start_idx + 1)
        body = src[start_idx:next_def_idx]
        self.assertIn(
            "ensure_windows_autostart(channel)", body,
            "start_channel_detached() must call ensure_windows_autostart(channel) "
            "-- otherwise a bridge started via the Console button still has no "
            "restart-on-crash/restart-on-reboot supervision on Windows",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
