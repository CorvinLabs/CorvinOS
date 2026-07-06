"""Tests for the auto-update command selection (uv-tool vs pip).

The Windows one-line installer uses ``uv tool install``, whose venv has no pip —
so the historical ``python -m pip install corvinos==<latest>`` upgrade silently
failed there and the autostart never updated. ``_pick_upgrade_command`` must pick
``uv tool upgrade`` for uv-managed installs and ``pip install`` otherwise.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

_THIS = Path(__file__).resolve()
_LAUNCHER = _THIS.parents[1]          # ops/launcher
sys.path.insert(0, str(_LAUNCHER))

from corvin import serve_backend as sb  # noqa: E402


class UpgradeCommandTests(unittest.TestCase):
    def setUp(self) -> None:
        self._orig_uv = sb._is_uv_tool_install
        self._orig_pip = sb._pip_available
        self._orig_which = sb.shutil.which

    def tearDown(self) -> None:
        sb._is_uv_tool_install = self._orig_uv
        sb._pip_available = self._orig_pip
        sb.shutil.which = self._orig_which

    def test_pip_install_flavour(self) -> None:
        sb._is_uv_tool_install = lambda: False
        sb._pip_available = lambda: True
        cmd, manual = sb._pick_upgrade_command("0.10.8")
        self.assertIsNotNone(cmd)
        self.assertEqual(cmd[1:4], ["-m", "pip", "install"])
        self.assertIn("corvinos==0.10.8", cmd)
        self.assertEqual(manual, "pip install corvinos==0.10.8")

    def test_uv_tool_flavour_when_uv_managed(self) -> None:
        sb._is_uv_tool_install = lambda: True
        sb.shutil.which = lambda x: "/home/u/.local/bin/uv" if x == "uv" else None
        cmd, manual = sb._pick_upgrade_command("0.10.8")
        self.assertEqual(cmd, ["/home/u/.local/bin/uv", "tool", "upgrade", "corvinos"])
        self.assertEqual(manual, "uv tool upgrade corvinos")

    def test_uv_flavour_when_pip_missing(self) -> None:
        # Not detected as uv-managed by path, but pip is unavailable and uv exists
        # → still prefer uv (a pip command would fail).
        sb._is_uv_tool_install = lambda: False
        sb._pip_available = lambda: False
        sb.shutil.which = lambda x: "/usr/bin/uv" if x == "uv" else None
        cmd, manual = sb._pick_upgrade_command("0.10.8")
        self.assertEqual(cmd, ["/usr/bin/uv", "tool", "upgrade", "corvinos"])

    def test_uv_managed_but_uv_missing_returns_none(self) -> None:
        sb._is_uv_tool_install = lambda: True
        sb._pip_available = lambda: False
        sb.shutil.which = lambda _x: None
        # Also block the ~/.local/bin fallback probe by pointing HOME at a temp.
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            orig_home = sb.Path.home
            sb.Path.home = staticmethod(lambda: Path(td))  # type: ignore[assignment]
            try:
                cmd, manual = sb._pick_upgrade_command("0.10.8")
            finally:
                sb.Path.home = orig_home  # type: ignore[assignment]
        self.assertIsNone(cmd)
        self.assertEqual(manual, "uv tool upgrade corvinos")

    def test_detect_uv_tool_install_by_prefix(self) -> None:
        orig_prefix = sb.sys.prefix
        try:
            sb.sys.prefix = "/home/u/.local/share/uv/tools/corvinos"
            self.assertTrue(sb._is_uv_tool_install())
            sb.sys.prefix = "/usr/lib/python3.12"
            self.assertFalse(sb._is_uv_tool_install())
        finally:
            sb.sys.prefix = orig_prefix


class WindowsLiveUpgradeSkipTests(unittest.TestCase):
    """A running process's own interpreter/extension files are locked on
    Windows, so a live in-process self-upgrade reliably fails there (unlike
    POSIX). maybe_pypi_autoupdate() must skip the doomed subprocess attempt
    on Windows and print the manual command instead of trying and failing."""

    def setUp(self) -> None:
        self._orig_platform = sb.sys.platform
        self._orig_run = sb.subprocess.run
        self._orig_pick = sb._pick_upgrade_command

    def tearDown(self) -> None:
        sb.sys.platform = self._orig_platform
        sb.subprocess.run = self._orig_run
        sb._pick_upgrade_command = self._orig_pick

    def test_skips_live_subprocess_on_windows(self) -> None:
        sb.sys.platform = "win32"
        sb._pick_upgrade_command = lambda latest: (
            ["uv", "tool", "upgrade", "corvinos"],
            "uv tool upgrade corvinos",
        )
        called = []
        sb.subprocess.run = lambda *a, **k: called.append((a, k)) or (_ for _ in ()).throw(
            AssertionError("subprocess.run must not be called on Windows")
        )

        import importlib.metadata as _meta
        import json as _json
        import urllib.request as _ur

        class _FakeResp:
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def read(self): return _json.dumps({"info": {"version": "9.9.9"}}).encode()

        orig_version = _meta.version
        orig_urlopen = _ur.urlopen
        _meta.version = lambda _pkg: "0.10.6"
        _ur.urlopen = lambda *a, **k: _FakeResp()
        try:
            sb.maybe_pypi_autoupdate()
        finally:
            _meta.version = orig_version
            _ur.urlopen = orig_urlopen
        self.assertEqual(called, [])


if __name__ == "__main__":
    unittest.main()
