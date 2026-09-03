"""HTTP E2E proof for the console learning routes (adversarial review L-09/L-10/L-20).

These tests drive the REAL FastAPI router through ``TestClient`` with an
authenticated session (the ``_sandbox`` pattern of test_license_http_gates.py:
CORVIN_HOME → temp dir, real ``auth.create_session`` cookie) and then verify
the side effect on disk — the rating landed as a learning event in the
tenant's ``learning/events/`` store.

Routes proven:
  POST /v1/console/tools/{tool_id}/rating
  GET  /v1/console/tools/{tool_id}/feedback
  POST /v1/console/skills/{skill_id}/rating
  GET  /v1/console/learning/nodes
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path

# ── Path bootstrap (same as test_license_http_gates.py) ───────────────────────
_HERE = Path(__file__).resolve().parent
_REPO = _HERE.parents[2]
_OPERATOR = _REPO / "operator"
_CONSOLE = _REPO / "core" / "console"
for _p in [
    str(_OPERATOR),
    str(_OPERATOR / "license"),
    str(_OPERATOR / "forge"),
    str(_CONSOLE),
]:
    if _p not in sys.path:
        sys.path.insert(0, _p)


def _reset_modules():
    for key in list(sys.modules):
        if any(key.startswith(p) for p in ("corvin_console", "corvin_gateway", "forge")):
            del sys.modules[key]


@contextmanager
def _sandbox(tmp_path: Path):
    """Spin up a sandboxed console app with a live session."""
    home = tmp_path / "corvin_home"
    tenant_id = "_default"
    (home / "tenants" / tenant_id / "global" / "auth").mkdir(parents=True)
    (home / "tenants" / tenant_id / "global" / "forge").mkdir(parents=True)
    (home / "tenants" / tenant_id / "global" / "console" / "sessions").mkdir(parents=True)

    prev = {k: os.environ.get(k) for k in ("CORVIN_HOME", "CORVIN_TENANT_ID")}
    os.environ["CORVIN_HOME"] = str(home)
    os.environ["CORVIN_TENANT_ID"] = tenant_id

    try:
        _reset_modules()
        from corvin_console import auth as _auth
        from corvin_console.app import router
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        rec = _auth.create_session(tenant_id=tenant_id, token_fingerprint="test-fp")
        csrf = _auth.derive_csrf_token(rec.csrf_secret, rec.sid)

        app = FastAPI()
        app.include_router(router, prefix="/v1/console")
        client = TestClient(app, raise_server_exceptions=False)
        client.cookies.set("corvin_console_sid", rec.sid)
        client.headers.update({"X-CSRF-Token": csrf})

        yield client, home, tenant_id
    finally:
        for k, v in prev.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        _reset_modules()


def _events_on_disk(home: Path, tenant_id: str) -> list[dict]:
    events_dir = home / "tenants" / tenant_id / "learning" / "events"
    out: list[dict] = []
    for f in sorted(events_dir.glob("*.jsonl")) if events_dir.is_dir() else []:
        out.extend(json.loads(l) for l in f.read_text().splitlines() if l.strip())
    return out


class TestToolRatingRoute(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp()

    def tearDown(self):
        import shutil
        shutil.rmtree(self._tmp, ignore_errors=True)

    def test_rating_round_trip_persists_learning_event(self):
        with _sandbox(Path(self._tmp)) as (client, home, tid):
            r1 = client.post("/v1/console/tools/tool_x/rating", json={"rating": 5, "feedback_text": "good"})
            self.assertEqual(r1.status_code, 200, r1.text)
            body = r1.json()
            self.assertEqual(body["status"], "success")
            self.assertEqual(body["rating_recorded"], 5)
            self.assertEqual(body["feedback_stats"]["sample_count"], 1)

            r2 = client.post("/v1/console/tools/tool_x/rating", json={"rating": 3})
            self.assertEqual(r2.status_code, 200, r2.text)
            self.assertEqual(r2.json()["feedback_stats"]["sample_count"], 2)
            self.assertAlmostEqual(r2.json()["feedback_stats"]["average_rating"], 4.0)

            g = client.get("/v1/console/tools/tool_x/feedback")
            self.assertEqual(g.status_code, 200, g.text)
            stats = g.json()
            self.assertEqual(stats["entity_id"], "tool_x")
            self.assertEqual(stats["entity_type"], "tool")
            self.assertEqual(stats["sample_count"], 2)
            self.assertEqual(stats["min_rating"], 3)
            self.assertEqual(stats["max_rating"], 5)

            # Side effect on disk: two FEEDBACK events for subject tool:tool_x in
            # <CORVIN_HOME>/tenants/_default/learning/events/ — no events.db dir.
            events = _events_on_disk(home, tid)
            ratings = [e for e in events if e["skill_id"] == "tool:tool_x"]
            self.assertEqual(len(ratings), 2, events)
            self.assertEqual({e["event_type"] for e in ratings}, {"feedback"})
            self.assertEqual(sorted(e["signal"]["rating"] for e in ratings), [3, 5])
            self.assertTrue(all(e["tenant_id"] == tid for e in ratings))
            self.assertFalse((home / "tenants" / tid / "learning" / "events.db").exists())

    def test_invalid_rating_is_400_and_not_persisted(self):
        with _sandbox(Path(self._tmp)) as (client, home, tid):
            r = client.post("/v1/console/tools/tool_x/rating", json={"rating": 9})
            self.assertEqual(r.status_code, 400, r.text)
            self.assertEqual(_events_on_disk(home, tid), [])

    def test_unauthenticated_is_rejected(self):
        with _sandbox(Path(self._tmp)) as (client, home, tid):
            client.cookies.clear()
            r = client.post("/v1/console/tools/tool_x/rating", json={"rating": 5})
            self.assertIn(r.status_code, (401, 403), r.text)
            self.assertEqual(_events_on_disk(home, tid), [])


class TestSkillRatingRoute(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp()

    def tearDown(self):
        import shutil
        shutil.rmtree(self._tmp, ignore_errors=True)

    def test_skill_rating_round_trip(self):
        with _sandbox(Path(self._tmp)) as (client, home, tid):
            r = client.post("/v1/console/skills/skill_y/rating", json={"rating": 4})
            self.assertEqual(r.status_code, 200, r.text)
            self.assertEqual(r.json()["feedback_stats"]["sample_count"], 1)
            g = client.get("/v1/console/skills/skill_y/feedback")
            self.assertEqual(g.status_code, 200, g.text)
            self.assertEqual(g.json()["entity_type"], "skill")
            self.assertEqual(g.json()["sample_count"], 1)
            events = [e for e in _events_on_disk(home, tid) if e["skill_id"] == "skill:skill_y"]
            self.assertEqual(len(events), 1)
            self.assertEqual(events[0]["signal"]["kind"], "operator_rated_skill")


class TestLearningNodesRoute(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp()

    def tearDown(self):
        import shutil
        shutil.rmtree(self._tmp, ignore_errors=True)

    def test_nodes_returns_tree(self):
        with _sandbox(Path(self._tmp)) as (client, home, tid):
            r = client.get("/v1/console/learning/nodes")
            self.assertEqual(r.status_code, 200, r.text)
            body = r.json()
            self.assertIn("nodes", body)
            self.assertIsInstance(body["nodes"], list)
            self.assertIn(body["source"], ("nodes", "earned"))
            # LearningIntegration was constructed for the tenant under CORVIN_HOME
            self.assertTrue((home / "tenants" / tid / "learning").is_dir())

    def test_grade_unknown_pattern_does_not_500_on_constructor(self):
        """The old constructor crashed (AnomalyDetector(store) → TypeError) before
        any handler ran; grading now reaches the handler."""
        with _sandbox(Path(self._tmp)) as (client, home, tid):
            r = client.post("/v1/console/learning/grade",
                            json={"pattern_id": "pattern_nope", "grade": 0.5, "reason": "x"})
            self.assertEqual(r.status_code, 200, r.text)
            self.assertEqual(r.json()["status"], "success")
            self.assertIsNone(r.json()["new_confidence"])


if __name__ == "__main__":
    unittest.main()


class TestLearningDashboardRoutes(unittest.TestCase):
    """N-06: ``/api/learning/*`` (mounted at app.py) uses the REAL session
    dependency — 401 unauthenticated, tenant from the SessionRecord."""

    def setUp(self):
        self._tmp = tempfile.mkdtemp()

    def tearDown(self):
        import shutil
        shutil.rmtree(self._tmp, ignore_errors=True)

    def test_unauthenticated_is_401(self):
        with _sandbox(Path(self._tmp)) as (client, home, tid):
            client.cookies.clear()
            for path in ("/v1/console/api/learning/summary", "/v1/console/api/learning/health",
                         "/v1/console/api/learning/skills/os.router", "/v1/console/api/learning/user/u1"):
                r = client.get(path)
                self.assertEqual(r.status_code, 401, f"{path}: {r.status_code} {r.text}")
            r = client.post("/v1/console/api/learning/subscribe")
            self.assertEqual(r.status_code, 401, r.text)
            r = client.post("/v1/console/api/learning/unsubscribe", params={"subscriber_id": "x"})
            self.assertEqual(r.status_code, 401, r.text)

    def test_authenticated_tenant_comes_from_session(self):
        with _sandbox(Path(self._tmp)) as (client, home, tid):
            r = client.post("/v1/console/api/learning/subscribe")
            self.assertEqual(r.status_code, 200, r.text)
            body = r.json()
            self.assertEqual(body["tenant_id"], tid)
            self.assertTrue(body["subscriber_id"])

            h = client.get("/v1/console/api/learning/health")
            self.assertEqual(h.status_code, 200, h.text)
            self.assertEqual(h.json()["status"], "healthy", h.text)
            self.assertEqual(h.json()["subscriber_count"], 1)

            s = client.get("/v1/console/api/learning/summary")
            self.assertEqual(s.status_code, 200, s.text)
            self.assertEqual(s.json()["status"], "ok")

            u = client.get("/v1/console/api/learning/user/u1")
            self.assertEqual(u.status_code, 200, u.text)

            # The dashboard's store is rooted at <CORVIN_HOME>/tenants/<tid>/ —
            # the same store the emitter writes to, not ~/.corvin/.../global.
            self.assertTrue((home / "tenants" / tid / "learning" / "events").is_dir())

            un = client.post("/v1/console/api/learning/unsubscribe", params={"subscriber_id": body["subscriber_id"]})
            self.assertEqual(un.status_code, 200, un.text)

    def test_websocket_without_session_is_closed_4401(self):
        with _sandbox(Path(self._tmp)) as (client, home, tid):
            client.cookies.clear()
            from starlette.websockets import WebSocketDisconnect
            with self.assertRaises(WebSocketDisconnect) as ctx:
                with client.websocket_connect("/v1/console/api/learning/stream?subscriber_id=nope"):
                    pass
            self.assertEqual(ctx.exception.code, 4401)


class TestVibeMetricsApiAuth(unittest.TestCase):
    """N-06: ``routes/vibe_metrics_api`` (unmounted today) must gate on the real session."""

    def setUp(self):
        self._tmp = tempfile.mkdtemp()

    def tearDown(self):
        import shutil
        shutil.rmtree(self._tmp, ignore_errors=True)

    def test_401_without_session_and_200_with(self):
        with _sandbox(Path(self._tmp)) as (client, home, tid):
            from corvin_console.routes import vibe_metrics_api
            client.app.include_router(vibe_metrics_api.router, prefix="/v1/console")
            try:
                # DB + emitter store are rooted under CORVIN_HOME, never ~/.corvin
                self.assertTrue(str(vibe_metrics_api._db.db_path).startswith(str(home)))

                ok = client.get("/v1/console/api/metrics/stats")
                self.assertEqual(ok.status_code, 200, ok.text)

                client.cookies.clear()
                r = client.get("/v1/console/api/metrics/stats")
                self.assertEqual(r.status_code, 401, r.text)
                r = client.get("/v1/console/api/metrics/session/s1/summary")
                self.assertEqual(r.status_code, 401, r.text)
            finally:
                vibe_metrics_api._emitter.stop()
