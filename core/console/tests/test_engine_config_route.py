"""E2E-wiring — GET/PUT /settings/engine engine_models (routes/engine.py).

ADR-0119/0181: 243690e8 ("rewrite engine.py for Claude Code only") dropped the
whole per-engine provider/model persistence path (`engine_models`) along with
the multi-engine machinery it was bundled with. `resolve_claude_code_provider_env`
(operator/bridges/shared/engine_models.py) — the function the adapter's spawn
path calls on every turn — still reads `spec.engine_models.<engine_id>.provider`
from tenant.corvin.yaml; nothing wrote that shape anymore. Restored 2026-09-10
alongside the /registry, /providers, /models, /detect routes, additively (no
Hermes/OpenCode/Codex multi-engine `default_engine` support reintroduced).

Pins the real FastAPI router (only session-auth/CSRF overridden), through a
real tenant.corvin.yaml under a temp CORVIN_HOME — the actual persistence
boundary `resolve_claude_code_provider_env` reads from.
"""
from __future__ import annotations

import dataclasses
import os
import sys
import tempfile
import unittest
from pathlib import Path

_REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_REPO / "core" / "console"))
sys.path.insert(0, str(_REPO / "operator" / "bridges" / "shared"))

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from corvin_console import auth as session_auth  # noqa: E402
from corvin_console import deps as console_deps  # noqa: E402
from corvin_console.routes import engine as ENG  # noqa: E402


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


class EngineConfigRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self._prev_home = os.environ.get("CORVIN_HOME")
        os.environ["CORVIN_HOME"] = self._tmp.name

    def tearDown(self) -> None:
        if self._prev_home is None:
            os.environ.pop("CORVIN_HOME", None)
        else:
            os.environ["CORVIN_HOME"] = self._prev_home
        self._tmp.cleanup()

    def _client(self) -> TestClient:
        app = FastAPI()
        app.include_router(ENG.router)
        app.dependency_overrides[console_deps.require_session] = lambda: _fake_record()
        app.dependency_overrides[console_deps.require_csrf] = lambda: None
        return TestClient(app)

    def test_get_default_has_empty_engine_models(self) -> None:
        r = self._client().get("/settings/engine")
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.assertEqual(body["engine_models"], {})
        self.assertEqual(body["compliance_warnings"], [])

    def test_put_persists_provider_and_model_roundtrip(self) -> None:
        client = self._client()
        r = client.put(
            "/settings/engine",
            json={
                "default_engine": "claude_code",
                "engine_models": {
                    "claude_code": {"os_model": "claude-opus-5", "provider": "anthropic"},
                },
            },
        )
        self.assertEqual(r.status_code, 200, r.text)
        saved = r.json()["engine_models"]["claude_code"]
        self.assertEqual(saved["os_model"], "claude-opus-5")
        self.assertEqual(saved["provider"], "anthropic")

        # Re-GET must reflect what was just persisted — the actual boundary
        # resolve_claude_code_provider_env() reads at spawn time, not just the
        # in-memory PUT response.
        r2 = client.get("/settings/engine")
        self.assertEqual(r2.json()["engine_models"]["claude_code"], saved)

    def test_put_rejects_unknown_provider(self) -> None:
        r = self._client().put(
            "/settings/engine",
            json={
                "default_engine": "claude_code",
                "engine_models": {
                    "claude_code": {"os_model": "x", "provider": "not_a_real_provider"},
                },
            },
        )
        self.assertEqual(r.status_code, 422, r.text)

    def test_put_cloud_provider_raises_compliance_advisory(self) -> None:
        r = self._client().put(
            "/settings/engine",
            json={
                "default_engine": "claude_code",
                "engine_models": {
                    "claude_code": {"os_model": "some-model", "provider": "openrouter"},
                },
            },
        )
        self.assertEqual(r.status_code, 200, r.text)
        warnings = r.json()["compliance_warnings"]
        self.assertTrue(warnings, "expected an advisory for a cloud provider assignment")
        self.assertIn("openrouter", warnings[0].lower() + str(r.json()))
        self.assertIn("OPENROUTER_API_KEY", warnings[0])

    def test_put_local_provider_raises_no_advisory(self) -> None:
        r = self._client().put(
            "/settings/engine",
            json={
                "default_engine": "claude_code",
                "engine_models": {
                    "claude_code": {"os_model": "qwen3:8b", "provider": "ollama_local"},
                },
            },
        )
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["compliance_warnings"], [])

    def test_put_extra_field_still_rejected(self) -> None:
        """The Claude-Code-only simplification (243690e8) is NOT reverted —
        `default_worker_engine`/`hermes_model` stay unsupported; only
        `engine_models` was added back."""
        r = self._client().put(
            "/settings/engine",
            json={"default_engine": "claude_code", "hermes_model": "hermes-fast"},
        )
        self.assertEqual(r.status_code, 422, r.text)


if __name__ == "__main__":
    unittest.main()
