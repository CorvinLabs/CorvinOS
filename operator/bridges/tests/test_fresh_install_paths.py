"""Fresh-install queue/adapter regression tests (2026-07-22).

Root cause class: on a wheel install the adapter defaulted its queues to the
source/vendored shared/ dir while daemons spawned from runtime dirs polled
<corvin_home>/bridges/shared — two different directories, so inbound envelopes
were never picked up and replies never reached a daemon. Additionally the web
console's bridge Start button launched ONLY the Node daemon; nothing ever
started the adapter.

Covers:
  1. _adapter_queue_env pins ADAPTER_INBOX/OUTBOX/PROCESSED to the runtime
     shared dir and creates the directories.
  2. _adapter_queue_env respects operator overrides (service.env wins).
  3. _pid_alive: own PID alive, bogus PID dead.
  4. ensure_adapter_detached: spawns once, second call is already_running,
     pidfile written; a boot-crash adapter is reported as error.

Run: python3 operator/bridges/tests/test_fresh_install_paths.py
"""
from __future__ import annotations

import os
import signal
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest
from pathlib import Path
from unittest.mock import patch

_REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_REPO / "operator" / "bridges"))

import bridge_manager as bm  # noqa: E402


class AdapterQueueEnvTests(unittest.TestCase):
    def test_pins_queues_to_runtime_shared(self):
        with tempfile.TemporaryDirectory() as td:
            home = Path(td) / "home"
            with patch.dict(os.environ, {"CORVIN_HOME": str(home)}):
                env: dict = {}
                bm._adapter_queue_env(env)
                shared = home / "bridges" / "shared"
                self.assertEqual(env["ADAPTER_INBOX"], str(shared / "inbox"))
                self.assertEqual(env["ADAPTER_OUTBOX"], str(shared / "outbox"))
                self.assertEqual(env["ADAPTER_PROCESSED"], str(shared / "processed"))
                for sub in ("inbox", "outbox", "processed"):
                    self.assertTrue((shared / sub).is_dir(),
                                    f"{sub} dir must be created for the adapter")

    def test_operator_override_wins(self):
        with tempfile.TemporaryDirectory() as td:
            home = Path(td) / "home"
            with patch.dict(os.environ, {"CORVIN_HOME": str(home)}):
                env = {"ADAPTER_OUTBOX": "/custom/outbox"}
                bm._adapter_queue_env(env)
                self.assertEqual(env["ADAPTER_OUTBOX"], "/custom/outbox",
                                 "service.env override must not be clobbered")
                self.assertTrue(env["ADAPTER_INBOX"].endswith(os.path.join("shared", "inbox")))


class PidAliveTests(unittest.TestCase):
    def test_own_pid_alive(self):
        self.assertTrue(bm._pid_alive(os.getpid()))

    def test_bogus_pid_dead(self):
        self.assertFalse(bm._pid_alive(2 ** 22 + 1234))

    def test_nonpositive_dead(self):
        self.assertFalse(bm._pid_alive(0))
        self.assertFalse(bm._pid_alive(-5))


