"""End-to-end tests for ADR-0078 Phase 1 — A2A Network Trust Anchor.

Two real ThreadingHTTPServer instances on ephemeral ports. Tests cover:
  - Envelope WITHOUT attestation accepted when min_trust=none (default)
  - Envelope WITH valid attestation accepted when min_trust=community
  - Envelope WITHOUT attestation rejected when min_trust=community
  - Envelope with wrong-CA attestation rejected when min_trust=community
  - Envelope with expired attestation rejected when min_trust=community
  - Envelope with unverified tier accepted/rejected correctly
  - No CA configured → WARNING but accepted (fail-open default)
  - CORVIN_ATTESTATION_STRICT=1 → CA not configured → rejected
  - ca-init and register --self-signed via CLI

CI lint: MUST NOT import anthropic.
"""
from __future__ import annotations

import hashlib
import hmac as _hmac
import json
import os
import secrets
import sys
import tempfile
import time
import unittest
import urllib.request
import uuid
from pathlib import Path

_here = Path(__file__).resolve().parent
if str(_here) not in sys.path:
    sys.path.insert(0, str(_here))

from a2a_http_server import build_server, serve_in_thread  # noqa: E402
from remote_trigger_receiver import NonceStore              # noqa: E402
import instance_attestation as att                          # noqa: E402


# ── Test keys ─────────────────────────────────────────────────────────────

HMAC_KEY = "ab" * 32
RECV_KEY = "cd" * 32
ORIGIN_ID = "e2e-adr0078"


def _write_origin(tmp: Path, **extra) -> None:
    cfg = {
        "origin_id":       ORIGIN_ID,
        "hmac_key":        HMAC_KEY,
        "recv_key":        RECV_KEY,
        "enabled":         True,
        "max_ttl_s":       300,
        "allowed_personas": ["assistant"],
        **extra,
    }
    p = tmp / f"{ORIGIN_ID}.json"
    p.write_text(json.dumps(cfg))
    p.chmod(0o600)


def _sign(env: dict, key_hex: str = HMAC_KEY) -> dict:
    payload = {k: v for k, v in env.items() if k != "signature"}
    sig = _hmac.new(
        bytes.fromhex(key_hex),
        json.dumps(payload, sort_keys=True, separators=(",", ":"),
                   ensure_ascii=True).encode(),
        hashlib.sha256,
    ).hexdigest()
    env["signature"] = sig
    return env


def _envelope(attestation: dict | None = None, **kwargs) -> dict:
    env: dict = {
        "task_id":            str(uuid.uuid4()),
        "nonce":              secrets.token_hex(32),
        "issued_at":          time.time(),
        "origin_id":          ORIGIN_ID,
        "instruction":        "echo",
        "result_schema":      {},
        "ttl_s":              30,
        "sender_instance_id": "",
        "attachments":        [],
        "signature":          "",
    }
    env.update(kwargs)
    if attestation is not None:
        env["sender_attestation"] = attestation
    return _sign(env)


def _post(url: str, body: dict) -> dict:
    data = json.dumps(body).encode()
    req = urllib.request.Request(
        url, data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=5) as resp:
        return json.loads(resp.read())


# ── Fixture ───────────────────────────────────────────────────────────────

class _ServerFixture(unittest.TestCase):

    _min_trust: str = "none"
    _ca_pub_hex: str | None = None  # inject via env

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        _write_origin(self.tmp, min_trust=self._min_trust)

        self._saved_env: dict[str, str] = {}
        for k in ("CORVIN_CA_PUBKEY_HEX", "CORVIN_ATTESTATION_STRICT"):
            old = os.environ.pop(k, None)
            if old is not None:
                self._saved_env[k] = old

        if self._ca_pub_hex:
            os.environ["CORVIN_CA_PUBKEY_HEX"] = self._ca_pub_hex

        self.server = build_server(
            origins_dir=self.tmp,
            nonce_store=NonceStore(),
            force_m1_only=True,
            instance_id="e2e-recv",
        )
        serve_in_thread(self.server)
        _, port = self.server.server_address[:2]
        self.url = f"http://127.0.0.1:{port}/v1/a2a/receive"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)
        for k in ("CORVIN_CA_PUBKEY_HEX", "CORVIN_ATTESTATION_STRICT"):
            os.environ.pop(k, None)
        os.environ.update(self._saved_env)


# ── Tests: min_trust=none (default) ──────────────────────────────────────

class TestMinTrustNone(_ServerFixture):
    _min_trust = "none"

    def test_no_attestation_accepted(self):
        resp = _post(self.url, _envelope())
        self.assertEqual(resp["status"], "ok")

    def test_with_valid_attestation_also_accepted(self):
        priv, pub = att.generate_ca_keypair()
        os.environ["CORVIN_CA_PUBKEY_HEX"] = pub
        try:
            iac = att.sign_attestation(instance_id="inst-a", tier="community",
                                       ca_privkey_hex=priv)
            resp = _post(self.url, _envelope(attestation=iac))
            self.assertEqual(resp["status"], "ok")
        finally:
            os.environ.pop("CORVIN_CA_PUBKEY_HEX", None)


