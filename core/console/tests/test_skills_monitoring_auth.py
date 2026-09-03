"""D-01 — skills monitoring routes are session-gated, tenant-bound, CSRF-gated.

Drives the REAL HTTP boundary (FastAPI router at /v1/console) with a
sandboxed CORVIN_HOME:

  * no session cookie → 401 on every route
  * a live session → 200, and the tenant in the response is the SESSION
    tenant even when the caller passes ``?tenant_id=other`` (the old query
    parameter is ignored — it no longer exists)
  * ``POST /api/skills/cache/clear`` without the CSRF header → 403; with it
    → 200 and it clears the SAME resolver the stats route reports on
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path

_REPO = Path(__file__).resolve().parents[3]
_OPERATOR = _REPO / "operator"
_CONSOLE = _REPO / "core" / "console"
for _p in [
    str(_OPERATOR / "bridges" / "shared"),
    str(_OPERATOR / "bridges"),
    str(_OPERATOR / "forge"),
    str(_CONSOLE),
]:
    if _p not in sys.path:
        sys.path.insert(0, _p)

ROUTES = (
    "/v1/console/api/skills/cache-stats",
    "/v1/console/api/skills/circuit-breaker",
    "/v1/console/api/skills/rate-limiter/some-client",
    "/v1/console/api/skills/health",
    "/v1/console/api/skills/status",
    "/v1/console/api/skills/os.nothing/metrics",
)


def _reset_modules():
    for key in list(sys.modules):
        if any(key.startswith(p) for p in ("corvin_console", "corvin_gateway", "forge")):
            del sys.modules[key]


@contextmanager
def _sandbox(tmp_path: Path, *, set_csrf: bool = True):
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
        from core.skills.corvin_skills.resolver import reset_resolvers
        reset_resolvers()
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
        if set_csrf:
            client.headers.update({"X-CSRF-Token": csrf})
        yield client, home, tenant_id, csrf
    finally:
        for k, v in prev.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        _reset_modules()


class TestSkillsMonitoringAuth(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.mkdtemp()

    def tearDown(self):
        import shutil
        shutil.rmtree(self._tmp, ignore_errors=True)

    def test_unauthenticated_is_401_everywhere(self):
        with _sandbox(Path(self._tmp)) as (client, home, tid, csrf):
            client.cookies.clear()
            for route in ROUTES:
                r = client.get(route)
                self.assertEqual(r.status_code, 401, f"{route}: {r.status_code} {r.text}")
            r = client.post("/v1/console/api/skills/cache/clear")
            self.assertEqual(r.status_code, 401, r.text)

    def test_authenticated_tenant_comes_from_session_not_query(self):
        with _sandbox(Path(self._tmp)) as (client, home, tid, csrf):
            for route in ("/v1/console/api/skills/cache-stats",
                          "/v1/console/api/skills/health",
                          "/v1/console/api/skills/status"):
                r = client.get(route, params={"tenant_id": "other"})
                self.assertEqual(r.status_code, 200, f"{route}: {r.text}")
                self.assertEqual(r.json()["tenant_id"], tid, r.text)
            r = client.get("/v1/console/api/skills/circuit-breaker")
            self.assertEqual(r.status_code, 200, r.text)
            self.assertIn(r.json()["state"], ("CLOSED", "OPEN", "HALF_OPEN"))
            r = client.get("/v1/console/api/skills/rate-limiter/client-a")
            self.assertEqual(r.status_code, 200, r.text)
            self.assertEqual(r.json()["quota_status"], "GREEN")

    def test_cache_clear_requires_csrf(self):
        with _sandbox(Path(self._tmp), set_csrf=False) as (client, home, tid, csrf):
            r = client.post("/v1/console/api/skills/cache/clear", params={"tenant_id": "other"})
            self.assertEqual(r.status_code, 403, r.text)
            r = client.post("/v1/console/api/skills/cache/clear",
                            headers={"X-CSRF-Token": csrf})
            self.assertEqual(r.status_code, 200, r.text)
            self.assertEqual(r.json()["tenant_id"], tid)

    def test_cache_routes_share_one_resolver(self):
        """/cache-stats, /cache/clear and /health report the SAME cache."""
        with _sandbox(Path(self._tmp)) as (client, home, tid, csrf):
            manifest = home / "tenants" / tid / "skill-forge" / "manifest.json"
            manifest.parent.mkdir(parents=True, exist_ok=True)
            manifest.write_text(json.dumps({"skills": [{"name": "a.b", "metadata": {}}]}))

            from core.skills.corvin_skills.resolver import resolver_for
            shared = resolver_for(tid)
            for _ in range(3):          # 1 miss + 2 hits → hit_rate 0.67
                shared.resolve("a.b")

            stats = client.get("/v1/console/api/skills/cache-stats").json()
            self.assertEqual(stats["size"], 1, stats)
            self.assertEqual(stats["hits"], 2, stats)

            health = client.get("/v1/console/api/skills/health").json()
            self.assertEqual(health["cache"]["size"], 1, health)
            self.assertTrue(health["healthy"], health)

            cleared = client.post("/v1/console/api/skills/cache/clear").json()
            self.assertEqual(cleared["entries_cleared"], 1, cleared)
            self.assertEqual(client.get("/v1/console/api/skills/cache-stats").json()["size"], 0)

    def test_source_has_no_auth_fallback_stub(self):
        src = (_CONSOLE / "corvin_console" / "routes" / "skills_monitoring.py").read_text()
        self.assertNotIn("except ImportError", src)
        self.assertNotIn("Depends(get_current_user)", src)
        self.assertNotIn("import get_current_user", src)
        self.assertNotIn("tenant_id: str = ", src)   # no query-param tenant
        self.assertIn("require_csrf", src)
        self.assertIn("rec.tenant_id", src)


if __name__ == "__main__":
    unittest.main()
