"""HTTP route-level tests for GET /v1/console/settings/instance-identity — ADR-0145 M4.

Mirrors the ``_sandbox`` TestClient pattern from test_license_http_gates.py.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import time
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

_HERE = Path(__file__).resolve().parent
_REPO = _HERE.parents[2]
_OPERATOR = _REPO / "operator"
_CONSOLE = _REPO / "core" / "console"
_BRIDGES_SHARED = _OPERATOR / "bridges" / "shared"

for _p in [str(_OPERATOR), str(_OPERATOR / "license"), str(_OPERATOR / "forge"), str(_CONSOLE), str(_BRIDGES_SHARED)]:
    if _p not in sys.path:
        sys.path.insert(0, _p)


def _reset_modules():
    for key in list(sys.modules):
        if any(key.startswith(p) for p in ("corvin_console", "corvin_gateway", "forge")):
            del sys.modules[key]


@contextmanager
def _sandbox(tmp_path: Path):
    """Spin up a sandboxed console app with a live owner session and an
    isolated instance-identity file set (id/key/cert/CRL-cache paths)."""
    home = tmp_path / "corvin_home"
    tenant_id = "_default"
    (home / "tenants" / tenant_id / "global" / "auth").mkdir(parents=True)
    (home / "tenants" / tenant_id / "global" / "forge").mkdir(parents=True)
    (home / "tenants" / tenant_id / "global" / "console" / "sessions").mkdir(parents=True)

    identity_env = {
        "CORVIN_INSTANCE_ID_PATH": str(tmp_path / "instance_id.json"),
        "CORVIN_INSTANCE_KEY_PATH": str(tmp_path / "instance_key.pem"),
        "CORVIN_INSTANCE_CERT_PATH": str(tmp_path / "instance_cert.jwt"),
        "CORVIN_CRL_CACHE_PATH": str(tmp_path / "ibc_crl_cache.json"),
    }
    prev = {k: os.environ.get(k) for k in ("CORVIN_HOME", "CORVIN_TENANT_ID", *identity_env)}
    os.environ["CORVIN_HOME"] = str(home)
    os.environ["CORVIN_TENANT_ID"] = tenant_id
    os.environ.update(identity_env)

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

        yield client, home, tenant_id
    finally:
        for k, v in prev.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        _reset_modules()


class TestInstanceIdentityRoute(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp()

    def tearDown(self):
        import shutil
        shutil.rmtree(self._tmp, ignore_errors=True)

    def test_returns_instance_id_with_no_ibc_bound(self):
        with _sandbox(Path(self._tmp)) as (client, home, tid):
            resp = client.get("/v1/console/settings/instance-identity")
            self.assertEqual(resp.status_code, 200, resp.text)
            body = resp.json()
            self.assertTrue(body["instance_id"])
            self.assertFalse(body["ibc_bound"])
            self.assertFalse(body["hardware_bound"])
            self.assertIsNone(body["hardware_matches"])
            self.assertEqual(body["revocation_status"], "unknown")

    def test_stable_instance_id_across_requests(self):
        with _sandbox(Path(self._tmp)) as (client, home, tid):
            r1 = client.get("/v1/console/settings/instance-identity")
            r2 = client.get("/v1/console/settings/instance-identity")
            self.assertEqual(r1.json()["instance_id"], r2.json()["instance_id"])

    def test_never_calls_network(self):
        """The route must be local-state-only — no CRL fetch, no bind request."""
        with _sandbox(Path(self._tmp)) as (client, home, tid):
            with patch("urllib.request.urlopen", side_effect=AssertionError("must not hit network")):
                resp = client.get("/v1/console/settings/instance-identity")
            self.assertEqual(resp.status_code, 200, resp.text)

    def test_ibc_bound_reflects_local_cert(self):
        with _sandbox(Path(self._tmp)) as (client, home, tid):
            # Prime instance_id + keypair, then plant a locally-valid-shaped
            # (unsigned-check-bypassed) cert file — get_ibc() only checks
            # expiry locally, never re-verifies the RS256 signature.
            import instance_identity  # type: ignore[import-not-found]
            instance_id = instance_identity.get_instance_id()
            pubkey_b64 = instance_identity.get_instance_pubkey_b64()
            try:
                import jwt as _pyjwt
            except ImportError:
                self.skipTest("pyjwt not installed")
            claims = {
                "sub": instance_id, "email": "owner@example.com", "plan": "member",
                "instance_pubkey": pubkey_b64, "iat": int(time.time()),
                "exp": int(time.time()) + 3600, "jti": "route-test-jti",
            }
            token = _pyjwt.encode(claims, "unused-secret", algorithm="HS256")
            cert_path = Path(os.environ["CORVIN_INSTANCE_CERT_PATH"])
            cert_path.parent.mkdir(parents=True, exist_ok=True)
            cert_path.write_text(token, encoding="utf-8")

            resp = client.get("/v1/console/settings/instance-identity")
            self.assertEqual(resp.status_code, 200, resp.text)
            body = resp.json()
            self.assertTrue(body["ibc_bound"])
            self.assertEqual(body["plan"], "member")
            self.assertEqual(body["email"], "owner@example.com")

    def test_revoked_status_surfaced_from_cache(self):
        with _sandbox(Path(self._tmp)) as (client, home, tid):
            import instance_identity  # type: ignore[import-not-found]
            instance_id = instance_identity.get_instance_id()
            pubkey_b64 = instance_identity.get_instance_pubkey_b64()
            try:
                import jwt as _pyjwt
            except ImportError:
                self.skipTest("pyjwt not installed")
            claims = {
                "sub": instance_id, "instance_pubkey": pubkey_b64,
                "iat": int(time.time()), "exp": int(time.time()) + 3600,
                "jti": "revoked-jti",
            }
            token = _pyjwt.encode(claims, "unused-secret", algorithm="HS256")
            Path(os.environ["CORVIN_INSTANCE_CERT_PATH"]).write_text(token, encoding="utf-8")
            Path(os.environ["CORVIN_CRL_CACHE_PATH"]).write_text(
                json.dumps({"fetched_at": time.time(), "revoked_jti": ["revoked-jti"]}),
                encoding="utf-8",
            )

            resp = client.get("/v1/console/settings/instance-identity")
            self.assertEqual(resp.json()["revocation_status"], "revoked")


if __name__ == "__main__":
    unittest.main()