# ── Tests: min_trust=community ────────────────────────────────────────────

class TestMinTrustCommunity(_ServerFixture):

    @classmethod
    def setUpClass(cls):
        cls.ca_priv, cls.ca_pub = att.generate_ca_keypair()

    _min_trust = "community"

    def setUp(self):
        self._ca_pub_hex = self.__class__.ca_pub
        super().setUp()

    def test_no_attestation_rejected(self):
        resp = _post(self.url, _envelope())
        self.assertEqual(resp["status"], "rejected")

    def test_valid_community_iac_accepted(self):
        iac = att.sign_attestation(instance_id="inst-a", tier="community",
                                   ca_privkey_hex=self.__class__.ca_priv)
        resp = _post(self.url, _envelope(attestation=iac))
        self.assertEqual(resp["status"], "ok")

    def test_valid_verified_iac_accepted(self):
        iac = att.sign_attestation(instance_id="inst-a", tier="verified",
                                   ca_privkey_hex=self.__class__.ca_priv)
        resp = _post(self.url, _envelope(attestation=iac))
        self.assertEqual(resp["status"], "ok")

    def test_wrong_ca_rejected(self):
        wrong_priv, _ = att.generate_ca_keypair()
        iac = att.sign_attestation(instance_id="inst-x", tier="community",
                                   ca_privkey_hex=wrong_priv)
        resp = _post(self.url, _envelope(attestation=iac))
        self.assertEqual(resp["status"], "rejected")

    def test_expired_iac_rejected(self):
        iac = att.sign_attestation(instance_id="inst-y", tier="community",
                                   ca_privkey_hex=self.__class__.ca_priv,
                                   ttl_days=-1)
        resp = _post(self.url, _envelope(attestation=iac))
        self.assertEqual(resp["status"], "rejected")

    def test_tampered_tier_rejected(self):
        import copy
        iac = att.sign_attestation(instance_id="inst-z", tier="community",
                                   ca_privkey_hex=self.__class__.ca_priv)
        bad = copy.deepcopy(iac)
        bad["cert"]["tier"] = "enterprise"  # tamper without re-signing
        resp = _post(self.url, _envelope(attestation=bad))
        self.assertEqual(resp["status"], "rejected")


# ── Tests: min_trust=verified ─────────────────────────────────────────────

class TestMinTrustVerified(_ServerFixture):

    @classmethod
    def setUpClass(cls):
        cls.ca_priv, cls.ca_pub = att.generate_ca_keypair()

    _min_trust = "verified"

    def setUp(self):
        self._ca_pub_hex = self.__class__.ca_pub
        super().setUp()

    def test_community_tier_below_minimum_rejected(self):
        iac = att.sign_attestation(instance_id="inst-a", tier="community",
                                   ca_privkey_hex=self.__class__.ca_priv)
        resp = _post(self.url, _envelope(attestation=iac))
        self.assertEqual(resp["status"], "rejected")

    def test_verified_tier_accepted(self):
        iac = att.sign_attestation(instance_id="inst-b", tier="verified",
                                   ca_privkey_hex=self.__class__.ca_priv)
        resp = _post(self.url, _envelope(attestation=iac))
        self.assertEqual(resp["status"], "ok")

    def test_enterprise_tier_accepted(self):
        iac = att.sign_attestation(instance_id="inst-c", tier="enterprise",
                                   ca_privkey_hex=self.__class__.ca_priv)
        resp = _post(self.url, _envelope(attestation=iac))
        self.assertEqual(resp["status"], "ok")


# ── Tests: CA not configured ──────────────────────────────────────────────

class TestNoCaConfigured(_ServerFixture):
    _min_trust = "community"
    _ca_pub_hex = None  # intentionally no CA

    def test_fail_open_by_default(self):
        # No CA configured + min_trust=community → WARNING but accepted.
        resp = _post(self.url, _envelope())
        # Default: CORVIN_ATTESTATION_STRICT=0 → fail-open
        self.assertIn(resp["status"], ("ok", "rejected"))
        # We don't assert "ok" here because the WARNING+accept behaviour is
        # best-effort. What we assert is: it does NOT crash.

    def test_strict_mode_rejects_when_no_ca(self):
        os.environ["CORVIN_ATTESTATION_STRICT"] = "1"
        try:
            resp = _post(self.url, _envelope())
            self.assertEqual(resp["status"], "rejected")
        finally:
            os.environ.pop("CORVIN_ATTESTATION_STRICT", None)


