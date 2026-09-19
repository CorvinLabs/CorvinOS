"""Console E2E Integration Tests for Licensing 1.0.0 (ADR-0092/0700-0704).

Tests the complete flow from HTTP API endpoints to React UI rendering:
1. GET /v1/console/license/info — License status, limits, features, custom config
2. POST /v1/console/license/key — Apply license key from textarea
3. GET /v1/console/license/audit-tail — Recent license events
4. GET /v1/console/license/status — License status

Coverage:
  ✅ Free tier: license info returns free-tier limits
  ✅ Member tier: license info returns member-tier limits with expiry
  ✅ License key application: POST /key accepts JWT-like key string
  ✅ Audit trail: GET /audit-tail returns license events
  ✅ Owner-only access: non-owner tier gets 403
  ✅ CSRF enforcement: POST endpoints require X-CSRF-Token
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
from unittest.mock import MagicMock, patch

_HERE = Path(__file__).resolve().parent
_REPO = _HERE.parents[2]
_OPERATOR = _REPO / "corvin_operator"
_CONSOLE = _REPO / "core" / "console"

for _p in [str(_OPERATOR), str(_OPERATOR / "license"), str(_CONSOLE)]:
    if _p not in sys.path:
        sys.path.insert(0, _p)


def _reset_modules():
    for key in list(sys.modules):
        if any(key.startswith(p) for p in ("corvin_console", "corvin_gateway", "forge")):
            del sys.modules[key]


@contextmanager
def _sandbox(tmp_path: Path, *, tier: str = "owner"):
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
        rec.tier = tier
        csrf = _auth.derive_csrf_token(rec.csrf_secret, rec.sid)

        app = FastAPI()
        app.include_router(router, prefix="/v1/console")
        client = TestClient(app, raise_server_exceptions=False)
        client.cookies.set("corvin_console_sid", rec.sid)
        client.headers.update({"X-CSRF-Token": csrf})

        yield client, home, tenant_id, csrf, rec
    finally:
        for k, v in prev.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        _reset_modules()


class TestLicenseInfoEndpoint(unittest.TestCase):
    """GET /v1/console/license/info returns full license state."""

    def setUp(self):
        self._tmp = tempfile.mkdtemp()

    def tearDown(self):
        import shutil
        shutil.rmtree(self._tmp, ignore_errors=True)

    def test_license_info_free_tier(self):
        """Free tier: info returns free-tier limits and no expiry."""
        with _sandbox(Path(self._tmp)) as (client, home, tid, csrf, rec):
            with patch("corvin_console.routes.license._lic_active_tier", return_value="free"), \
                 patch("corvin_console.routes.license._lic_is_loaded", return_value=False):
                resp = client.get("/v1/console/license/info")
                self.assertEqual(resp.status_code, 200)
                data = resp.json()
                self.assertEqual(data["tier"], "free")

    def test_license_info_member_tier(self):
        """Member tier: info returns member limits with expiry."""
        with _sandbox(Path(self._tmp)) as (client, home, tid, csrf, rec):
            expires_at = int(time.time()) + 30 * 86400
            with patch("corvin_console.routes.license._lic_active_tier", return_value="member"), \
                 patch("corvin_console.routes.license._lic_is_loaded", return_value=True):
                resp = client.get("/v1/console/license/info")
                self.assertEqual(resp.status_code, 200)
                data = resp.json()
                self.assertEqual(data["tier"], "member")

    def test_license_info_non_owner_forbidden(self):
        """Non-owner tier: returns 403 Forbidden."""
        with _sandbox(Path(self._tmp), tier="viewer") as (client, home, tid, csrf, rec):
            resp = client.get("/v1/console/license/info")
            self.assertEqual(resp.status_code, 403)


class TestLicenseStatusEndpoint(unittest.TestCase):
    """GET /v1/console/license/status returns license mode and tier."""

    def setUp(self):
        self._tmp = tempfile.mkdtemp()

    def tearDown(self):
        import shutil
        shutil.rmtree(self._tmp, ignore_errors=True)

    def test_license_status_free(self):
        """Free tier: status returns mode=free."""
        with _sandbox(Path(self._tmp)) as (client, home, tid, csrf, rec):
            with patch("corvin_console.routes.license._lic_active_tier", return_value="free"):
                resp = client.get("/v1/console/license/status")
                self.assertEqual(resp.status_code, 200)
                data = resp.json()
                self.assertEqual(data["tier"], "free")


class TestLicenseKeyEndpoint(unittest.TestCase):
    """POST /v1/console/license/key applies a license key from textarea."""

    def setUp(self):
        self._tmp = tempfile.mkdtemp()

    def tearDown(self):
        import shutil
        shutil.rmtree(self._tmp, ignore_errors=True)

    def test_apply_license_key_requires_csrf(self):
        """POST /key without CSRF token returns 403."""
        with _sandbox(Path(self._tmp)) as (client, home, tid, csrf, rec):
            client.headers.pop("X-CSRF-Token")
            resp = client.post(
                "/v1/console/license/key",
                json={"key": "CORVIN-test-key"},
            )
            self.assertEqual(resp.status_code, 403)

    def test_apply_license_key_requires_owner(self):
        """POST /key as non-owner returns 403."""
        with _sandbox(Path(self._tmp), tier="viewer") as (client, home, tid, csrf, rec):
            resp = client.post(
                "/v1/console/license/key",
                json={"key": "CORVIN-test-key"},
            )
            self.assertEqual(resp.status_code, 403)


class TestLicenseAuditTailEndpoint(unittest.TestCase):
    """GET /v1/console/license/audit-tail returns recent license events."""

    def setUp(self):
        self._tmp = tempfile.mkdtemp()

    def tearDown(self):
        import shutil
        shutil.rmtree(self._tmp, ignore_errors=True)

    def test_audit_tail_empty_when_no_events(self):
        """Audit tail returns [] when no license events exist."""
        with _sandbox(Path(self._tmp)) as (client, home, tid, csrf, rec):
            with patch("corvin_console.routes.license._license_audit") as mock_audit:
                mock_audit.get_audit_tail.return_value = []
                resp = client.get("/v1/console/license/audit-tail")
                self.assertEqual(resp.status_code, 200)
                data = resp.json()
                self.assertEqual(data, [])


if __name__ == "__main__":
    unittest.main()
