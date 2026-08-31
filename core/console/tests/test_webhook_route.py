"""HTTP route-level tests for the inbound webhook receiver (ADR-0124 M7).

Target: corvin_console/routes/webhooks.py :: receive_webhook()
  POST /webhook/{tenant_id}/{channel_id}

This endpoint is deliberately session-less ("No session required" — see the
route docstring) so external systems can post to it directly. If the operator
registers a channel *without* an ``hmac_secret_env``, the endpoint also skips
signature verification entirely — meaning it can be a fully unauthenticated
POST target. Confirmed blind spot: unlike every other console route that
buffers a raw request body (see routes/memory.py's ``_MAX_BODY_BYTES = 256 *
1024`` cap, enforced with a 413 *before* any expensive processing),
``receive_webhook`` does ``body_bytes = await request.body()``
unconditionally, with no size cap anywhere in the file. These tests document
that gap with real HTTP traffic through the TestClient (mirrors the
``_sandbox`` TestClient pattern from test_instance_route.py /
test_license_http_gates.py), not just a static-analysis claim.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import sys
import tempfile
import time
import unittest
from contextlib import contextmanager
from pathlib import Path

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
def _sandbox(tmp_path: Path, tenant_id: str = "_default"):
    """Spin up a sandboxed console app — no session, matching the endpoint
    under test which is intentionally reachable with zero authentication."""
    home = tmp_path / "corvin_home"
    (home / "tenants" / tenant_id / "global" / "auth").mkdir(parents=True)
    (home / "tenants" / tenant_id / "global" / "forge").mkdir(parents=True)
    (home / "tenants" / tenant_id / "global" / "console" / "sessions").mkdir(parents=True)
    (home / "tenants" / tenant_id / "global" / "bridges" / "custom").mkdir(parents=True)

    prev = {k: os.environ.get(k) for k in ("CORVIN_HOME", "CORVIN_TENANT_ID")}
    os.environ["CORVIN_HOME"] = str(home)
    os.environ["CORVIN_TENANT_ID"] = tenant_id

    try:
        _reset_modules()
        from corvin_console.app import router
        from corvin_console.routes import webhooks as webhooks_route
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        app = FastAPI()
        app.include_router(router, prefix="/v1/console")
        client = TestClient(app, raise_server_exceptions=False)

        yield client, home, tenant_id, webhooks_route
    finally:
        for k, v in prev.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        _reset_modules()


def _register_channel(webhooks_route, tenant_id, channel_id, *, hmac_secret_env=None,
                       rate_limit_per_hour=60):
    """Write a channel manifest directly (bypasses the admin PUT route, which
    requires a session — out of scope for the inbound-receiver tests here)."""
    manifest = {
        "channel_id": channel_id,
        "display_name": "Test Channel",
        "hmac_secret_env": hmac_secret_env,
        "persona": "assistant",
        "rate_limit_per_hour": rate_limit_per_hour,
        "description": "",
        "tenant_id": tenant_id,
        "inbound_url": f"/v1/console/webhook/{tenant_id}/{channel_id}",
        "created_at": time.time(),
        "updated_at": time.time(),
    }
    webhooks_route._write_channel(tenant_id, channel_id, manifest)


class TestReceiveWebhookNoAuth(unittest.TestCase):
    """Channel registered without hmac_secret_env: no session, no signature."""

    def setUp(self):
        self._tmp = tempfile.mkdtemp()

    def tearDown(self):
        import shutil
        shutil.rmtree(self._tmp, ignore_errors=True)

    def test_no_hmac_channel_is_reachable_with_zero_auth(self):
        """Documents the endpoint's designed exposure: with hmac_secret_env
        omitted, a bare POST (no cookie, no signature header) is accepted."""
        with _sandbox(Path(self._tmp)) as (client, home, tid, webhooks_route):
            _register_channel(webhooks_route, tid, "open-channel")
            resp = client.post(
                f"/v1/console/webhook/{tid}/open-channel",
                content=b'{"hello": "world"}',
                headers={"content-type": "application/json"},
            )
            self.assertEqual(resp.status_code, 200, resp.text)
            body = resp.json()
            self.assertTrue(body["ok"])
            self.assertEqual(body["payload_size"], len(b'{"hello": "world"}'))

    def test_unknown_channel_returns_404_before_any_processing(self):
        with _sandbox(Path(self._tmp)) as (client, home, tid, webhooks_route):
            resp = client.post(
                f"/v1/console/webhook/{tid}/does-not-exist",
                content=b"x" * 1024,
            )
            self.assertEqual(resp.status_code, 404, resp.text)

    def test_no_hmac_channel_accepts_multi_megabyte_body_with_no_size_cap(self):
        """BLIND SPOT: routes/memory.py enforces `_MAX_BODY_BYTES = 256 * 1024`
        and returns 413 for an oversized body on an *authenticated* route.
        This route has no equivalent cap anywhere, despite being reachable
        with zero authentication. A body far larger than memory.py's cap is
        accepted and fully echoed back in `payload_size` -- proving the whole
        thing was buffered into process memory with no rejection path."""
        with _sandbox(Path(self._tmp)) as (client, home, tid, webhooks_route):
            _register_channel(webhooks_route, tid, "open-channel")
            oversized = b"a" * (4 * 1024 * 1024)  # 4 MiB, 16x memory.py's cap
            resp = client.post(
                f"/v1/console/webhook/{tid}/open-channel",
                content=oversized,
                headers={"content-type": "application/octet-stream"},
            )
            self.assertEqual(resp.status_code, 200, resp.text)
            self.assertEqual(resp.json()["payload_size"], len(oversized))

    def test_rate_limit_per_hour_field_is_not_enforced(self):
        """BLIND SPOT: `rate_limit_per_hour` is accepted and persisted in the
        channel manifest (routes/webhooks.py L104/L139) but `receive_webhook`
        never reads it back or tracks a request count. A channel configured
        with the minimum allowed limit (1/hour) still accepts unlimited
        back-to-back requests -- the field is decorative, not enforced."""
        with _sandbox(Path(self._tmp)) as (client, home, tid, webhooks_route):
            _register_channel(webhooks_route, tid, "throttled", rate_limit_per_hour=1)
            for _ in range(5):
                resp = client.post(
                    f"/v1/console/webhook/{tid}/throttled",
                    content=b"{}",
                )
                self.assertEqual(resp.status_code, 200, resp.text)


class TestReceiveWebhookHmac(unittest.TestCase):
    """Channel registered *with* hmac_secret_env: signature is required, but
    the body is still buffered in full before the signature is checked."""

    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self._env_var = "TEST_WEBHOOK_HMAC_SECRET"
        self._prev_secret = os.environ.get(self._env_var)
        os.environ[self._env_var] = "s3cr3t"

    def tearDown(self):
        import shutil
        shutil.rmtree(self._tmp, ignore_errors=True)
        if self._prev_secret is None:
            os.environ.pop(self._env_var, None)
        else:
            os.environ[self._env_var] = self._prev_secret

    def _sign(self, body: bytes) -> str:
        return "sha256=" + hmac.new(b"s3cr3t", body, hashlib.sha256).hexdigest()

    def test_missing_signature_header_rejected(self):
        with _sandbox(Path(self._tmp)) as (client, home, tid, webhooks_route):
            _register_channel(webhooks_route, tid, "signed", hmac_secret_env=self._env_var)
            resp = client.post(f"/v1/console/webhook/{tid}/signed", content=b"{}")
            self.assertEqual(resp.status_code, 401, resp.text)

    def test_wrong_signature_rejected(self):
        with _sandbox(Path(self._tmp)) as (client, home, tid, webhooks_route):
            _register_channel(webhooks_route, tid, "signed", hmac_secret_env=self._env_var)
            resp = client.post(
                f"/v1/console/webhook/{tid}/signed",
                content=b'{"a": 1}',
                headers={"X-Hub-Signature-256": "sha256=deadbeef"},
            )
            self.assertEqual(resp.status_code, 401, resp.text)

    def test_correct_signature_accepted(self):
        with _sandbox(Path(self._tmp)) as (client, home, tid, webhooks_route):
            _register_channel(webhooks_route, tid, "signed", hmac_secret_env=self._env_var)
            payload = b'{"a": 1}'
            resp = client.post(
                f"/v1/console/webhook/{tid}/signed",
                content=payload,
                headers={"X-Hub-Signature-256": self._sign(payload)},
            )
            self.assertEqual(resp.status_code, 200, resp.text)

    def test_oversized_body_is_fully_buffered_even_when_hmac_verification_fails(self):
        """BLIND SPOT (compounding the missing cap): `body_bytes =
        await request.body()` (L212) runs unconditionally, *before* the HMAC
        branch (L215+). So even a channel that requires a signature still
        pays the full in-memory buffering cost for an oversized body -- the
        eventual 401 for a bad/missing signature does not save the process
        from having read the whole thing into memory first."""
        with _sandbox(Path(self._tmp)) as (client, home, tid, webhooks_route):
            _register_channel(webhooks_route, tid, "signed", hmac_secret_env=self._env_var)
            oversized = b"b" * (4 * 1024 * 1024)
            resp = client.post(
                f"/v1/console/webhook/{tid}/signed",
                content=oversized,
                headers={"X-Hub-Signature-256": "sha256=deadbeef"},
            )
            # Rejected for a bad signature -- but only *after* full buffering;
            # there is no early-exit/size-limited read anywhere in the path.
            self.assertEqual(resp.status_code, 401, resp.text)

    def test_missing_vault_secret_returns_503(self):
        with _sandbox(Path(self._tmp)) as (client, home, tid, webhooks_route):
            _register_channel(webhooks_route, tid, "misconfigured",
                               hmac_secret_env="NO_SUCH_ENV_VAR_SET")
            resp = client.post(f"/v1/console/webhook/{tid}/misconfigured", content=b"{}")
            self.assertEqual(resp.status_code, 503, resp.text)


class TestReceiveWebhookAudit(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp()

    def tearDown(self):
        import shutil
        shutil.rmtree(self._tmp, ignore_errors=True)

    def test_successful_receipt_writes_metadata_only_audit_event(self):
        """GDPR Art. 5 convention check: the audit event must record
        metadata (channel_id, payload_size, has_signature) but never the
        raw payload content itself."""
        with _sandbox(Path(self._tmp)) as (client, home, tid, webhooks_route):
            _register_channel(webhooks_route, tid, "open-channel")
            secret_payload = b'{"super_secret_field": "should-not-be-logged"}'
            resp = client.post(f"/v1/console/webhook/{tid}/open-channel", content=secret_payload)
            self.assertEqual(resp.status_code, 200, resp.text)

            chain_path = home / "tenants" / tid / "global" / "forge" / "audit.jsonl"
            self.assertTrue(chain_path.exists(), "expected the audit chain file to be written")
            lines = [json.loads(l) for l in chain_path.read_text(encoding="utf-8").splitlines() if l.strip()]
            events = [rec for rec in lines if rec.get("event_type") == "webhook.message_received"]
            self.assertTrue(events, "expected a webhook.message_received audit event")
            details = events[-1]["details"]
            self.assertEqual(details["channel_id"], "open-channel")
            self.assertEqual(details["payload_size"], len(secret_payload))
            self.assertNotIn("super_secret_field", json.dumps(details))
            self.assertNotIn("payload", details)


if __name__ == "__main__":
    unittest.main()
