#!/usr/bin/env python3
"""One channel list, and every surface that enumerates channels reads it.

Why this test exists
--------------------
The list of shipped messenger channels was copy-pasted into six places and had
drifted in three of them, always in the same direction — ``signal`` and ``teams``
ship complete daemons but were missing:

* ``session_reset.VALID_CHANNELS`` (five) made ``/new`` and ``/reset`` die with
  ``argparse: invalid choice: 'signal'``. The user saw "session reset failed"
  and the session was never reset. Reproduced 2026-07-28, fixed the same day.
* ``settings_view._BRIDGE_CHANNELS`` (five) hid Signal/Teams from ``/settings``.
* ``bridges_migrate._CHANNELS`` (five) skipped their legacy-state migration.

``bridge_manager._CHANNELS`` already carried the right seven — plus a comment
recording an EARLIER incarnation of the same bug ("the console saved their
settings and then NOTHING could ever start the daemons"). Two independent
occurrences of one omission is what makes this a test rather than a fix.

Run: python3 operator/bridges/shared/test_channel_list_ssot.py
"""
from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))
os.environ.setdefault("CORVIN_ADAPTER_SANDBOX", "0")

from channels import BRIDGE_CHANNELS, CHANNEL_LABELS  # noqa: E402


class ChannelListSSOT(unittest.TestCase):
    def test_every_channel_has_a_shipped_daemon(self):
        """The list is not aspirational — each entry has a daemon on disk."""
        bridges_dir = _HERE.parent
        for ch in BRIDGE_CHANNELS:
            self.assertTrue(
                (bridges_dir / ch / "daemon.js").is_file(),
                f"{ch} is in BRIDGE_CHANNELS but has no {ch}/daemon.js",
            )

    def test_every_shipped_daemon_is_in_the_list(self):
        """The other direction — a new daemon directory must be declared."""
        bridges_dir = _HERE.parent
        on_disk = {
            p.parent.name for p in bridges_dir.glob("*/daemon.js")
        }
        self.assertEqual(
            on_disk, set(BRIDGE_CHANNELS),
            "a daemon.js exists for a channel that BRIDGE_CHANNELS does not "
            "list (or vice versa) — that is exactly how signal/teams stayed "
            "half-wired for months",
        )

    def test_every_channel_has_a_label(self):
        self.assertEqual(set(CHANNEL_LABELS), set(BRIDGE_CHANNELS))

    def test_session_reset_accepts_every_channel(self):
        """`/new` must work on all of them — this is the bug that started it."""
        import session_reset
        self.assertEqual(tuple(session_reset.VALID_CHANNELS), BRIDGE_CHANNELS)

    def test_settings_view_reports_every_channel(self):
        import settings_view
        self.assertEqual(tuple(settings_view._BRIDGE_CHANNELS), BRIDGE_CHANNELS)

    def test_bridges_migrate_covers_every_channel(self):
        import bridges_migrate
        self.assertEqual(tuple(bridges_migrate._CHANNELS), BRIDGE_CHANNELS)

    def test_bridge_manager_agrees(self):
        sys.path.insert(0, str(_HERE.parent))
        import bridge_manager
        self.assertEqual(
            sorted(bridge_manager._CHANNELS), sorted(BRIDGE_CHANNELS),
            "bridge_manager is the process launcher — a channel it does not "
            "know cannot be started at all",
        )

    def test_supervisor_plugin_declarations_agree(self):
        """`corvin_plugins.bridges` keeps its own copy on purpose (different
        distribution package, must import without `operator/` on the path).
        Pin the two together so the copy cannot drift."""
        repo = _HERE.parents[2]
        sys.path.insert(0, str(repo / "core" / "plugins"))
        try:
            from corvin_plugins.bridges.supervisor import BRIDGE_CHANNELS as SUP
        except Exception as exc:  # pragma: no cover — plugin pkg absent
            self.skipTest(f"corvin_plugins not importable here: {exc}")
        self.assertEqual(
            sorted(SUP), sorted(BRIDGE_CHANNELS),
            "corvin_plugins.bridges.supervisor.BRIDGE_CHANNELS drifted from "
            "operator/bridges/shared/channels.py",
        )

    def test_installer_offers_every_channel(self):
        """`corvin-install` listed five, so a fresh install could never select
        Signal or Teams — and its Windows uninstall sweep listed five too, so
        their Scheduled Tasks kept auto-launching the bridge after uninstall."""
        repo = _HERE.parents[2]
        sys.path.insert(0, str(repo))
        try:
            from corvinOS.installer.core import CorvinInstaller
        except Exception as exc:  # pragma: no cover — installer pkg absent
            self.skipTest(f"installer not importable here: {exc}")
        self.assertEqual(sorted(CorvinInstaller.BRIDGES), sorted(BRIDGE_CHANNELS))

    def test_no_stale_private_copy_survives(self):
        """The four `paths.py` files each held a dead, stale `_BRIDGE_CHANNELS`
        frozenset — assigned, never read, missing signal/teams, and therefore
        read by humans as the canonical list. They must not come back."""
        repo = _HERE.parents[2]
        for rel in (
            "operator/bridges/shared/paths.py",
            "operator/cowork/lib/paths.py",
            "operator/forge/forge/paths.py",
            "corvinOS/shared/paths.py",
        ):
            text = (repo / rel).read_text(encoding="utf-8")
            self.assertNotIn(
                "_BRIDGE_CHANNELS = frozenset", text,
                f"{rel} re-introduced a private channel allow-list; channel "
                f"identity there is a charset rule, and the canonical list "
                f"lives in operator/bridges/shared/channels.py",
            )


if __name__ == "__main__":
    unittest.main(verbosity=2)
