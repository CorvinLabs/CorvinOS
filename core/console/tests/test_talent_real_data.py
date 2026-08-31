"""Your Talent — real cel.decision aggregation, no mock/random (ADR-0275).

Drives the routes through the real router. Verifies figures come from the
cel.decision audit records, an honest empty-state at zero, and tenant isolation.

Run: python3 core/console/tests/test_talent_real_data.py
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
from corvin_console.routes import talent as T  # noqa: E402


def _fake_record(tenant_id="_default") -> session_auth.SessionRecord:
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


def _ev(ts, top, sources):
    return json.dumps({
        "event_type": "cel.decision", "ts": ts,
        "details": {"top_score": top, "degraded": None, "stages": [
            {"stage": "memory", "status": "ok",
             "sources": [{"id": s, "score": top} for s in sources]},
            {"stage": "graph", "status": "ok", "sources": []}]}})


class TalentRealDataTests(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.addCleanup(self.td.cleanup)
        self.base = Path(self.td.name)
        self.p = patch.object(T._forge_paths, "tenant_global_dir",
                              lambda tid: self.base / tid / "global")
        self.p.start(); self.addCleanup(self.p.stop)

    def _client(self, tenant="_default"):
        app = FastAPI()
        app.include_router(T.router)
        app.dependency_overrides[console_deps.require_session] = lambda: _fake_record(tenant)
        return TestClient(app)

    def _audit(self, tenant, *events):
        d = self.base / tenant / "global" / "forge"
        d.mkdir(parents=True, exist_ok=True)
        (d / "audit.jsonl").write_text("\n".join(events) + "\n", encoding="utf-8")

    def test_empty_state(self):
        body = self._client().get("/talent/score").json()
        self.assertTrue(body["empty"])
        self.assertEqual(body["talent_score"], 0.0)
        self.assertEqual(body["ranking"], [])

    def test_real_score_and_ranking(self):
        self._audit("_default",
                    _ev(100.0, 0.8, ["ADR-0155", "mem-a"]),
                    _ev(200.0, 0.6, ["ADR-0155"]))
        body = self._client().get("/talent/score").json()
        self.assertFalse(body["empty"])
        self.assertAlmostEqual(body["talent_score"], 7.0, places=1)  # avg(0.8,0.6)*10
        self.assertEqual(body["ranking"][0]["id"], "ADR-0155")  # most used
        self.assertEqual(body["ranking"][0]["medal"], "🥇")
        self.assertEqual(body["components"]["efficiency"], 1.0)  # none degraded

    def test_tenant_isolation(self):
        self._audit("tenant_b", _ev(100.0, 0.9, ["secret"]))
        body = self._client(tenant="_default").get("/talent/score").json()
        self.assertTrue(body["empty"], "tenant A sees none of tenant B's records")

    def test_task_types_from_stages(self):
        self._audit("_default", _ev(100.0, 0.7, ["m1", "m2"]))
        body = self._client().get("/talent/task-types").json()
        self.assertFalse(body["empty"])
        types = {t["type"] for t in body["task_types"]}
        self.assertIn("Memory Lookup", types)


if __name__ == "__main__":
    unittest.main()
