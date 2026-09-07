"""R2-C3 + R2-C5 (adversarial review round 2, 2026-09-07) — inbound webhooks.

Round 1 documented three gaps in ``routes/webhooks.py`` as *blind spots*
(see ``test_webhook_route.py``); this file is the regression suite for the
fixes, driven through the REAL HTTP boundary with FastAPI's TestClient:

  * R2-C3a  a channel may no longer be registered WITHOUT ``hmac_secret_env``
            — an unauthenticated POST target that writes to the tenant's audit
            chain is an anonymous chain writer. Registration 400s; a legacy
            manifest written before the fix is refused at receive time (503),
            never accepted unauthenticated.
  * R2-C3b  ``rate_limit_per_hour`` is ENFORCED (429), not merely persisted.
  * R2-C3c  the body is CAPPED (413) via Content-Length and while streaming,
            instead of being buffered without limit.
  * R2-C5   a malformed tenant_id (``__evil``) 404s instead of raising
            ``InvalidTenantID`` into a 500 for an unauthenticated caller.

The admin routes need a real session + CSRF; the inbound route deliberately
needs neither.
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

for _p in [str(_OPERATOR), str(_OPERATOR / "license"), str(_OPERATOR / "forge"),
           str(_CONSOLE), str(_BRIDGES_SHARED)]:
    if _p not in sys.path:
        sys.path.insert(0, _p)


def _reset_modules():
    for key in list(sys.modules):
        if any(key.startswith(p) for p in ("corvin_console", "corvin_gateway", "forge")):
            del sys.modules[key]


@contextmanager
def _sandbox(tmp_path: Path, tenant_id: str = "_default"):
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
        from corvin_console import auth as _auth
        from corvin_console.app import router
        from corvin_console.routes import webhooks as webhooks_route
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        webhooks_route._rate_hits.clear()  # per-process window must not leak between tests

        rec = _auth.create_session(tenant_id=tenant_id, token_fingerprint="test-fp")
        csrf = _auth.derive_csrf_token(rec.csrf_secret, rec.sid)

        app = FastAPI()
        app.include_router(router, prefix="/v1/console")
        client = TestClient(app, raise_server_exceptions=False)
        admin = TestClient(app, raise_server_exceptions=False)
        admin.cookies.set("corvin_console_sid", rec.sid)
        admin.headers.update({"X-CSRF-Token": csrf})
        yield client, admin, home, tenant_id, webhooks_route
    finally:
        for k, v in prev.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        _reset_modules()


def _legacy_channel(webhooks_route, tenant_id, channel_id, *, hmac_secret_env=None,
                    rate_limit_per_hour=60):
    """Write a manifest directly — used to simulate a channel persisted BEFORE
    the mandatory-HMAC rule, and to set a rate limit without a second route."""
    webhooks_route._write_channel(tenant_id, channel_id, {
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
    })


ENV_VAR = "TEST_WEBHOOK_HMAC_SECRET_R2"
SECRET = b"s3cr3t"


def _sign(body: bytes) -> str:
    return "sha256=" + hmac.new(SECRET, body, hashlib.sha256).hexdigest()


class WebhookHardeningTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self._prev = os.environ.get(ENV_VAR)
        os.environ[ENV_VAR] = SECRET.decode()

    def tearDown(self):
        import shutil
        shutil.rmtree(self._tmp, ignore_errors=True)
        if self._prev is None:
            os.environ.pop(ENV_VAR, None)
        else:
            os.environ[ENV_VAR] = self._prev

    # ── R2-C3a: HMAC required at registration ────────────────────────────────

    def test_registration_without_hmac_secret_env_is_refused(self):
        with _sandbox(Path(self._tmp)) as (client, admin, home, tid, wh):
            resp = admin.put(
                "/v1/console/bridges/custom/open-channel",
                json={"display_name": "Open"},
            )
            self.assertEqual(resp.status_code, 400, resp.text)
            self.assertIn("hmac_secret_env", resp.text)
            # nothing persisted → the inbound URL is not live
            self.assertIsNone(wh._load_channel(tid, "open-channel"))
            self.assertEqual(
                client.post(f"/v1/console/webhook/{tid}/open-channel", content=b"{}").status_code,
                404,
            )

    def test_registration_with_a_bogus_env_name_is_refused(self):
        with _sandbox(Path(self._tmp)) as (client, admin, home, tid, wh):
            for bad in ("", "   ", "not a var", "9LEADING_DIGIT", "x" * 200):
                resp = admin.put(
                    "/v1/console/bridges/custom/c1",
                    json={"display_name": "C1", "hmac_secret_env": bad},
                )
                self.assertEqual(resp.status_code, 400, f"{bad!r}: {resp.text}")

    def test_registration_with_hmac_secret_env_works_and_is_enforced(self):
        with _sandbox(Path(self._tmp)) as (client, admin, home, tid, wh):
            resp = admin.put(
                "/v1/console/bridges/custom/signed",
                json={"display_name": "Signed", "hmac_secret_env": ENV_VAR},
            )
            self.assertEqual(resp.status_code, 200, resp.text)
            payload = b'{"a": 1}'
            self.assertEqual(
                client.post(f"/v1/console/webhook/{tid}/signed", content=payload).status_code,
                401, "unsigned POST accepted",
            )
            ok = client.post(f"/v1/console/webhook/{tid}/signed", content=payload,
                             headers={"X-Hub-Signature-256": _sign(payload)})
            self.assertEqual(ok.status_code, 200, ok.text)
            # the secret is never echoed back by the admin listing
            listing = admin.get("/v1/console/bridges/custom").json()
            self.assertNotIn(SECRET.decode(), json.dumps(listing))

    def test_legacy_channel_without_secret_is_refused_at_receive_time(self):
        """A manifest persisted before the rule must NOT stay an anonymous
        writer — receive fails closed (503) instead of accepting it."""
        with _sandbox(Path(self._tmp)) as (client, admin, home, tid, wh):
            _legacy_channel(wh, tid, "legacy-open", hmac_secret_env=None)
            resp = client.post(f"/v1/console/webhook/{tid}/legacy-open", content=b"{}")
            self.assertEqual(resp.status_code, 503, resp.text)
            chain = home / "tenants" / tid / "global" / "forge" / "audit.jsonl"
            events = []
            if chain.exists():
                events = [json.loads(x) for x in chain.read_text(encoding="utf-8").splitlines() if x.strip()]
            self.assertEqual(
                [e for e in events if e.get("event_type") == "webhook.message_received"], [],
                "an unauthenticated POST still wrote to the audit chain",
            )

    # ── R2-C3b: the stored rate limit is enforced ────────────────────────────

    def test_rate_limit_per_hour_is_enforced(self):
        with _sandbox(Path(self._tmp)) as (client, admin, home, tid, wh):
            _legacy_channel(wh, tid, "throttled", hmac_secret_env=ENV_VAR,
                            rate_limit_per_hour=3)
            body = b"{}"
            sig = {"X-Hub-Signature-256": _sign(body)}
            codes = [client.post(f"/v1/console/webhook/{tid}/throttled",
                                 content=body, headers=sig).status_code
                     for _ in range(6)]
            self.assertEqual(codes[:3], [200, 200, 200], codes)
            self.assertEqual(codes[3:], [429, 429, 429], codes)

    def test_rate_limit_is_per_channel_and_per_tenant(self):
        with _sandbox(Path(self._tmp)) as (client, admin, home, tid, wh):
            _legacy_channel(wh, tid, "a", hmac_secret_env=ENV_VAR, rate_limit_per_hour=1)
            _legacy_channel(wh, tid, "b", hmac_secret_env=ENV_VAR, rate_limit_per_hour=1)
            body = b"{}"
            sig = {"X-Hub-Signature-256": _sign(body)}
            self.assertEqual(client.post(f"/v1/console/webhook/{tid}/a", content=body, headers=sig).status_code, 200)
            self.assertEqual(client.post(f"/v1/console/webhook/{tid}/a", content=body, headers=sig).status_code, 429)
            # a different channel has its own budget
            self.assertEqual(client.post(f"/v1/console/webhook/{tid}/b", content=body, headers=sig).status_code, 200)

    def test_throttled_request_is_refused_before_hmac_and_before_audit(self):
        """The 429 must be cheap: no body buffering, no chain write."""
        with _sandbox(Path(self._tmp)) as (client, admin, home, tid, wh):
            _legacy_channel(wh, tid, "cheap", hmac_secret_env=ENV_VAR, rate_limit_per_hour=1)
            body = b"{}"
            client.post(f"/v1/console/webhook/{tid}/cheap", content=body,
                        headers={"X-Hub-Signature-256": _sign(body)})
            chain = home / "tenants" / tid / "global" / "forge" / "audit.jsonl"
            before = len(chain.read_text(encoding="utf-8").splitlines()) if chain.exists() else 0
            resp = client.post(f"/v1/console/webhook/{tid}/cheap",
                               content=b"x" * (8 * 1024 * 1024))  # oversized AND unsigned
            self.assertEqual(resp.status_code, 429, resp.text)
            after = len(chain.read_text(encoding="utf-8").splitlines()) if chain.exists() else 0
            self.assertEqual(before, after, "a throttled request still wrote to the chain")

    # ── R2-C3c: the body is capped ───────────────────────────────────────────

    def test_oversized_body_is_rejected_with_413(self):
        with _sandbox(Path(self._tmp)) as (client, admin, home, tid, wh):
            _legacy_channel(wh, tid, "signed", hmac_secret_env=ENV_VAR,
                            rate_limit_per_hour=10_000)
            cap = wh._MAX_WEBHOOK_BODY_BYTES
            oversized = b"a" * (cap + 1)
            resp = client.post(f"/v1/console/webhook/{tid}/signed", content=oversized,
                               headers={"X-Hub-Signature-256": _sign(oversized)})
            self.assertEqual(resp.status_code, 413, resp.text)
            # exactly at the cap still works
            at_cap = b"b" * cap
            ok = client.post(f"/v1/console/webhook/{tid}/signed", content=at_cap,
                             headers={"X-Hub-Signature-256": _sign(at_cap)})
            self.assertEqual(ok.status_code, 200, ok.text)

    def test_oversized_chunked_body_without_content_length_is_rejected(self):
        """A streamed body carries no Content-Length — the cap must still hold
        while the chunks are read."""
        with _sandbox(Path(self._tmp)) as (client, admin, home, tid, wh):
            _legacy_channel(wh, tid, "signed", hmac_secret_env=ENV_VAR,
                            rate_limit_per_hour=10_000)
            cap = wh._MAX_WEBHOOK_BODY_BYTES

            def _gen():
                for _ in range((cap // 1024) + 8):
                    yield b"c" * 1024

            resp = client.post(f"/v1/console/webhook/{tid}/signed", content=_gen(),
                               headers={"X-Hub-Signature-256": "sha256=deadbeef"})
            self.assertEqual(resp.status_code, 413, resp.text)

    # ── R2-C5: malformed tenant ids 404 instead of 500 ───────────────────────

    def test_malformed_tenant_id_returns_404_not_500(self):
        with _sandbox(Path(self._tmp)) as (client, admin, home, tid, wh):
            _legacy_channel(wh, tid, "openchan", hmac_secret_env=ENV_VAR)
            for bad_tenant in ("__evil", "..", ".", "a" * 300, "has space", "a/b"):
                resp = client.post(f"/v1/console/webhook/{bad_tenant}/openchan", content=b"{}")
                self.assertEqual(resp.status_code, 404, f"{bad_tenant!r}: {resp.status_code} {resp.text[:200]}")

    def test_malformed_channel_id_returns_404(self):
        with _sandbox(Path(self._tmp)) as (client, admin, home, tid, wh):
            for bad_channel in ("../../etc/passwd", "UPPER", "x" * 200):
                resp = client.post(f"/v1/console/webhook/{tid}/{bad_channel}", content=b"{}")
                self.assertEqual(resp.status_code, 404, f"{bad_channel!r}: {resp.status_code}")


if __name__ == "__main__":
    unittest.main()
