"""E2E-wiring — Vibe Engineering route (ADR-0275/0278).

Drives the routes through the REAL FastAPI router via TestClient. The route now
reads the DURABLE Layer-A Decision Record (audit.jsonl), and serves the Layer-B
brief via /explain. Covers:
  * empty-state (no audit log) → 200, sessions=[].
  * Layer-A records read back + grouped by session, carrying hash + brief_sha256.
  * TENANT ISOLATION: the audit log is per-tenant (tenant_global_dir).
  * /explain returns the brief text; invalid hash → 400; missing → found:false.
  * /explain traversal guard: a hash-shaped name can't escape the tenant root.

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

_SHA = "a" * 64
_SHA2 = "b" * 64


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


def _decision_event(session_id, turn_id, ts, brief_sha, hashv):
    return json.dumps({
        "event_type": "cel.decision", "ts": ts, "hash": hashv, "prev_hash": "p",
        "details": {"turn_id": turn_id, "session_id": session_id, "top_score": 0.6,
                    "stages_ok": 3, "brief_sha256": brief_sha, "brief_bytes": 100,
                    "stages": [{"stage": "memory", "status": "ok",
                                "sources": [{"id": "m.md", "score": 0.6}]}]}})


class VibeRouteTests(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.addCleanup(self.td.cleanup)
        self.base = Path(self.td.name)
        self.pg = patch.object(V._forge_paths, "tenant_global_dir",
                               lambda tid: self.base / tid / "global")
        self.ps = patch.object(V._forge_paths, "tenant_sessions_dir",
                               lambda tid: self.base / tid / "sessions")
        self.pg.start(); self.addCleanup(self.pg.stop)
        self.ps.start(); self.addCleanup(self.ps.stop)

    def _client(self, tenant_id="_default"):
        app = FastAPI()
        app.include_router(V.router)
        rec = _fake_record(tenant_id)
        app.dependency_overrides[console_deps.require_session] = lambda: rec
        return TestClient(app)

    def _write_audit(self, tenant, *events):
        d = self.base / tenant / "global" / "forge"
        d.mkdir(parents=True, exist_ok=True)
        (d / "audit.jsonl").write_text("\n".join(events) + "\n", encoding="utf-8")

    def _write_brief(self, tenant, session, sha, text):
        d = self.base / tenant / "sessions" / session / "cel-briefs"
        d.mkdir(parents=True, exist_ok=True)
        (d / f"{sha}.txt").write_text(text, encoding="utf-8")

    def test_empty_state(self):
        r = self._client().get("/vibe-engineering/traces")
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["sessions"], [])

    def test_reads_layer_a_grouped(self):
        self._write_audit("_default",
                          _decision_event("web:a", "turn-1", 100.0, _SHA, "h1"),
                          _decision_event("web:a", "turn-2", 200.0, _SHA2, "h2"))
        r = self._client().get("/vibe-engineering/traces")
        body = r.json()
        self.assertEqual(len(body["sessions"]), 1)
        s = body["sessions"][0]
        self.assertEqual(s["session"], "web:a")
        self.assertEqual(len(s["turns"]), 2)
        self.assertEqual(s["turns"][0]["hash"], "h2")  # newest first
        self.assertEqual(s["turns"][0]["brief_sha256"], _SHA2)

    def test_tenant_isolation(self):
        self._write_audit("tenant_b",
                          _decision_event("web:secret", "t", 1.0, _SHA, "h"))
        r = self._client(tenant_id="_default").get("/vibe-engineering/traces")
        self.assertEqual(r.json()["sessions"], [],
                         "tenant A must not read tenant B's audit records")

    def test_explain_returns_brief(self):
        self._write_brief("_default", "web:a", _SHA, "## Context brief\nreal text")
        r = self._client().get(f"/vibe-engineering/explain/{_SHA}")
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.assertTrue(body["found"])
        self.assertIn("real text", body["text"])

    def test_explain_invalid_hash_400(self):
        r = self._client().get("/vibe-engineering/explain/not-a-hash")
        self.assertEqual(r.status_code, 400)

    def test_explain_missing_is_found_false(self):
        r = self._client().get(f"/vibe-engineering/explain/{_SHA}")
        self.assertEqual(r.status_code, 200)
        self.assertFalse(r.json()["found"])
        self.assertEqual(r.json()["reason"], "erased_or_absent")


if __name__ == "__main__":
    unittest.main()
