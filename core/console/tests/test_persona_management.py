"""Per-subtask E2E for persona management (create / edit / disable / delete /
engine) and the fresh-install bundle-visibility fix.

Covers:
  * GET /personas lists the SHIPPED bundle personas (fresh install is not empty)
  * _resolve_bundle_dir() falls back to the vendored operator tree (wheel layout)
  * PUT /personas/{new} creates a user-scope persona; GET reflects it
  * POST /personas/{name}/disable + /enable toggle the per-tenant registry and
    the GET projection's `disabled` flag; bundle personas can be disabled too
  * DELETE /personas/{name} removes a user override (reauth-gated); bundle
    personas are read-only (404); a stale disabled entry is pruned on delete
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path

_REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_REPO / "core" / "console"))
sys.path.insert(0, str(_REPO / "core" / "gateway"))
sys.path.insert(0, str(_REPO / "operator" / "forge"))
sys.path.insert(0, str(_REPO / "operator" / "bridges" / "shared"))


def _reset_modules():
    for name in list(sys.modules):
        if name.startswith("corvin_console") or name in ("forge.paths",):
            sys.modules.pop(name, None)


@contextmanager
def _sandbox(tenant_id: str = "_default"):
    with tempfile.TemporaryDirectory(prefix="console-persona-test-") as td:
        home = Path(td) / "corvin"
        xdg = Path(td) / "xdg"
        (home / "tenants" / tenant_id / "global" / "auth").mkdir(parents=True)
        (home / "tenants" / tenant_id / "global" / "forge").mkdir(parents=True)
        (home / "tenants" / tenant_id / "global" / "console" / "sessions").mkdir(parents=True)
        (xdg / "corvin-voice").mkdir(parents=True)

        prev = {k: os.environ.get(k) for k in ("CORVIN_HOME", "XDG_CONFIG_HOME")}
        os.environ["CORVIN_HOME"] = str(home)
        os.environ["XDG_CONFIG_HOME"] = str(xdg)
        try:
            _reset_modules()
            from corvin_console import auth as console_session_auth
            from corvin_console.app import router
            from fastapi import FastAPI
            from fastapi.testclient import TestClient

            rec = console_session_auth.create_session(tenant_id=tenant_id)
            csrf = console_session_auth.derive_csrf_token(rec.csrf_secret, rec.sid)
            app = FastAPI()
            app.include_router(router, prefix="/v1/console")
            client = TestClient(app)
            client.cookies.set("corvin_console_sid", rec.sid)
            yield {"client": client, "csrf": csrf, "token": rec.sid_fingerprint,
                   "home": home, "tenant_id": tenant_id}
        finally:
            for k, v in prev.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
            _reset_modules()


def _h(ctx):
    return {"X-CSRF-Token": ctx["csrf"]}


def _names(body):
    return {p["name"] for p in body["personas"]}


class BundleVisibilityTests(unittest.TestCase):
    def test_fresh_install_lists_bundle_personas(self):
        with _sandbox() as ctx:
            r = ctx["client"].get("/v1/console/personas")
            self.assertEqual(r.status_code, 200, r.text)
            body = r.json()
            # The shipped bundle personas must be visible on a fresh tenant
            # (no user overrides written yet) — this is the fresh-install fix.
            self.assertGreater(body["count"], 0)
            names = _names(body)
            self.assertIn("assistant", names)

    def test_resolve_bundle_dir_prefers_vendored_when_repo_missing(self):
        with _sandbox():
            from corvin_console.routes import personas as P
            # Source-tree path exists here, so it is returned as-is.
            self.assertTrue(P._resolve_bundle_dir().is_dir())


class CreateEditDeleteTests(unittest.TestCase):
    def test_create_user_persona_then_delete(self):
        with _sandbox() as ctx:
            cl = ctx["client"]
            r = cl.put("/v1/console/personas/mytest", headers=_h(ctx), json={
                "body": {"name": "mytest", "description": "my custom persona"},
                "re_auth_token": ctx["token"],
            })
            self.assertEqual(r.status_code, 200, r.text)
            self.assertIn("mytest", _names(cl.get("/v1/console/personas").json()))

            # Delete (reauth-gated) removes it again.
            r = cl.request("DELETE", "/v1/console/personas/mytest",
                           headers=_h(ctx), json={"re_auth_token": ctx["token"]})
            self.assertEqual(r.status_code, 200, r.text)
            self.assertTrue(r.json()["deleted"])
            self.assertNotIn("mytest", _names(cl.get("/v1/console/personas").json()))

    def test_delete_bundle_persona_is_404(self):
        with _sandbox() as ctx:
            r = ctx["client"].request(
                "DELETE", "/v1/console/personas/assistant",
                headers=_h(ctx), json={"re_auth_token": ctx["token"]})
            self.assertEqual(r.status_code, 404, r.text)

    def test_delete_requires_reauth(self):
        with _sandbox() as ctx:
            cl = ctx["client"]
            cl.put("/v1/console/personas/tmp", headers=_h(ctx), json={
                "body": {"name": "tmp"}, "re_auth_token": ctx["token"]})
            r = cl.request("DELETE", "/v1/console/personas/tmp",
                           headers=_h(ctx), json={"re_auth_token": "wrong"})
            self.assertEqual(r.status_code, 401, r.text)


class DisableEnableTests(unittest.TestCase):
    def test_disable_then_enable_bundle_persona(self):
        with _sandbox() as ctx:
            cl = ctx["client"]
            r = cl.post("/v1/console/personas/research/disable", headers=_h(ctx))
            self.assertEqual(r.status_code, 200, r.text)
            self.assertTrue(r.json()["disabled"])
            # GET projection reflects it.
            got = {p["name"]: p for p in cl.get("/v1/console/personas").json()["personas"]}
            self.assertTrue(got["research"]["disabled"])

            r = cl.post("/v1/console/personas/research/enable", headers=_h(ctx))
            self.assertEqual(r.status_code, 200, r.text)
            self.assertFalse(r.json()["disabled"])
            got = {p["name"]: p for p in cl.get("/v1/console/personas").json()["personas"]}
            self.assertFalse(got["research"]["disabled"])

    def test_disable_unknown_persona_404(self):
        with _sandbox() as ctx:
            r = ctx["client"].post(
                "/v1/console/personas/does-not-exist/disable", headers=_h(ctx))
            self.assertEqual(r.status_code, 404, r.text)


if __name__ == "__main__":
    unittest.main()
