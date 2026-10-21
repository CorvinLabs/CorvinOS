"""GET /settings/engine/health is a real probe (routes/engine.py, 2026-10-21).

Until 2026-10-21 the route returned a constant ``healthy: true`` — on a fresh
install with the ``claude`` CLI signed out it still said "Claude Code is ready"
while the chat refused every turn with a 503. The route now reports the SAME
subprocess-free probe the chat gates a turn on
(``chat_runtime.get_engine_unavailable_message``), through the REAL FastAPI
router with only the session dependency overridden.
"""
from __future__ import annotations

import dataclasses
import sys
import unittest
from pathlib import Path
from unittest import mock

_REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_REPO / "core" / "console"))

from corvin_console import auth as session_auth  # noqa: E402
from corvin_console import chat_runtime  # noqa: E402
from corvin_console import deps as console_deps  # noqa: E402
from corvin_console.routes import engine as ENG  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402


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


class EngineHealthRouteTests(unittest.TestCase):
    def _client(self) -> TestClient:
        app = FastAPI()
        app.include_router(ENG.router)
        app.dependency_overrides[console_deps.require_session] = lambda: _fake_record()
        return TestClient(app)

    def test_signed_out_engine_is_unhealthy_with_the_chats_own_message(self) -> None:
        msg = "The **Claude Code** engine is selected but not signed in."
        with mock.patch.object(chat_runtime, "get_engine_unavailable_message", return_value=msg) as probe:
            r = self._client().get("/settings/engine/health")
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json(), {"healthy": False, "message": msg})
        probe.assert_called_once_with("_default")

    def test_ready_engine_is_healthy(self) -> None:
        with mock.patch.object(chat_runtime, "get_engine_unavailable_message", return_value=None):
            r = self._client().get("/settings/engine/health")
        self.assertEqual(r.json(), {"healthy": True, "message": "Claude Code is ready"})

    def test_a_probe_error_is_unhealthy_never_a_500(self) -> None:
        with mock.patch.object(chat_runtime, "get_engine_unavailable_message", side_effect=RuntimeError("boom")):
            r = self._client().get("/settings/engine/health")
        self.assertEqual(r.status_code, 200)
        self.assertFalse(r.json()["healthy"])


if __name__ == "__main__":
    unittest.main()
