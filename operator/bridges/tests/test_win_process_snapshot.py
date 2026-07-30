"""Windows process-enumeration regression tests (2026-07-30).

Root cause: every Windows process-scan in bridge_manager.py shelled out to
wmic.exe exclusively. wmic has been deprecated since Windows 10 21H1 and is
REMOVED BY DEFAULT on newer Windows 11 builds — Microsoft's own guidance is
to use the CIM cmdlets instead. On an affected machine, every wmic call
raised FileNotFoundError, was caught, and the caller degraded to
confident=False ("cannot verify whether a daemon is already running").

_scan_channel_daemon_pid()'s caller (the bridge supervisor,
core/plugins/corvin_plugins/bridges/supervisor.py) treats confident=False
as "refuse to start, might be a duplicate" — BY DESIGN, since starting a
real duplicate is worse — so a channel could never auto-start on an
affected Windows install at all, with no config knob and no actionable
error beyond the "cannot verify" message. Observed live (WhatsApp, 2026-07-
30). _pid_cmdline() has the same dependency and gates adapter-duplicate
detection (adapter_running_pid()).

Fix: _win_process_snapshot() prefers PowerShell's Get-CimInstance (the
modern WMI-via-CIM provider, unaffected by wmic.exe's removal), falling
back to wmic only if PowerShell itself cannot be run.

These tests exercise _win_process_snapshot() and its two callers directly
via mocked subprocess output — they cannot run the real Windows-only tools
on this dev machine, but they DO validate the parsing/wiring logic that
would otherwise only be discovered on a real, affected Windows box.

Run: python3 operator/bridges/tests/test_win_process_snapshot.py
"""
from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

_REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_REPO / "operator" / "bridges"))

import bridge_manager as bm  # noqa: E402


class WinProcessSnapshotTests(unittest.TestCase):
    def test_powershell_cim_output_parsed_correctly(self):
        """The primary path: Get-CimInstance via PowerShell (or pwsh)."""
        fake_output = (
            "1234\tC:\\Windows\\System32\\svchost.exe\n"
            "5678\tC:\\Python314\\python.exe C:\\Users\\op\\operator\\bridges\\shared\\adapter.py\n"
            "9999\tnode.exe C:\\corvin\\bridges\\whatsapp\\daemon.js\n"
        )
        with mock.patch.object(bm.shutil, "which",
                                lambda n: "powershell.exe" if n == "powershell" else None), \
             mock.patch.object(bm.subprocess, "run") as mock_run:
            mock_run.return_value = mock.Mock(stdout=fake_output)
            procs, confident = bm._win_process_snapshot()

        self.assertTrue(confident)
        self.assertEqual(len(procs), 3)
        self.assertTrue(procs[5678].endswith("adapter.py"))
        self.assertIn("whatsapp/daemon.js", procs[9999].replace("\\", "/").lower())
        # The command actually run must invoke Get-CimInstance, NOT wmic —
        # the whole point of this fix.
        cmd = mock_run.call_args[0][0]
        self.assertIn("Get-CimInstance", " ".join(cmd))
        self.assertNotIn("wmic", [c.lower() for c in cmd])

    def test_falls_back_to_wmic_when_powershell_unavailable(self):
        """Legacy path: wmic still works if PowerShell itself cannot run."""
        wmic_output = (
            "CommandLine=C:\\Windows\\System32\\svchost.exe\n"
            "ProcessId=1234\n"
            "\n"
            "CommandLine=node.exe C:\\corvin\\bridges\\whatsapp\\daemon.js\n"
            "ProcessId=9999\n"
            "\n"
        )
        with mock.patch.object(bm.shutil, "which", lambda _n: None), \
             mock.patch.object(bm.subprocess, "run") as mock_run:
            mock_run.return_value = mock.Mock(stdout=wmic_output)
            procs, confident = bm._win_process_snapshot()

        self.assertTrue(confident)
        self.assertIn("whatsapp/daemon.js", procs[9999].replace("\\", "/").lower())

    def test_confident_false_only_when_nothing_at_all_works(self):
        """wmic removed AND PowerShell unavailable/blocked -- 'could not
        look', never silently treated as 'found nothing running'."""
        with mock.patch.object(bm.shutil, "which", lambda _n: None), \
             mock.patch.object(bm.subprocess, "run", side_effect=FileNotFoundError()):
            procs, confident = bm._win_process_snapshot()

        self.assertFalse(confident)
        self.assertEqual(procs, {})

    def test_powershell_present_but_produces_nothing_falls_back_to_wmic(self):
        """A PowerShell that runs but returns empty output (e.g. execution
        policy silently swallowing the command) must not be treated as
        'confident: zero processes' -- it must still try wmic before
        giving up."""
        wmic_output = (
            "CommandLine=node.exe C:\\corvin\\bridges\\whatsapp\\daemon.js\n"
            "ProcessId=9999\n"
        )

        def _run(cmd, **_kw):
            if "wmic" in [c.lower() for c in cmd]:
                return mock.Mock(stdout=wmic_output)
            return mock.Mock(stdout="")

        with mock.patch.object(bm.shutil, "which",
                                lambda n: "powershell.exe" if n == "powershell" else None), \
             mock.patch.object(bm.subprocess, "run", side_effect=_run):
            procs, confident = bm._win_process_snapshot()

        self.assertTrue(confident)
        self.assertIn(9999, procs)

    def test_timeout_on_powershell_falls_back_to_wmic(self):
        wmic_output = "CommandLine=node.exe daemon.js\nProcessId=42\n"

        def _run(cmd, **_kw):
            if "wmic" in [c.lower() for c in cmd]:
                return mock.Mock(stdout=wmic_output)
            raise subprocess.TimeoutExpired(cmd, 15)

        with mock.patch.object(bm.shutil, "which",
                                lambda n: "powershell.exe" if n == "powershell" else None), \
             mock.patch.object(bm.subprocess, "run", side_effect=_run):
            procs, confident = bm._win_process_snapshot()

        self.assertTrue(confident)
        self.assertEqual(procs.get(42), "node.exe daemon.js")


