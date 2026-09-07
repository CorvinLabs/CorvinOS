"""HTTP route-level tests for the inbound webhook receiver (ADR-0124 M7).

Target: corvin_console/routes/webhooks.py :: receive_webhook()
  POST /webhook/{tenant_id}/{channel_id}

This endpoint is deliberately session-less ("No session required" — see the
route docstring) so external systems can post to it directly. If the operator
UPDATED 2026-09-07 (R2-C3): the three gaps this file originally *documented*
as blind spots — a signature-less channel being a fully unauthenticated POST
target, ``rate_limit_per_hour`` persisted but never read, and an uncapped
``await request.body()`` — are FIXED in ``routes/webhooks.py``. The tests that
asserted the vulnerable behaviour are inverted here rather than deleted, so
this file keeps covering the same real HTTP paths through the TestClient
(mirrors the ``_sandbox`` pattern from test_instance_route.py /
test_license_http_gates.py). The dedicated regression suite for the fixes is
``test_webhook_hardening_r2.py``.
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

    def test_no_hmac_channel_is_refused_not_reachable_with_zero_auth(self):
        """R2-C3 (was: "documents the endpoint's designed exposure"). A channel
        persisted without ``hmac_secret_env`` is no longer an anonymous writer
        to the audit chain: the receiver fails closed with 503 and the operator
        must re-register the channel with a secret."""
        with _sandbox(Path(self._tmp)) as (client, home, tid, webhooks_route):
            webhooks_route._rate_hits.clear()
            _register_channel(webhooks_route, tid, "open-channel")
            resp = client.post(
                f"/v1/console/webhook/{tid}/open-channel",
                content=b'{"hello": "world"}',
                headers={"content-type": "application/json"},
            )
            self.assertEqual(resp.status_code, 503, resp.text)

    def test_unknown_channel_returns_404_before_any_processing(self):
        with _sandbox(Path(self._tmp)) as (client, home, tid, webhooks_route):
            resp = client.post(
                f"/v1/console/webhook/{tid}/does-not-exist",
                content=b"x" * 1024,
            )
            self.assertEqual(resp.status_code, 404, resp.text)

    def test_oversized_body_is_capped_like_memory_route(self):
        """R2-C3 (was: BLIND SPOT — no size cap). The receiver now enforces the
        same order of cap as routes/memory.py's ``_MAX_BODY_BYTES``, so an
        oversized body is refused rather than buffered. (A signature-less
        channel is refused before the cap even matters — see the 503 test — so
        the cap itself is exercised in test_webhook_hardening_r2.py.)"""
        with _sandbox(Path(self._tmp)) as (client, home, tid, webhooks_route):
            _register_channel(webhooks_route, tid, "open-channel")
            self.assertLessEqual(webhooks_route._MAX_WEBHOOK_BODY_BYTES, 1024 * 1024)
            oversized = b"a" * (4 * 1024 * 1024)
            resp = client.post(
                f"/v1/console/webhook/{tid}/open-channel",
                content=oversized,
                headers={"content-type": "application/octet-stream"},
            )
            self.assertNotEqual(resp.status_code, 200, resp.text)

    def test_rate_limit_per_hour_field_is_enforced(self):
        """R2-C3 (was: BLIND SPOT — the field was decorative). The stored limit
        is now read back and enforced with a 429, checked before the body is
        read. Signature handling is orthogonal, so this asserts the limit fires
        regardless of the (here unset) secret."""
        with _sandbox(Path(self._tmp)) as (client, home, tid, webhooks_route):
            webhooks_route._rate_hits.clear()
            _register_channel(webhooks_route, tid, "throttled",
                              hmac_secret_env="NO_SUCH_ENV_VAR_SET", rate_limit_per_hour=1)
            first = client.post(f"/v1/console/webhook/{tid}/throttled", content=b"{}")
            self.assertEqual(first.status_code, 503, first.text)  # consumed the one hit
            for _ in range(3):
                resp = client.post(f"/v1/console/webhook/{tid}/throttled", content=b"{}")
                self.assertEqual(resp.status_code, 429, resp.text)


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

    def test_oversized_body_is_refused_before_hmac_verification(self):
        """R2-C3 (was: BLIND SPOT — full buffering before the 401). The capped
        read aborts as soon as the limit is passed, so an oversized body is 413
        and never reaches the signature compare."""
        with _sandbox(Path(self._tmp)) as (client, home, tid, webhooks_route):
            webhooks_route._rate_hits.clear()
            _register_channel(webhooks_route, tid, "signed", hmac_secret_env=self._env_var,
                              rate_limit_per_hour=10_000)
            oversized = b"b" * (4 * 1024 * 1024)
            resp = client.post(
                f"/v1/console/webhook/{tid}/signed",
                content=oversized,
                headers={"X-Hub-Signature-256": "sha256=deadbeef"},
            )
            self.assertEqual(resp.status_code, 413, resp.text)

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
        env_var = "TEST_WEBHOOK_AUDIT_SECRET"
        prev = os.environ.get(env_var)
        os.environ[env_var] = "s3cr3t"
        try:
            self._audit_case(env_var)
        finally:
            if prev is None:
                os.environ.pop(env_var, None)
            else:
                os.environ[env_var] = prev

    def _audit_case(self, env_var: str):
        with _sandbox(Path(self._tmp)) as (client, home, tid, webhooks_route):
            webhooks_route._rate_hits.clear()
            _register_channel(webhooks_route, tid, "open-channel", hmac_secret_env=env_var)
            secret_payload = b'{"super_secret_field": "should-not-be-logged"}'
            sig = "sha256=" + hmac.new(b"s3cr3t", secret_payload, hashlib.sha256).hexdigest()
            resp = client.post(f"/v1/console/webhook/{tid}/open-channel",
                               content=secret_payload,
                               headers={"X-Hub-Signature-256": sig})
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
