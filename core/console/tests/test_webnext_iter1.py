"""Iteration-1 smoke tests for the ADR-0037 console re-launch.

Covers the two backend changes that are load-bearing for the new
frontend skeleton (Landing + Dashboard pages):

  1. ``GET /v1/console/landing/personas`` returns the publishable
     projection of every bundle persona — unauthenticated, no
     tenant-specific data leaks.
  2. ``mount_static`` serves the web-next/dist SPA (the legacy
     vanilla-JS ``web/`` frontend and the ``CORVIN_CONSOLE_UI``
     selection switch were removed once web-next became the only UI).

The full Settings / Chat / systemd surface lands in later iterations
and is out of scope here.
"""
from __future__ import annotations

import importlib
import sys
import unittest
from pathlib import Path

_REPO = Path(__file__).resolve().parents[3]
# The console package and its sibling MUST be importable; mirror the
# pattern used by ``test_profile_routes.py``.
for sub in ("core/console", "core/gateway", "operator/forge"):
    p = _REPO / sub
    if p.exists() and str(p) not in sys.path:
        sys.path.insert(0, str(p))


class LandingPersonasTests(unittest.TestCase):
    """Public landing-personas projection — ADR-0037 § Backend integration."""

    def setUp(self) -> None:
        # Always re-import to pick up any fixture-driven path changes.
        from corvin_console.routes import landing as landing_mod  # type: ignore
        importlib.reload(landing_mod)
        self.landing = landing_mod

    def test_returns_count_and_personas(self) -> None:
        payload = self.landing.landing_personas()
        self.assertIn("count", payload)
        self.assertIn("personas", payload)
        self.assertEqual(payload["count"], len(payload["personas"]))
        # The bundle ships 12 personas (ADR-0034 / operator-bundle).
        # We assert "at least one" so the test doesn't drift on every
        # bundle addition — bundle integrity is covered elsewhere.
        self.assertGreaterEqual(payload["count"], 1,
                                msg="bundle personas should not be empty")

    def test_projection_does_not_leak_sensitive_fields(self) -> None:
        payload = self.landing.landing_personas()
        ALLOWED = {
            "name", "description", "tool_namespace",
            "forge_enabled", "skill_forge_enabled", "ldd_preset",
        }
        for p in payload["personas"]:
            self.assertEqual(
                set(p.keys()), ALLOWED,
                msg=f"persona projection leaks fields: {set(p.keys()) - ALLOWED}",
            )
            self.assertIsInstance(p["forge_enabled"], bool)
            self.assertIsInstance(p["skill_forge_enabled"], bool)

    def test_every_persona_has_a_name(self) -> None:
        payload = self.landing.landing_personas()
        for p in payload["personas"]:
            self.assertTrue(p["name"], msg=f"persona without name: {p}")


class MountStaticTests(unittest.TestCase):
    """``mount_static`` serves the web-next SPA; legacy UI removed."""

    def setUp(self) -> None:
        from corvin_console import app as app_mod  # type: ignore
        importlib.reload(app_mod)
        self.app_mod = app_mod

    def test_dist_dir_points_at_web_next(self) -> None:
        d = self.app_mod._NEXT_DIST_DIR
        self.assertEqual(d.name, "dist")
        self.assertEqual(d.parent.name, "web-next")

    def test_legacy_selection_machinery_is_gone(self) -> None:
        # The legacy vanilla-JS frontend and its CORVIN_CONSOLE_UI
        # selection switch were deleted once web-next became the only UI.
        self.assertFalse(hasattr(self.app_mod, "_choose_ui_dir"))
        self.assertFalse(hasattr(self.app_mod, "_LEGACY_WEB_DIR"))

    def test_mount_static_mounts_spa_when_dist_present(self) -> None:
        from fastapi import FastAPI
        app = FastAPI()
        before = len(app.routes)
        self.app_mod.mount_static(app)
        if self.app_mod._NEXT_DIST_DIR.exists():
            self.assertTrue(
                any(getattr(r, "path", "") == "/console" for r in app.routes),
                msg="web-next SPA should be mounted at /console",
            )
        else:
            # No build artifact → mount skipped, REST API unaffected.
            self.assertEqual(len(app.routes), before)


if __name__ == "__main__":  # pragma: no cover
    unittest.main(verbosity=2)
