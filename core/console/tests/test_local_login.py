"""E2E tests for GET /v1/console/auth/local-login (ADR-0065).

Covers:
  * localhost access → 302 to /console/ with session cookie set
  * session is valid immediately (whoami returns 200)
  * non-localhost → 403
  * CORVIN_LOCAL_AUTOLOGIN=0 → 403
"""
from __future__ import annotations

import os
import sys
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

_REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_REPO / "core" / "console"))
sys.path.insert(0, str(_REPO / "core" / "gateway"))
sys.path.insert(0, str(_REPO / "operator" / "forge"))

import importlib


def _reset_modules():
    for key in list(sys.modules):
        if any(key.startswith(p) for p in ("corvin_console", "corvin_gateway", "forge")):
            del sys.modules[key]


@contextmanager
def _sandbox(tmp_path: Path, monkeypatch_env: dict | None = None):
    """Spin up a self-contained console app with a real token store."""
    import tempfile
    home = tmp_path / "corvin_home"
    tenant_id = "_default"
    (home / "tenants" / tenant_id / "global" / "auth").mkdir(parents=True)
    (home / "tenants" / tenant_id / "global" / "forge").mkdir(parents=True)
    (home / "tenants" / tenant_id / "global" / "console" / "sessions").mkdir(parents=True)

    extra_env = monkeypatch_env or {}
    prev = {k: os.environ.get(k) for k in ("CORVIN_HOME", "CORVIN_TENANT_ID", "CORVIN_LOCAL_AUTOLOGIN")}
    os.environ["CORVIN_HOME"] = str(home)
    os.environ["CORVIN_TENANT_ID"] = tenant_id
    for k, v in extra_env.items():
        os.environ[k] = v

    try:
        _reset_modules()
        from corvin_console.app import router
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        app = FastAPI()
        app.include_router(router, prefix="/v1/console")
        yield TestClient(app, raise_server_exceptions=False)
    finally:
        for k, v in prev.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        _reset_modules()


class TestLocalLogin(unittest.TestCase):

    def setUp(self):
        import tempfile
        self._tmp = tempfile.mkdtemp()
        self._tmp_path = Path(self._tmp)

    def tearDown(self):
        import shutil
        shutil.rmtree(self._tmp, ignore_errors=True)

    def test_localhost_gets_redirect_and_cookie(self):
        """From 127.0.0.1: should get 302 + session cookie."""
        with _sandbox(self._tmp_path) as client:
            with patch("corvin_console.routes.auth_routes._is_localhost", return_value=True):
                r = client.get("/v1/console/auth/local-login", follow_redirects=False)
        self.assertEqual(r.status_code, 302)
        self.assertEqual(r.headers["location"], "/console/")
        self.assertIn("corvin_console_sid", r.cookies)

    def test_localhost_session_is_valid(self):
        """After local-login, whoami should return 200."""
        with _sandbox(self._tmp_path) as client:
            with patch("corvin_console.routes.auth_routes._is_localhost", return_value=True):
                login = client.get("/v1/console/auth/local-login", follow_redirects=False)
            sid = login.cookies["corvin_console_sid"]
            whoami = client.get(
                "/v1/console/auth/whoami",
                cookies={"corvin_console_sid": sid},
            )
        self.assertEqual(whoami.status_code, 200)
        data = whoami.json()
        self.assertEqual(data["tenant_id"], "_default")
        self.assertIn("csrf_token", data)

    def test_non_localhost_gets_403(self):
        """From a non-localhost IP: must be rejected."""
        with _sandbox(self._tmp_path) as client:
            with patch("corvin_console.routes.auth_routes._is_localhost", return_value=False):
                r = client.get("/v1/console/auth/local-login", follow_redirects=False)
        self.assertEqual(r.status_code, 403)
        self.assertIn("localhost", r.json()["detail"])

    def test_disabled_via_env_gets_403(self):
        """CORVIN_LOCAL_AUTOLOGIN=0 must block the endpoint."""
        with _sandbox(self._tmp_path, {"CORVIN_LOCAL_AUTOLOGIN": "0"}) as client:
            with patch("corvin_console.routes.auth_routes._is_localhost", return_value=True):
                r = client.get("/v1/console/auth/local-login", follow_redirects=False)
        self.assertEqual(r.status_code, 403)
        self.assertIn("disabled", r.json()["detail"])

    def test_enabled_explicitly_via_env(self):
        """CORVIN_LOCAL_AUTOLOGIN=1 (explicit) should still work."""
        with _sandbox(self._tmp_path, {"CORVIN_LOCAL_AUTOLOGIN": "1"}) as client:
            with patch("corvin_console.routes.auth_routes._is_localhost", return_value=True):
                r = client.get("/v1/console/auth/local-login", follow_redirects=False)
        self.assertEqual(r.status_code, 302)



if __name__ == "__main__":
    unittest.main()
