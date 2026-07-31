"""_apply_runtime_change's Windows fallback (2026-07-31).

Root cause: on first-time channel activation (no systemd unit installed
yet), _apply_runtime_change fell through to _run_bridge_sh_up(), which runs
`bash bridge.sh up` -- a bash script. Native Windows has no systemd (so
_unit_installed() is always False there) AND typically no `bash` on PATH,
so _run_bridge_sh_up() always returned {"applied": False, "reason": "bash
not on PATH"} on Windows. A freshly-saved Discord/Telegram token on Windows
therefore had settings.json written but the daemon was never materialised
(npm install into the runtime dir) or started -- live-reported as "Discord
bridge files completely missing" (daemon.js/package.json were vendored in
the wheel the whole time; only the writable runtime dir was never
populated). Fixed by routing to bridge_manager.start_channel_detached() on
win32 -- the same cross-platform materialise+spawn path the console's
"Start WhatsApp bridge" button already relies on.

Run: python3 -m pytest core/console/tests/test_apply_runtime_change_windows.py
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

_REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_REPO / "core" / "console"))

from corvin_console.routes import bridges as br  # noqa: E402


class ApplyRuntimeChangeWindowsFallbackTests(unittest.TestCase):
    def setUp(self) -> None:
        self.sup = patch.object(br, "_supervisor_toggle", return_value={"applied": False})
        self.sup.start(); self.addCleanup(self.sup.stop)
        self.unit = patch.object(br, "_unit_installed", return_value=False)
        self.unit.start(); self.addCleanup(self.unit.stop)

    def test_win32_first_activation_uses_start_channel_detached_not_bash(self):
        """The exact regression: no systemd unit + win32 must go through
        bridge_manager.start_channel_detached, never _run_bridge_sh_up
        (which shells out to bash -- absent on a typical Windows box)."""
        fake_bm = MagicMock()
        fake_bm.start_channel_detached.return_value = {"ok": True, "pid": 4242}

        with patch.object(sys, "platform", "win32"), \
             patch.object(br, "_import_bridge_manager", return_value=fake_bm), \
             patch.object(br, "_run_bridge_sh_up") as mock_bash_path:
            result = br._apply_runtime_change("discord", enabled=None)

        mock_bash_path.assert_not_called()
        fake_bm.start_channel_detached.assert_called_once_with("discord")
        self.assertTrue(result["applied"])
        self.assertEqual(result["via"], "start_channel_detached")
        self.assertEqual(result["pid"], 4242)

    def test_win32_start_failure_is_reported_not_applied(self):
        fake_bm = MagicMock()
        fake_bm.start_channel_detached.return_value = {
            "ok": False, "error": "npm install failed", "reason": "npm_failed",
        }
        with patch.object(sys, "platform", "win32"), \
             patch.object(br, "_import_bridge_manager", return_value=fake_bm):
            result = br._apply_runtime_change("discord", enabled=None)

        self.assertFalse(result["applied"])
        self.assertEqual(result["reason"], "npm_failed")

    def test_win32_without_bridge_manager_fails_cleanly(self):
        with patch.object(sys, "platform", "win32"), \
             patch.object(br, "_import_bridge_manager", return_value=None):
            result = br._apply_runtime_change("discord", enabled=None)

        self.assertFalse(result["applied"])
        self.assertIn("bridge_manager", result["reason"])

    def test_non_windows_first_activation_still_uses_bridge_sh(self):
        """Linux/macOS/WSL2 behaviour must be completely unchanged."""
        with patch.object(sys, "platform", "linux"), \
             patch.object(br, "_run_bridge_sh_up",
                           return_value={"applied": True, "via": "bridge.sh up"}) as mock_bash_path:
            result = br._apply_runtime_change("discord", enabled=None)

        mock_bash_path.assert_called_once()
        self.assertTrue(result["applied"])
        self.assertEqual(result["via"], "bridge.sh up")


if __name__ == "__main__":
    unittest.main()