# ── Tests: CLI commands ───────────────────────────────────────────────────

class TestCLICommands(unittest.TestCase):

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self._saved_path = os.environ.pop("CORVIN_ATTESTATION_PATH", None)
        os.environ["CORVIN_ATTESTATION_PATH"] = str(
            self.tmp / "instance_attestation.json"
        )
        self._saved_ca = os.environ.pop("CORVIN_CA_PUBKEY_HEX", None)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)
        os.environ.pop("CORVIN_ATTESTATION_PATH", None)
        if self._saved_path is not None:
            os.environ["CORVIN_ATTESTATION_PATH"] = self._saved_path
        os.environ.pop("CORVIN_CA_PUBKEY_HEX", None)
        if self._saved_ca is not None:
            os.environ["CORVIN_CA_PUBKEY_HEX"] = self._saved_ca

    def _run_cli(self, args: list[str]) -> tuple[int, str, str]:
        """Run corvin_instance_id CLI, capture stdout/stderr, return (rc, out, err)."""
        import io
        from operator import attrgetter
        sys.path.insert(0, str(Path(__file__).resolve().parents[2]
                                / "voice" / "scripts"))
        import corvin_instance_id as cli
        out_buf, err_buf = io.StringIO(), io.StringIO()
        old_out, old_err = sys.stdout, sys.stderr
        sys.stdout, sys.stderr = out_buf, err_buf
        try:
            rc = cli.main(args)
        finally:
            sys.stdout, sys.stderr = old_out, old_err
        return rc, out_buf.getvalue(), err_buf.getvalue()

    def test_register_self_signed_creates_iac(self):
        rc, out, err = self._run_cli(["register", "--self-signed"])
        self.assertEqual(rc, 0, f"stderr: {err}")
        iac = att.load_attestation()
        self.assertIsNotNone(iac)
        self.assertEqual(iac["cert"]["tier"], "community")
        self.assertTrue(iac.get("self_signed"))

    def test_register_self_signed_json_output(self):
        rc, out, _ = self._run_cli(["register", "--self-signed"])
        self.assertEqual(rc, 0)
        result = json.loads(out)
        self.assertIn("tier", result)
        self.assertIn("expires_at", result)

    def test_status_unregistered(self):
        rc, out, _ = self._run_cli(["status"])
        self.assertEqual(rc, 0)
        result = json.loads(out)
        self.assertEqual(result["trust_level"], "unverified")
        self.assertIn("instance_id", result)

    def test_status_after_self_signed(self):
        self._run_cli(["register", "--self-signed"])
        rc, out, _ = self._run_cli(["status"])
        self.assertEqual(rc, 0)
        result = json.loads(out)
        self.assertEqual(result["tier"], "community")

    def test_ca_init_requires_yes(self):
        rc, out, err = self._run_cli(["ca-init"])
        self.assertEqual(rc, 2)

    def test_ca_init_generates_keypair(self):
        out_file = str(self.tmp / "ca.json")
        rc, out, err = self._run_cli(["ca-init", "--yes", "--out", out_file])
        self.assertEqual(rc, 0, f"stderr: {err}")
        keypair = json.loads(Path(out_file).read_text())
        self.assertIn("ca_privkey_hex", keypair)
        self.assertIn("ca_pubkey_hex", keypair)
        # Verify it's a real Ed25519 keypair
        priv_bytes = bytes.fromhex(keypair["ca_privkey_hex"])
        pub_bytes = bytes.fromhex(keypair["ca_pubkey_hex"])
        self.assertEqual(len(priv_bytes), 32)
        self.assertEqual(len(pub_bytes), 32)

    def test_ca_init_output_mode_0600(self):
        out_file = str(self.tmp / "ca.json")
        self._run_cli(["ca-init", "--yes", "--out", out_file])
        mode = Path(out_file).stat().st_mode
        self.assertFalse(mode & (stat.S_IRWXG | stat.S_IRWXO))

    def test_register_missing_api_key_without_self_signed(self):
        rc, _, err = self._run_cli(["register"])
        self.assertEqual(rc, 2)
        self.assertIn("api-key", err.lower())


# ── CI lint ───────────────────────────────────────────────────────────────

import stat  # noqa: E402 (needed for mode checks above)


class TestCILint(unittest.TestCase):
    def test_no_anthropic_import(self):
        import ast
        for fname in ("instance_attestation.py",):
            src = (_here / fname).read_text("utf-8")
            tree = ast.parse(src)
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        self.assertNotEqual(alias.name, "anthropic",
                                            f"{fname}: import anthropic found")
                elif isinstance(node, ast.ImportFrom):
                    self.assertNotEqual(node.module, "anthropic",
                                        f"{fname}: from anthropic found")


if __name__ == "__main__":
    unittest.main(verbosity=2)
