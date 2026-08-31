"""Tests for GET /chat/sessions/{sid}/workdir-path (reveal-in-file-manager).

Bug report (2026-07-12): a Windows install only ever showed the raw path
text banner, never an actual Explorer window. Root-caused to two issues,
both covered here:

  1. The route's win32 branch spawned `explorer.exe` via subprocess, which
     depends on it being resolvable on the launching process's PATH and can
     fail without raising anything actionable — and the failure was a bare
     `except Exception: pass` with NO server-side log, so it was
     unreproducible from a bug report alone. Fixed to use `os.startfile()`
     (the standard, ShellExecute-backed stdlib call for exactly this) and to
     log any failure.
  2. The frontend silently discarded the `opened` field the route already
     returned, so a failed reveal looked identical to a successful one from
     the UI (covered by a separate frontend fix, not testable from Python).
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from dataclasses import dataclass
from pathlib import Path
from unittest import mock

_THIS = Path(__file__).resolve()
_REPO = _THIS.parents[3]
sys.path.insert(0, str(_REPO / "core" / "console"))


@dataclass
class _FakeSession:
    workdir: Path


@dataclass
class _FakeRec:
    tenant_id: str = "_default"


class WorkdirRevealTests(unittest.TestCase):
    def setUp(self) -> None:
        from corvin_console.routes import chat as chat_routes
        self.chat_routes = chat_routes
        self._tmp = tempfile.TemporaryDirectory()
        self.workdir = Path(self._tmp.name) / "session-workdir"
        self.rec = _FakeRec()

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _call(self, *, reveal: bool):
        with mock.patch.object(
            self.chat_routes.chat_runtime, "get_session",
            return_value=_FakeSession(workdir=self.workdir),
        ):
            return self.chat_routes.get_session_workdir_path("s1", self.rec, reveal=reveal)

    def test_reveal_false_never_touches_the_os_and_reports_not_opened(self) -> None:
        with mock.patch("os.startfile", create=True) as start, \
             mock.patch("subprocess.Popen") as popen:
            result = self._call(reveal=False)
        start.assert_not_called()
        popen.assert_not_called()
        self.assertEqual(result["opened"], False)
        self.assertEqual(result["path"], str(self.workdir))
        # Side effect: the workdir must exist on disk even without reveal —
        # the route's mkdir(parents=True, exist_ok=True) call.
        self.assertTrue(self.workdir.is_dir())

    def test_windows_reveal_uses_os_startfile_not_subprocess(self) -> None:
        with mock.patch.object(self.chat_routes.sys, "platform", "win32"), \
             mock.patch("os.startfile", create=True) as start, \
             mock.patch("subprocess.Popen") as popen:
            result = self._call(reveal=True)
        start.assert_called_once_with(str(self.workdir))
        popen.assert_not_called()
        self.assertEqual(result["opened"], True)

    def test_windows_reveal_failure_is_logged_not_swallowed_silently(self) -> None:
        with mock.patch.object(self.chat_routes.sys, "platform", "win32"), \
             mock.patch("os.startfile", create=True, side_effect=OSError("no shell handler")), \
             mock.patch.object(self.chat_routes, "logger") as logger:
            result = self._call(reveal=True)
        self.assertEqual(result["opened"], False)
        logger.warning.assert_called_once()

    def test_macos_reveal_uses_open(self) -> None:
        with mock.patch.object(self.chat_routes.sys, "platform", "darwin"), \
             mock.patch("subprocess.Popen") as popen:
            result = self._call(reveal=True)
        popen.assert_called_once_with(["open", str(self.workdir)])
        self.assertEqual(result["opened"], True)

    def test_linux_reveal_uses_xdg_open(self) -> None:
        with mock.patch.object(self.chat_routes.sys, "platform", "linux"), \
             mock.patch("subprocess.Popen") as popen:
            result = self._call(reveal=True)
        popen.assert_called_once_with(["xdg-open", str(self.workdir)])
        self.assertEqual(result["opened"], True)


if __name__ == "__main__":
    unittest.main()
