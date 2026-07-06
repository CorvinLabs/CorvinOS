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


if __name__ == "__main__":
    unittest.main()
