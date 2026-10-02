"""``corvin-id maintain`` as a real subprocess against a local Corvin-Features stub.

ADR-2099 P0 facts 4 + 5: the A2A receive path reads the revocation list from
cache only, so this daily job (corvin-ibc-maintain.timer) is what keeps that
cache current, and it renews the instance's IBC before it expires. Also proves
both outcomes reach the hash-chained audit trail: until 2026-10-01 every IBC
audit call imported a class that does not exist and wrote nothing.

Run: ``pytest corvin_operator/bridges/shared/test_ibc_maintain_e2e.py -v``
"""
from __future__ import annotations

import base64
import json
import os
import secrets
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
CLI = HERE / "corvin_id_cli.py"
DAY = 24 * 3600


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _claims(token: str) -> dict:
    payload = token[len("CORVIN-"):].split(".")[1]
    return json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))


def _ibc(signer: Ed25519PrivateKey, *, sub: str, pubkey: str, exp: float) -> str:
    h = _b64url(json.dumps({"alg": "EdDSA", "kid": "ibc-test"}).encode())
    p = _b64url(json.dumps({
        "type": "instance_binding", "iss": "corvinlabs.io", "sub": sub,
        "jti": "ibc-" + secrets.token_hex(8), "instance_pubkey": pubkey, "exp": int(exp),
    }).encode())
    return f"CORVIN-{h}.{p}.{_b64url(signer.sign(f'{h}.{p}'.encode()))}"


class _Stub:
    """Minimal Corvin-Features: GET /v1/instance/revoked, POST /v1/instance/bind."""

    def __init__(self, signer: Ed25519PrivateKey, revoked: list[str]):
        self.signer = signer
        self.revoked = revoked
        self.crl_status = 200
        self.binds: list[dict] = []
        stub = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _json(self, code, body):
                data = json.dumps(body).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                if self.path != "/v1/instance/revoked":
                    return self._json(404, {})
                if stub.crl_status != 200:
                    return self._json(stub.crl_status, {"detail": "down"})
                self._json(200, {"revoked_jti": stub.revoked})

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                if self.path != "/v1/instance/bind" or not self.headers.get("Authorization"):
                    return self._json(401, {})
                stub.binds.append(body)
                self._json(200, {"ibc": _ibc(stub.signer, sub=body["instance_id"],
                                             pubkey=body["instance_pubkey"],
                                             exp=time.time() + 365 * DAY)})

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"


class TestIbcMaintain(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        t = Path(self._tmp.name)
        self.trust = Ed25519PrivateKey.generate()
        self.stub = _Stub(self.trust, revoked=["ibc-revoked-1"])
        self.addCleanup(self.stub.server.shutdown)
        der = self.trust.public_key().public_bytes(
            serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
        (t / "xdg" / "corvin-voice").mkdir(parents=True)
        features = t / "xdg" / "corvin-voice" / "features.json"
        features.write_text(json.dumps({"api_key": "k" * 32, "token_fp": "fp"}))
        features.chmod(0o600)
        self.home = t / "home"
        self.cert = t / "id" / "instance_cert.jwt"
        self.crl = t / "crl_cache.json"
        self.cert.parent.mkdir()
        self.env = {
            **{k: v for k, v in os.environ.items() if not k.startswith("CORVIN_")},
            "CORVIN_HOME": str(self.home),
            "XDG_CONFIG_HOME": str(t / "xdg"),
            "CORVIN_AUDIT_ANCHOR_KEY": str(t / "anchor.key"),
            "FORGE_ROOT": str(t / "forge"),
            "CORVIN_TEST_MODE": "1",
            "CORVIN_FEATURES_URL": self.stub.url,
            "CORVIN_IBC_PUBKEY_DER_B64": base64.b64encode(der).decode(),
            "CORVIN_LICENSE_KEY": "test-license-token",
            "CORVIN_INSTANCE_CERT_PATH": str(self.cert),
            "CORVIN_INSTANCE_KEY_PATH": str(t / "id" / "instance_key.pem"),
            "CORVIN_CRL_CACHE_PATH": str(self.crl),
        }
        self.chain = t / "forge" / "audit.jsonl"
        # audit.audit_path(): VOICE_AUDIT_PATH > FORGE_ROOT; this package's
        # conftest sets the former per test, so pin it to the same file.
        self.env["VOICE_AUDIT_PATH"] = str(self.chain)

    def _bind(self, exp: float) -> str:
        token = _ibc(self.trust, sub="iid-test", pubkey="AAAA", exp=exp)
        self.cert.write_text(token)
        self.cert.chmod(0o600)
        return token

    def _maintain(self) -> subprocess.CompletedProcess:
        return subprocess.run([sys.executable, str(CLI), "maintain"], cwd=HERE, env=self.env,
                              capture_output=True, text=True, timeout=120)

    def _events(self) -> list[str]:
        if not self.chain.exists():
            return []
        return [json.loads(line)["event_type"] for line in self.chain.read_text().splitlines()]

    def test_expiring_ibc_is_renewed_and_crl_cached(self):
        old = self._bind(time.time() + 5 * DAY)
        r = self._maintain()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("IBC: renewed", r.stdout)
        self.assertEqual(len(self.stub.binds), 1)
        new = self.cert.read_text().strip()
        self.assertNotEqual(new, old)
        self.assertGreater(_claims(new)["exp"], time.time() + 300 * DAY)
        self.assertEqual(json.loads(self.crl.read_text())["revoked_jti"], ["ibc-revoked-1"])
        events = self._events()
        self.assertIn("instance.crl_refreshed", events)
        self.assertIn("instance.ibc_issued", events)

    def test_fresh_ibc_is_left_alone(self):
        token = self._bind(time.time() + 200 * DAY)
        r = self._maintain()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("IBC: fresh", r.stdout)
        self.assertEqual(self.stub.binds, [])
        self.assertEqual(self.cert.read_text().strip(), token)

    def test_unbound_instance_only_refreshes_the_list(self):
        r = self._maintain()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("IBC: unbound", r.stdout)
        self.assertEqual(self.stub.binds, [])
        self.assertTrue(self.crl.exists())

    def test_failed_fetch_exits_1_keeps_cache_and_is_audited(self):
        self.crl.write_text(json.dumps({"fetched_at": 1, "revoked_jti": ["keep-me"]}))
        self.stub.crl_status = 503
        r = self._maintain()
        self.assertEqual(r.returncode, 1)
        self.assertEqual(json.loads(self.crl.read_text())["revoked_jti"], ["keep-me"])
        self.assertIn("instance.crl_refresh_failed", self._events())

    def test_audit_chain_verifies(self):
        self._bind(time.time() + 5 * DAY)
        self.assertEqual(self._maintain().returncode, 0)
        self.assertGreaterEqual(len(self._events()), 2, "nothing reached the chain")
        out = subprocess.run(
            [sys.executable, "-c",
             "import sys; from pathlib import Path; from forge import security_events as se; "
             "ok, errs = se.verify_chain(Path(sys.argv[1])); print(ok, errs); sys.exit(0 if ok else 1)",
             str(self.chain)],
            env={**self.env, "PYTHONPATH": str(REPO / "corvin_operator" / "forge")},
            capture_output=True, text=True, timeout=60)
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)


if __name__ == "__main__":
    unittest.main()
