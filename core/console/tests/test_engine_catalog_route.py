"""E2E-wiring — GET /settings/engine/catalog (routes/engine.py).

Commit 243690e8 ("rewrite engine.py for Claude Code only") deleted the whole
model-catalog auto-refresh mechanism and left `_CLAUDE_MODELS` as a static,
hand-curated list — which then went stale (still offering the superseded
claude-opus-4-1 / claude-sonnet-4-20250514 / claude-haiku-4-5-20251001 trio
after the Claude 5 family shipped). This suite pins the route through the
REAL FastAPI router (only the session-auth dependency is overridden) so the
picker's actual HTTP response is checked, not just the module-level constant:

  * the catalog offers the current model family (opus-5 / sonnet-5 / haiku-4.5)
    and none of the superseded ids,
  * exactly one entry is marked default,
  * the response shape stays {id, label, default} per model plus the
    claude_code engine entry — the shape the console frontend depends on.

The route does no network egress (`_CLAUDE_MODELS` is a static list; engine.py
imports no http client) — there is nothing to gate or mock here.
"""
from __future__ import annotations

import dataclasses
import sys
import unittest
from pathlib import Path

_REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_REPO / "core" / "console"))

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from corvin_console import auth as session_auth  # noqa: E402
from corvin_console import deps as console_deps  # noqa: E402
from corvin_console.routes import engine as ENG  # noqa: E402

_SUPERSEDED_IDS = {
    "claude-opus-4-1",
    "claude-sonnet-4-20250514",
}
_CURRENT_IDS = {
    "claude-opus-5",
    "claude-sonnet-5",
    "claude-haiku-4-5-20251001",
}


def _fake_record(tenant_id: str = "_default") -> session_auth.SessionRecord:
    now = 1_000_000.0
    values: dict[str, object] = {}
    for f in dataclasses.fields(session_auth.SessionRecord):
        if f.default is not dataclasses.MISSING:
            continue
        ann = str(f.type)
        if "float" in ann:
            values[f.name] = now + (3600 if f.name == "expires_at" else 0)
        elif "bool" in ann:
            values[f.name] = False
        elif f.name == "tier":
            tier = getattr(session_auth, "Tier", None)
            values[f.name] = next(iter(tier)) if tier else "owner"
        elif f.name == "tenant_id":
            values[f.name] = tenant_id
        else:
            values[f.name] = f"test-{f.name}"
    return session_auth.SessionRecord(**values)  # type: ignore[arg-type]


class EngineCatalogRouteTests(unittest.TestCase):
    def _client(self) -> TestClient:
        app = FastAPI()
        app.include_router(ENG.router)
        app.dependency_overrides[console_deps.require_session] = lambda: _fake_record()
        return TestClient(app)

    def test_catalog_offers_current_models_only(self) -> None:
        r = self._client().get("/settings/engine/catalog")
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        model_ids = {m["id"] for m in body["models"]}

        self.assertTrue(
            _CURRENT_IDS.issubset(model_ids),
            f"catalog missing current models: {_CURRENT_IDS - model_ids}",
        )
        self.assertFalse(
            model_ids & _SUPERSEDED_IDS,
            f"catalog still offers superseded models: {model_ids & _SUPERSEDED_IDS}",
        )

    def test_exactly_one_default_model(self) -> None:
        r = self._client().get("/settings/engine/catalog")
        body = r.json()
        defaults = [m for m in body["models"] if m["default"]]
        self.assertEqual(len(defaults), 1, body["models"])
        self.assertEqual(defaults[0]["id"], "claude-sonnet-5")

    def test_response_shape(self) -> None:
        r = self._client().get("/settings/engine/catalog")
        body = r.json()
        self.assertIn("engines", body)
        self.assertIn("models", body)
        self.assertEqual(body["engines"][0]["id"], "claude_code")
        for m in body["models"]:
            self.assertEqual(set(m.keys()), {"id", "label", "default"})


if __name__ == "__main__":
    unittest.main()
