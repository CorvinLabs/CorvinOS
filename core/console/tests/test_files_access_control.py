#!/usr/bin/env python3
"""Regression: the console file API's _access() must let NO_ACCESS win over
READ_ONLY regardless of component order.

round-2 HIGH: the hash-chained GDPR audit log lives at global/forge/audit.jsonl.
The old first-match loop hit "forge" (READ_ONLY → "read") and never reached
"audit.jsonl" (NO_ACCESS → "none"), so GET /files/download?path=global/forge/
audit.jsonl streamed the audit chain, and any secrets.json/.env/vault under a
forge/ or skill-forge/ subtree was downloadable by any authenticated session.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

_REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_REPO / "core" / "console"))

from corvin_console.routes.files import _access, _NO_ACCESS  # type: ignore


class FilesAccessControlTests(unittest.TestCase):
    def test_no_access_wins_over_read_only_forge(self):
        # audit chain + secrets under a READ_ONLY forge/ subtree → none
        self.assertEqual(_access("global/forge/audit.jsonl"), "none")
        self.assertEqual(_access("global/forge/secrets.json"), "none")
        self.assertEqual(_access("global/forge/.env"), "none")
        self.assertEqual(_access("sessions/x/skill-forge/vault"), "none")

    def test_every_no_access_component_blocked_under_forge(self):
        for name in _NO_ACCESS:
            self.assertEqual(
                _access(f"global/forge/{name}"), "none",
                f"NO_ACCESS component {name!r} leaked as readable under forge/",
            )

    def test_legit_forge_tool_still_read_only(self):
        self.assertEqual(_access("sessions/x/forge/tools/mytool.py"), "read")
        self.assertEqual(_access("global/skill-forge/skills/s/SKILL.md"), "read")

    def test_ordinary_path_full(self):
        self.assertEqual(_access("outputs/report.md"), "full")
        self.assertEqual(_access(""), "full")


if __name__ == "__main__":
    unittest.main()
