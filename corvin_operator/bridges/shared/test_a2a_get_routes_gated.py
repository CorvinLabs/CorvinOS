"""SRF-7 (review 2026-11-05): the A2A listener's GET routes must not disclose state.

* every GET answers 403 ``license_required`` on a network-denied (Free) install,
  exactly like the POST routes;
* ``/healthz`` is content-free (no instance id);
* ``/v1/a2a/audit-head`` answers only a request signed with a paired origin's
  hmac_key over a fresh (issued_at, nonce) - anything else is one opaque 403.

Driven over real loopback HTTP against ``build_server``.
"""
from __future__ import annotations

import json
import os
import secrets
import sys
import tempfile
import threading
import time
import unittest
import unittest.mock as mock
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import a2a_audit_head as ah  # noqa: E402
import a2a_http_server as hs  # noqa: E402
import remote_trigger_receiver as rtr  # noqa: E402


class _SE:
    def write_event(self, path, event_type, **kw):
        return {"hash": "x"}

    def get_audit_chain_tail(self, path):
        return ""


def _get(url: str) -> tuple[int, bytes]:
    try:
        with urllib.request.urlopen(url, timeout=5) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


class TestA2AGetRoutes(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="a2a-get-")
        self.home = Path(self._tmp.name)
        chain = self.home / "chain.jsonl"
        chain.write_text(json.dumps({"hash": "ab" * 32, "ts": 1730000000.0}) + "\n",
                         encoding="utf-8")
        self._env = mock.patch.dict(os.environ, {
            "CORVIN_HOME": str(self.home),
            "VOICE_AUDIT_PATH": str(chain),
            "CORVIN_A2A_ATTESTATION_DISABLED": "1",
        })
        self._env.start()
        os.environ.pop("REMOTE_ORIGINS_DIR", None)
        self.origins = self.home / "origins"
        self.origins.mkdir()
        self.hk = secrets.token_hex(32)
        p = self.origins / "peer1.json"
        p.write_text(json.dumps({"enabled": True, "hmac_key": self.hk,
                                 "recv_key": secrets.token_hex(32)}), encoding="utf-8")
        p.chmod(0o600)
        self.srv = hs.build_server(origins_dir=self.origins, nonce_store=rtr.NonceStore(),
                                   forge_se=_SE(), instance_id="inst-PSEUDONYM-123",
                                   force_m1_only=True)
        self.denied = False
        recv = self.srv.RequestHandlerClass.receiver
        self._deny = mock.patch.object(type(recv), "_network_denied",
                                       lambda _self: self.denied)
        self._deny.start()
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.srv.server_address[1]}"

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()
        self._deny.stop()
        self._env.stop()
        self._tmp.cleanup()

    def _head_url(self, **override) -> str:
        params = ah.sign_head_request("peer1", self.hk)
        params.update(override)
        return f"{self.base}/v1/a2a/audit-head?{urllib.parse.urlencode(params)}"

    def test_healthz_answers_without_the_instance_id(self):
        code, body = _get(self.base + "/healthz")
        self.assertEqual(code, 200)
        self.assertEqual(json.loads(body), {"ok": True})
        self.assertNotIn(b"inst-PSEUDONYM", body)

    def test_network_denied_install_refuses_every_get(self):
        self.denied = True
        for path in ("/healthz", "/metrics", "/v1/a2a/audit-head"):
            code, body = _get(self.base + path)
            self.assertEqual(code, 403, path)
            self.assertIn(b"license_required", body)
        code, _ = _get(self._head_url())
        self.assertEqual(code, 403)

    def test_unsigned_audit_head_request_is_refused(self):
        code, body = _get(self.base + "/v1/a2a/audit-head")
        self.assertEqual(code, 403)
        self.assertNotIn(b"chain_head", body)
        code, _ = _get(self.base + "/v1/a2a/audit-head?origin_id=peer1")
        self.assertEqual(code, 403)

    def test_wrong_key_unknown_origin_and_stale_requests_are_refused_alike(self):
        bad_key = ah.sign_head_request("peer1", secrets.token_hex(32))
        unknown = ah.sign_head_request("nobody", self.hk)
        stale = ah.sign_head_request("peer1", self.hk, issued_at=int(time.time()) - 3600)
        for params in (bad_key, unknown, stale):
            url = f"{self.base}/v1/a2a/audit-head?{urllib.parse.urlencode(params)}"
            code, body = _get(url)
            self.assertEqual(code, 403, params)
            self.assertEqual(json.loads(body), {"reason": "audit_head_rejected"})

    def test_signed_request_from_a_paired_origin_gets_the_head(self):
        code, body = _get(self._head_url())
        self.assertEqual(code, 200, body)
        head = json.loads(body)
        self.assertEqual(head["chain_head"], "ab" * 32)
        self.assertEqual(len(head["signature"]), 64)

    # ── refutation round 2026-11-05 (RC-4, RC-7) ─────────────────────────

    def test_signed_audit_head_request_is_not_replayable(self):
        url = self._head_url()
        code, _ = _get(url)
        self.assertEqual(code, 200)
        for _ in range(2):  # the very same URL, e.g. lifted from a proxy log
            code, body = _get(url)
            self.assertEqual(code, 403, body)
            self.assertEqual(json.loads(body), {"reason": "audit_head_rejected"})
        code, _ = _get(self._head_url())  # a fresh nonce still works
        self.assertEqual(code, 200)

    def test_pairing_revoked_in_the_network_manifest_is_refused(self):
        p = self.origins / "peer1.json"
        cfg = json.loads(p.read_text(encoding="utf-8"))
        cfg["pairing_id"] = "pair-123"
        p.write_text(json.dumps(cfg), encoding="utf-8")
        p.chmod(0o600)
        import a2a_manifest

        revoked = a2a_manifest.A2AManifest({"revoked_pairing_ids": ["pair-123"]})
        with mock.patch.object(a2a_manifest, "load_manifest", lambda **kw: revoked):
            code, body = _get(self._head_url())
        self.assertEqual(code, 403, body)
        clean = a2a_manifest.A2AManifest({})
        with mock.patch.object(a2a_manifest, "load_manifest", lambda **kw: clean):
            code, body = _get(self._head_url())
        self.assertEqual(code, 200, body)

    def test_metrics_refuses_a_dns_rebound_host_even_from_a_loopback_peer(self):
        port = self.srv.server_address[1]
        for host in (f"evil.example:{port}", "evil.example", "localhost.evil.example"):
            req = urllib.request.Request(self.base + "/metrics", headers={"Host": host})
            try:
                with urllib.request.urlopen(req, timeout=5) as r:
                    code = r.status
            except urllib.error.HTTPError as e:
                code = e.code
            self.assertEqual(code, 404, host)
        # the scrape target itself (loopback Host) is still served (200, or 503
        # when prometheus_client is not installed) - never refused as not_found
        for host in (f"127.0.0.1:{port}", f"localhost:{port}"):
            req = urllib.request.Request(self.base + "/metrics", headers={"Host": host})
            try:
                with urllib.request.urlopen(req, timeout=5) as r:
                    code = r.status
            except urllib.error.HTTPError as e:
                code = e.code
            self.assertIn(code, (200, 503), host)

    def test_signed_post_routes_carry_no_host_check(self):
        # A paired peer reaches the listener by DNS name: the signed POST route
        # answers a foreign Host exactly like a loopback one (judged by the body
        # and its signature only).
        def post(host):
            body = json.dumps({"origin_id": "nobody"}).encode()
            req = urllib.request.Request(self.base + "/v1/a2a/ping", data=body, method="POST",
                                         headers={"Host": host,
                                                  "Content-Type": "application/json"})
            try:
                with urllib.request.urlopen(req, timeout=5) as r:
                    return r.status, r.read()
            except urllib.error.HTTPError as e:
                return e.code, e.read()

        port = self.srv.server_address[1]
        self.assertEqual(post("peer.example.net"), post(f"127.0.0.1:{port}"))


if __name__ == "__main__":
    unittest.main()
