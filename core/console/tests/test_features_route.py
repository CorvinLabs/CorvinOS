"""HTTP route-level tests for Settings → Features + Worker Engine.

Mirrors the ``_sandbox`` TestClient pattern from test_instance_route.py.

Covers what the UI depends on and what the compliance rule depends on:
a fresh install answers "everything off, engine native", a toggle round-trips,
and nothing outside the registry can be written.
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_REPO = _HERE.parents[2]
_OPERATOR = _REPO / "operator"
_CONSOLE = _REPO / "core" / "console"
_BRIDGES_SHARED = _OPERATOR / "bridges" / "shared"

for _p in [str(_OPERATOR), str(_OPERATOR / "license"), str(_OPERATOR / "forge"),
           str(_CONSOLE), str(_BRIDGES_SHARED)]:
    if _p not in sys.path:
        sys.path.insert(0, _p)


def _reset_modules():
    for key in list(sys.modules):
        if any(key.startswith(p) for p in ("corvin_console", "corvin_gateway", "forge")):
            del sys.modules[key]


@contextmanager
def _sandbox(tmp_path: Path):
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
        app = FastAPI()
        app.include_router(router, prefix="/v1/console")
        client = TestClient(app, raise_server_exceptions=False)
        client.cookies.set("corvin_console_sid", rec.sid)
        yield client, _auth.derive_csrf_token(rec.csrf_secret, rec.sid), home
    finally:
        for k, v in prev.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        _reset_modules()


class TestFeaturesRoute(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp()

    def tearDown(self):
        import shutil
        shutil.rmtree(self._tmp, ignore_errors=True)

    def _hdr(self, csrf: str) -> dict[str, str]:
        return {"X-CSRF-Token": csrf}

    # ── Registry listing ────────────────────────────────────────────────

    def test_fresh_install_lists_everything_off(self):
        with _sandbox(Path(self._tmp)) as (client, _csrf, _home):
            resp = client.get("/v1/console/settings/features")
            self.assertEqual(resp.status_code, 200, resp.text)
            features = resp.json()["features"]
            self.assertTrue(features, "registry should not be empty")
            for f in features:
                self.assertFalse(f["enabled"], f"{f['id']} must ship dark")
                self.assertEqual(f["source"], "default")
                self.assertTrue(f["owner"])
                self.assertTrue(f["target_release"])

    def test_toggle_round_trips(self):
        with _sandbox(Path(self._tmp)) as (client, csrf, _home):
            fid = client.get("/v1/console/settings/features").json()["features"][0]["id"]
            resp = client.put(f"/v1/console/settings/features/{fid}",
                              json={"enabled": True}, headers=self._hdr(csrf))
            self.assertEqual(resp.status_code, 200, resp.text)
            self.assertTrue(resp.json()["enabled"])

            listed = {f["id"]: f for f in
                      client.get("/v1/console/settings/features").json()["features"]}
            self.assertTrue(listed[fid]["enabled"])
            self.assertEqual(listed[fid]["source"], "console")

            resp = client.put(f"/v1/console/settings/features/{fid}",
                              json={"enabled": False}, headers=self._hdr(csrf))
            self.assertEqual(resp.status_code, 200, resp.text)
            self.assertFalse(resp.json()["enabled"])

    def test_unknown_flag_is_rejected(self):
        with _sandbox(Path(self._tmp)) as (client, csrf, _home):
            resp = client.put("/v1/console/settings/features/house_rules_off",
                              json={"enabled": True}, headers=self._hdr(csrf))
            self.assertEqual(resp.status_code, 404, resp.text)
            self.assertEqual(resp.json()["detail"], "unknown_flag")

    def test_write_requires_csrf(self):
        with _sandbox(Path(self._tmp)) as (client, csrf, _home):
            fid = client.get("/v1/console/settings/features").json()["features"][0]["id"]
            resp = client.put(f"/v1/console/settings/features/{fid}",
                              json={"enabled": True})
            self.assertGreaterEqual(resp.status_code, 400, resp.text)

    # ── Worker engine ───────────────────────────────────────────────────

    def test_worker_engine_defaults_to_native(self):
        with _sandbox(Path(self._tmp)) as (client, _csrf, _home):
            resp = client.get("/v1/console/settings/worker-engine")
            self.assertEqual(resp.status_code, 200, resp.text)
            body = resp.json()
            self.assertEqual(body["mode"], "native")
            self.assertEqual(body["default"], "native")
            self.assertEqual(body["modes"], ["native", "acs", "tde"])

    def test_worker_engine_selection_round_trips(self):
        with _sandbox(Path(self._tmp)) as (client, csrf, _home):
            for mode in ("acs", "tde", "native"):
                resp = client.put("/v1/console/settings/worker-engine",
                                  json={"mode": mode}, headers=self._hdr(csrf))
                self.assertEqual(resp.status_code, 200, resp.text)
                self.assertEqual(resp.json()["mode"], mode)
                self.assertEqual(
                    client.get("/v1/console/settings/worker-engine").json()["mode"],
                    mode)

    def test_unknown_worker_engine_is_rejected(self):
        with _sandbox(Path(self._tmp)) as (client, csrf, _home):
            resp = client.put("/v1/console/settings/worker-engine",
                              json={"mode": "quantum"}, headers=self._hdr(csrf))
            self.assertEqual(resp.status_code, 400, resp.text)
            self.assertEqual(
                client.get("/v1/console/settings/worker-engine").json()["mode"],
                "native")



class TestBrowserFlagGate(unittest.TestCase):
    """Behavioural on/off pair for `browser_automation` — the structural test
    in test_feature_flags.py only proves the gate exists, not that it bites."""

    def setUp(self):
        self._tmp = tempfile.mkdtemp()

    def tearDown(self):
        import shutil
        shutil.rmtree(self._tmp, ignore_errors=True)

    def test_off_refuses_session_creation(self):
        with _sandbox(Path(self._tmp)) as (client, csrf, _home):
            resp = client.post("/v1/console/browser/session", json={},
                               headers={"X-CSRF-Token": csrf})
            self.assertEqual(resp.status_code, 403, resp.text)
            self.assertIn("Settings → Features", resp.json()["detail"])

    def test_on_passes_the_feature_gate(self):
        with _sandbox(Path(self._tmp)) as (client, csrf, _home):
            r = client.put("/v1/console/settings/features/browser_automation",
                           json={"enabled": True}, headers={"X-CSRF-Token": csrf})
            self.assertEqual(r.status_code, 200, r.text)
            resp = client.post("/v1/console/browser/session", json={},
                               headers={"X-CSRF-Token": csrf})
            # Anything but the feature gate: launching a real browser may still
            # fail in CI (no Chrome), that is not what this test asserts.
            self.assertNotEqual(resp.status_code, 403, resp.text)


if __name__ == "__main__":
    unittest.main()
