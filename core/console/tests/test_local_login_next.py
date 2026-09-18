"""ADR-0885 — local-login returns the operator to the console deep link it was
going to (``next``), validated as a same-origin /console/ path; anything else
falls back to /console/ and the login still succeeds (never an open redirect).
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

_REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_REPO / "core" / "console"))
sys.path.insert(0, str(_REPO / "corvin_operator" / "bridges" / "shared"))
sys.path.insert(0, str(_REPO))

from corvin_console.routes import auth_routes as AR  # noqa: E402


class SafeNextTests(unittest.TestCase):
    def test_accepts_console_deep_links(self) -> None:
        self.assertEqual(AR._safe_next("/console/app/models?tab=routing"), "/console/app/models?tab=routing")
        self.assertEqual(AR._safe_next("/console/app/engine-config"), "/console/app/engine-config")

    def test_rejects_everything_else(self) -> None:
        for bad in ("https://evil.example/", "//evil.example/x", "/app/models", "/console", "",
                    None, "/console/\\evil", "/console/x\r\nSet-Cookie: a=b", "/console/" + "a" * 600):
            self.assertIsNone(AR._safe_next(bad), repr(bad))


if __name__ == "__main__":
    unittest.main()
