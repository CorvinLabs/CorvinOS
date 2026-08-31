"""serve_backend.start() must refuse to launch a second instance when one
is already listening on the target port (2026-08-01).

Root cause, live-observed: install.ps1 creates a Desktop shortcut that
invokes `corvinos-serve` directly (install.ps1 step "3c. Desktop
shortcut"), completely independent of the Scheduled Task supervisor's own
healthz standby loop -- that loop only protects the SUPERVISOR's own
restart cycle, not a manual/duplicate launch through the Desktop icon. A
user double-clicking the Desktop icon while the auto-started supervised
console is already running spawned a second uvicorn process with no
error, no guard, anywhere in this function. Windows' default SO_REUSEADDR
semantics (unlike POSIX) can let a second bind to an already-listening
port succeed instead of failing loudly, so this produced two live console
processes rather than a clean rejection -- confirmed live via
Get-CimInstance showing two `python.exe -m uvicorn corvin_console.
standalone:c...` processes on one Windows box this session.

Run: python3 -m pytest ops/launcher/tests/test_duplicate_instance_guard.py
"""
from __future__ import annotations

import socket
import sys
import threading
import time
import unittest
from pathlib import Path

_THIS = Path(__file__).resolve()
_LAUNCHER = _THIS.parents[1]          # ops/launcher
sys.path.insert(0, str(_LAUNCHER))

from corvin import serve_backend as sb  # noqa: E402


class ConsoleAlreadyRunningProbeTests(unittest.TestCase):
    def test_false_when_nothing_is_listening(self) -> None:
        # A port picked from the ephemeral range is astronomically unlikely
        # to have a real listener; if this ever flakes, raise the port.
        self.assertFalse(sb._console_already_running("127.0.0.1", 47591))

    def test_true_when_something_is_listening(self) -> None:
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.bind(("127.0.0.1", 0))
        srv.listen(1)
        port = srv.getsockname()[1]
        try:
            self.assertTrue(sb._console_already_running("127.0.0.1", port))
        finally:
            srv.close()


class StartRefusesASecondInstanceTests(unittest.TestCase):
    """Real subprocess-free but real-socket exercise of start()'s guard:
    a real listening socket stands in for an already-running console, and
    start() must detect it and return WITHOUT ever invoking subprocess.run
    (i.e. without attempting to launch a second uvicorn)."""

    def setUp(self) -> None:
        self.srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.srv.bind(("127.0.0.1", 0))
        self.srv.listen(1)
        self.port = self.srv.getsockname()[1]
        self.addCleanup(self.srv.close)

        self._orig_subprocess_run = sb.subprocess.run
        self.subprocess_run_called = False

        def _tracking_run(*args, **kwargs):
            self.subprocess_run_called = True
            return self._orig_subprocess_run(*args, **kwargs)

        sb.subprocess.run = _tracking_run
        self.addCleanup(lambda: setattr(sb.subprocess, "run", self._orig_subprocess_run))

        # start() has real side effects (telemetry ping, heartbeat thread,
        # browser open) unrelated to this guard -- neutralise them so this
        # test exercises only the duplicate-instance check.
        self._orig_ping = sb._fire_startup_ping
        self._orig_heartbeat = sb._start_heartbeat
        self._orig_seed = sb._seed_builtin_tools
        self._orig_telemetry_notice = sb._show_telemetry_notice_once
        sb._fire_startup_ping = lambda: None
        sb._start_heartbeat = lambda: None
        sb._seed_builtin_tools = lambda: None
        sb._show_telemetry_notice_once = lambda: None
        self.addCleanup(lambda: setattr(sb, "_fire_startup_ping", self._orig_ping))
        self.addCleanup(lambda: setattr(sb, "_start_heartbeat", self._orig_heartbeat))
        self.addCleanup(lambda: setattr(sb, "_seed_builtin_tools", self._orig_seed))
        self.addCleanup(lambda: setattr(sb, "_show_telemetry_notice_once", self._orig_telemetry_notice))

    def test_start_returns_zero_without_launching_uvicorn(self) -> None:
        exit_code = sb.start(port=self.port, open_browser=False, host="127.0.0.1")
        self.assertEqual(exit_code, 0)
        self.assertFalse(
            self.subprocess_run_called,
            "start() launched a second uvicorn instead of detecting the "
            "already-listening port",
        )

    def test_start_still_offers_to_open_the_browser_to_the_existing_instance(self) -> None:
        """UX: a user double-clicking the Desktop icon while already running
        should land back in their console, not see nothing happen."""
        opened: list[str] = []
        self._orig_schedule = sb._schedule_browser_open
        sb._schedule_browser_open = lambda url, delay: opened.append(url)
        self.addCleanup(lambda: setattr(sb, "_schedule_browser_open", self._orig_schedule))

        sb.start(port=self.port, open_browser=True, host="127.0.0.1")
        self.assertEqual(len(opened), 1)


if __name__ == "__main__":
    unittest.main()
