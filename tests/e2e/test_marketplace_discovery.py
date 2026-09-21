"""Every indexed marketplace plugin is discoverable (ADR-0892, 2026-09-21).

Replaces ``test_marketplace_phase5_1_staged_rollout.py``, which asserted the
opposite: that contributor-tier plugins were HIDDEN unless the
``marketplace_rollout_pct`` flag was on and the tenant hashed into its canary
bucket. The flag ships OFF, so that "passing" suite was pinning the defect an
operator reports as "the contributor plugins are missing from the marketplace" —
30 builtin plugins listed, all five community ones silently dropped.

The gate is gone. Discovery is a read-only listing of a static index; INSTALL is
the path that carries risk, and it keeps every check it had. These tests assert
the new invariant and would fail if the gate came back.

Runs against the live console over real HTTP. Skips (never fails) when the host
is not up, so it is safe in a pipeline without one. Runnable with pytest or
``python3 -m unittest tests.e2e.test_marketplace_discovery``.
"""

from __future__ import annotations

import json
import unittest
import urllib.error
import urllib.request
from http.cookiejar import CookieJar
from typing import Any, Optional

BASE = "http://127.0.0.1:8765"


def _opener() -> Optional[Any]:
    """An authenticated opener via localhost auto-login, or None if unreachable."""
    opener = urllib.request.build_opener(
        urllib.request.HTTPCookieProcessor(CookieJar())
    )
    # Generous timeout on purpose: a console that has just restarted takes
    # several seconds on its first request, and a skip-on-timeout turns this
    # whole file into a test that never runs and always reports OK.
    try:
        opener.open(f"{BASE}/v1/console/auth/local-login", timeout=30).read()
    except (urllib.error.URLError, OSError):
        return None
    return opener


def _get(opener: Any, path: str) -> dict:
    with opener.open(f"{BASE}{path}", timeout=15) as resp:
        return json.loads(resp.read().decode())


class MarketplaceDiscoveryTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.opener = _opener()
        if cls.opener is None:
            raise unittest.SkipTest(f"console not reachable at {BASE}")

    def _index(self, query: str = "?limit=1000") -> dict:
        return _get(self.opener, f"/v1/console/api/v1/marketplace/plugins{query}")

    def test_contributor_plugins_are_listed(self) -> None:
        """The regression this file exists for: community plugins must appear."""
        plugins = self._index()["plugins"]
        contributor = [p for p in plugins if p.get("tier") == "contributor"]
        self.assertGreater(
            len(contributor), 0,
            "no contributor-tier plugins in the marketplace index — the discovery "
            "gate is back, or the index lost its community entries",
        )

    def test_every_indexed_plugin_is_returned(self) -> None:
        """No tier is filtered out of an unfiltered listing.

        The index file is the source of truth; a listing that returns fewer
        entries than it holds is hiding some, which is exactly the old defect.
        """
        from core.console.corvin_console.routes.marketplace import resolve_index_path

        index_path = resolve_index_path()
        if not index_path.is_file():
            self.skipTest(f"no local index at {index_path}")
        on_disk = json.loads(index_path.read_text())["plugins"]

        served = self._index()
        self.assertEqual(
            served["count"], len(on_disk),
            f"index holds {len(on_disk)} plugins, API returned {served['count']}",
        )
        self.assertEqual(
            {p["id"] for p in served["plugins"]}, {p["id"] for p in on_disk},
        )

    def test_tier_filter_still_selects(self) -> None:
        """Removing the gate must not have removed the explicit filter."""
        only = self._index("?tier=contributor&limit=1000")["plugins"]
        self.assertGreater(len(only), 0)
        self.assertTrue(all(p.get("tier") == "contributor" for p in only))

    def test_listed_plugins_carry_resolved_local_state(self) -> None:
        """Each row says whether it can actually be installed, and why not.

        Discovery is allowed to list everything precisely BECAUSE install
        eligibility travels with the row — hiding a row was never the control.
        """
        for p in self._index()["plugins"]:
            self.assertIn("installable", p, f"{p['id']} has no install eligibility")
            self.assertIn("installed", p)
            if not p["installable"]:
                self.assertTrue(
                    p.get("install_blocker"),
                    f"{p['id']} is not installable but names no blocker",
                )


if __name__ == "__main__":
    unittest.main()