class EnsureAdapterDetachedTests(unittest.TestCase):
    def _fake_bridge_dir(self, td: Path, body: str) -> Path:
        bridge_dir = td / "bridges_src"
        (bridge_dir / "shared").mkdir(parents=True)
        (bridge_dir / "shared" / "adapter.py").write_text(
            textwrap.dedent(body), encoding="utf-8")
        return bridge_dir

    def test_spawn_then_already_running_then_crash_report(self):
        with tempfile.TemporaryDirectory() as td:
            tdp = Path(td)
            home = tdp / "home"
            sleeper = self._fake_bridge_dir(
                tdp, """
                import time
                time.sleep(30)
                """)
            with patch.dict(os.environ, {"CORVIN_HOME": str(home)}), \
                 patch.object(bm, "_BRIDGE_DIR", sleeper):
                first = bm.ensure_adapter_detached()
                try:
                    self.assertTrue(first["ok"], first)
                    pid = first["pid"]
                    pidfile = home / "run" / "adapter.pid"
                    self.assertTrue(pidfile.exists(), "pidfile must be written")
                    self.assertEqual(pidfile.read_text().strip(), str(pid))

                    second = bm.ensure_adapter_detached()
                    self.assertTrue(second.get("already_running"),
                                    "second call must not spawn a twin adapter")
                    self.assertEqual(second["pid"], pid)
                finally:
                    try:
                        os.kill(first.get("pid", 0), signal.SIGKILL)
                    except OSError:
                        pass

    def test_boot_crash_is_reported(self):
        with tempfile.TemporaryDirectory() as td:
            tdp = Path(td)
            home = tdp / "home"
            crasher = self._fake_bridge_dir(
                tdp, """
                import sys
                sys.exit(7)
                """)
            with patch.dict(os.environ, {"CORVIN_HOME": str(home)}), \
                 patch.object(bm, "_BRIDGE_DIR", crasher):
                res = bm.ensure_adapter_detached()
                self.assertFalse(res["ok"])
                self.assertIn("exited on boot", res["error"])

    def test_pid_reuse_does_not_read_as_running(self):
        """A pidfile PID recycled onto an unrelated process (our own test PID,
        whose cmdline is NOT adapter.py) must not count as a live adapter."""
        with tempfile.TemporaryDirectory() as td:
            tdp = Path(td)
            home = tdp / "home"
            sleeper = self._fake_bridge_dir(tdp, "import time\ntime.sleep(30)")
            with patch.dict(os.environ, {"CORVIN_HOME": str(home)}), \
                 patch.object(bm, "_BRIDGE_DIR", sleeper):
                pidfile = bm._adapter_pidfile()
                pidfile.parent.mkdir(parents=True, exist_ok=True)
                # This test process is alive but is not an adapter.
                pidfile.write_text(str(os.getpid()), encoding="utf-8")
                self.assertEqual(
                    bm._adapter_running_pid(sleeper / "shared" / "adapter.py"), 0,
                    "PID reuse must be rejected via cmdline verification")

    def test_cross_launcher_adapter_detected(self):
        """An adapter started by another launcher (no pidfile) is still found
        by the system-wide cmdline scan, so we don't spawn a duplicate."""
        with tempfile.TemporaryDirectory() as td:
            tdp = Path(td)
            home = tdp / "home"
            sleeper = self._fake_bridge_dir(tdp, "import time\ntime.sleep(30)")
            adapter_py = sleeper / "shared" / "adapter.py"
            # Launch it directly, as bridge.sh/systemd would — no pidfile.
            proc = subprocess.Popen([sys.executable, str(adapter_py)])
            try:
                # give it a moment to appear in the process table
                import time as _t
                _t.sleep(0.5)
                with patch.dict(os.environ, {"CORVIN_HOME": str(home)}), \
                     patch.object(bm, "_BRIDGE_DIR", sleeper):
                    found = bm._adapter_running_pid(adapter_py)
                    self.assertEqual(found, proc.pid,
                                     "system-wide scan must find a foreign-launched adapter")
                    status = bm.ensure_adapter_detached()
                    self.assertTrue(status.get("already_running"))
                    self.assertEqual(status["pid"], proc.pid)
            finally:
                proc.kill()

    def test_missing_adapter_is_error(self):
        with tempfile.TemporaryDirectory() as td:
            tdp = Path(td)
            home = tdp / "home"
            empty = tdp / "nothing"
            empty.mkdir()
            with patch.dict(os.environ, {"CORVIN_HOME": str(home)}), \
                 patch.object(bm, "_BRIDGE_DIR", empty):
                res = bm.ensure_adapter_detached()
                self.assertFalse(res["ok"])
                self.assertIn("not found", res["error"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
