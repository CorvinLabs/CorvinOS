"""Guard: this suite must never write into the live hash-chained audit trail.

This is not a test of the plugin system. It is a test of the test setup, and it
exists because the failure it guards against has happened four times in this
repo and is *unrepairable* when it does: the audit chain is append-only and
hash-linked, so a test record written into it cannot be removed afterwards
without breaking the chain for every real record that follows.

The plugin suite is unusually exposed to this because it exercises boot paths —
`bootstrap_*` builds a real `PluginContext` whose `audit_emit` resolves the real
writer. One run left 28 permanent `plugin.loaded` records in the live chain.

If `conftest.py` next to this file is removed or its fixture stops being
autouse, this test fails.
"""
from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve()
_REPO = _HERE.parents[3]
for _p in (str(_HERE.parents[1]), str(_REPO)):
    if _p not in sys.path:
        sys.path.insert(0, _p)


class TestAuditChainIsolation(unittest.TestCase):
    def test_audit_path_env_is_redirected(self):
        target = os.environ.get("VOICE_AUDIT_PATH")
        self.assertIsNotNone(
            target,
            "VOICE_AUDIT_PATH is unset — the autouse fixture in "
            "core/plugins/tests/conftest.py is not running, and any test that "
            "touches a bootstrap path will append to the live audit chain",
        )
        self.assertNotIn(
            ".corvin", target or "",
            f"VOICE_AUDIT_PATH points at {target!r}, which looks like the live "
            f"corvin_home rather than a tmp dir",
        )

    def test_the_writer_actually_resolves_to_the_redirected_path(self):
        # The env var only helps if audit_path() reads it per call rather than
        # caching it at import. That is the part worth pinning: a refactor to a
        # module-level constant would silently reinstate the live chain.
        try:
            import audit  # type: ignore[import-not-found]
        except ImportError:
            self.skipTest("audit module not importable in this layout")
        resolved = str(audit.audit_path())
        self.assertEqual(resolved, os.environ["VOICE_AUDIT_PATH"])


if __name__ == "__main__":
    unittest.main()
