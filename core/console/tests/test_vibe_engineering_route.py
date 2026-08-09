"""E2E-wiring — Vibe Engineering P1 route (ADR-0275).

Drives GET /vibe-engineering/traces through the REAL FastAPI router via
TestClient (not a direct call to the handler) — the e2e-wiring-proof for a new
endpoint. Covers:
  * empty-state (no trace file yet) → 200, sessions=[], available=True.
  * a persisted trace is read back through the HTTP boundary.
  * TENANT ISOLATION: a trace under tenant B's sessions dir is invisible to a
    session authenticated as tenant A (the route roots at tenant_sessions_dir).
  * limit bounds → 400.

Run: python3 core/console/tests/test_vibe_engineering_route.py
"""
from __future__ import annotations

import dataclasses
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

_REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_REPO / "core" / "console"))
sys.path.insert(0, str(_REPO / "operator" / "forge"))

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from corvin_console import auth as session_auth  # noqa: E402
from corvin_console import deps as console_deps  # noqa: E402
from corvin_console.routes import vibe_engineering as V  # noqa: E402


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


def _write_trace(sessions_root: Path, session_name: str, trace: dict, ts: float):
    wd = sessions_root / session_name
    wd.mkdir(parents=True, exist_ok=True)
    rec = {"v": 1, "turn_id": "turn-1", "ts": ts, "trace": trace}
    (wd / ".corvin-cel-traces.jsonl").write_text(
        json.dumps(rec) + "\n", encoding="utf-8")


class VibeRouteTests(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.addCleanup(self.td.cleanup)
        self.base = Path(self.td.name)

        # tenant_sessions_dir(tid) -> <tmp>/<tid>/sessions  (real tenant isolation)
        self.pp = patch.object(
            V._forge_paths, "tenant_sessions_dir",
            lambda tid: self.base / tid / "sessions")
        self.pp.start(); self.addCleanup(self.pp.stop)

    def _client(self, tenant_id="_default"):
        app = FastAPI()
        app.include_router(V.router)
        rec = _fake_record(tenant_id)
        app.dependency_overrides[console_deps.require_session] = lambda: rec
        return TestClient(app)

    def test_empty_state(self):
        r = self._client().get("/vibe-engineering/traces")
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.assertEqual(body["sessions"], [])
        self.assertTrue(body["available"])

    def test_reads_persisted_trace_over_http(self):
        root = self.base / "_default" / "sessions"
        _write_trace(root, "web:abc", {
            "task_preview": "erklär postgres indexes",
            "stages": [{"stage": "memory", "status": "ok",
                        "confidence_tier": "high", "sources": ["m1"]}],
        }, ts=100.0)
        r = self._client().get("/vibe-engineering/traces")
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.assertEqual(len(body["sessions"]), 1)
        s = body["sessions"][0]
        self.assertEqual(s["session"], "web:abc")
        self.assertEqual(s["traces"][0]["trace"]["stages"][0]["stage"], "memory")

    def test_tenant_isolation(self):
        # tenant B has a trace; a session authed as tenant A must NOT see it.
        b_root = self.base / "tenant_b" / "sessions"
        _write_trace(b_root, "web:secret", {"stages": [], "task_preview": "B"}, ts=1.0)
        r = self._client(tenant_id="_default").get("/vibe-engineering/traces")
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["sessions"], [],
                         "tenant A must not read tenant B's traces")

    def test_limit_bounds(self):
        self.assertEqual(self._client().get(
            "/vibe-engineering/traces?limit=0").status_code, 400)
        self.assertEqual(self._client().get(
            "/vibe-engineering/traces?limit=201").status_code, 400)


if __name__ == "__main__":
    unittest.main()