class WindowsCallerWiringTests(unittest.TestCase):
    """_pid_cmdline() and _scan_channel_daemon_pid() must route through
    _win_process_snapshot() on Windows, not reimplement their own wmic call."""

    def test_pid_cmdline_uses_snapshot(self):
        snapshot = ({777: "python.exe C:\\bridges\\shared\\adapter.py"}, True)
        with mock.patch.object(bm.os, "name", "nt"), \
             mock.patch.object(bm, "_win_process_snapshot", return_value=snapshot):
            self.assertEqual(
                bm._pid_cmdline(777), "python.exe C:\\bridges\\shared\\adapter.py",
            )
            self.assertEqual(bm._pid_cmdline(888), "")  # not in snapshot

    def test_pid_cmdline_empty_when_unconfident(self):
        with mock.patch.object(bm.os, "name", "nt"), \
             mock.patch.object(bm, "_win_process_snapshot", return_value=({}, False)):
            self.assertEqual(bm._pid_cmdline(777), "")

    # NOTE: real pathlib.WindowsPath cannot be instantiated on a POSIX host
    # regardless of a mocked os.name (CPython ties `_flavour.is_supported`
    # to the ACTUAL platform) -- so these tests replace the `Path` NAME
    # inside bridge_manager's own namespace with a stub whose "/proc" probe
    # answers False, instead of trying to fake a real WindowsPath. On real
    # Windows this stub is never used at all -- Path("/proc") behaves
    # normally there.
    class _FakeNonProcPath:
        def __init__(self, _arg):
            pass

        def is_dir(self):
            return False

    def test_scan_channel_daemon_pid_uses_snapshot(self):
        snapshot = ({
            111: "svchost.exe",
            222: "node.exe C:\\corvin\\bridges\\telegram\\daemon.js",
        }, True)
        with mock.patch.object(bm.os, "name", "nt"), \
             mock.patch.object(bm, "Path", self._FakeNonProcPath), \
             mock.patch.object(bm, "_win_process_snapshot", return_value=snapshot):
            pid, confident = bm._scan_channel_daemon_pid("telegram")
        self.assertEqual(pid, 222)
        self.assertTrue(confident)

    def test_scan_channel_daemon_pid_refuses_when_unconfident(self):
        """The exact regression: no daemon found because enumeration itself
        failed must come back as 'cannot verify', never 'nothing running'."""
        with mock.patch.object(bm.os, "name", "nt"), \
             mock.patch.object(bm, "Path", self._FakeNonProcPath), \
             mock.patch.object(bm, "_win_process_snapshot", return_value=({}, False)):
            pid, confident = bm._scan_channel_daemon_pid("whatsapp")
        self.assertEqual(pid, 0)
        self.assertFalse(confident)

    def test_own_pid_excluded_from_scan(self):
        snapshot = ({bm.os.getpid(): "node.exe C:\\corvin\\bridges\\slack\\daemon.js"}, True)
        with mock.patch.object(bm.os, "name", "nt"), \
             mock.patch.object(bm, "Path", self._FakeNonProcPath), \
             mock.patch.object(bm, "_win_process_snapshot", return_value=snapshot):
            pid, confident = bm._scan_channel_daemon_pid("slack")
        self.assertEqual(pid, 0)
        self.assertTrue(confident)


if __name__ == "__main__":
    unittest.main()
