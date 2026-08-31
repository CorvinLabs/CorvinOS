"""Tests for instance_attestation.py — ADR-0078 Phase 1."""
from __future__ import annotations

import json
import os
import stat
import sys
import tempfile
import time
import unittest
from pathlib import Path

_here = Path(__file__).resolve().parent
if str(_here) not in sys.path:
    sys.path.insert(0, str(_here))

import instance_attestation as att


# ── Helpers ───────────────────────────────────────────────────────────────

def _fresh_ca():
    """Return (priv_hex, pub_hex) for a throwaway test CA."""
    return att.generate_ca_keypair()


def _issue(instance_id="test-inst", tier="community", ttl_days=365,
           priv_hex=None, pub_hex=None):
    if priv_hex is None:
        priv_hex, pub_hex = _fresh_ca()
    iac = att.sign_attestation(
        instance_id=instance_id,
        tier=tier,
        ca_privkey_hex=priv_hex,
        corvin_version="2026.6-test",
        ttl_days=ttl_days,
    )
    return iac, priv_hex, pub_hex


# ── TrustLevel + parse_min_trust ─────────────────────────────────────────

class TestTrustLevel(unittest.TestCase):

    def test_ordering(self):
        self.assertLess(att.TrustLevel.UNVERIFIED, att.TrustLevel.COMMUNITY)
        self.assertLess(att.TrustLevel.COMMUNITY,  att.TrustLevel.VERIFIED)
        self.assertLess(att.TrustLevel.VERIFIED,   att.TrustLevel.ENTERPRISE)

    def test_parse_none_is_unverified(self):
        self.assertEqual(att.parse_min_trust(None), att.TrustLevel.UNVERIFIED)

    def test_parse_none_string(self):
        self.assertEqual(att.parse_min_trust("none"), att.TrustLevel.UNVERIFIED)

    def test_parse_community(self):
        self.assertEqual(att.parse_min_trust("community"), att.TrustLevel.COMMUNITY)

    def test_parse_verified(self):
        self.assertEqual(att.parse_min_trust("verified"), att.TrustLevel.VERIFIED)

    def test_parse_enterprise(self):
        self.assertEqual(att.parse_min_trust("enterprise"), att.TrustLevel.ENTERPRISE)

    def test_parse_unknown_is_unverified(self):
        self.assertEqual(att.parse_min_trust("godmode"), att.TrustLevel.UNVERIFIED)

    def test_trust_level_name_roundtrip(self):
        for level in att.TrustLevel:
            name = att.trust_level_name(level)
            self.assertIsInstance(name, str)
            self.assertTrue(len(name) > 0)


# ── generate_ca_keypair ───────────────────────────────────────────────────

class TestGenerateCaKeypair(unittest.TestCase):

    def test_returns_two_hex_strings(self):
        priv, pub = att.generate_ca_keypair()
        self.assertIsInstance(priv, str)
        self.assertIsInstance(pub, str)

    def test_keys_are_32_bytes(self):
        priv, pub = att.generate_ca_keypair()
        self.assertEqual(len(bytes.fromhex(priv)), 32)
        self.assertEqual(len(bytes.fromhex(pub)),  32)

    def test_two_calls_produce_different_keys(self):
        p1, _ = att.generate_ca_keypair()
        p2, _ = att.generate_ca_keypair()
        self.assertNotEqual(p1, p2)


# ── sign_attestation ──────────────────────────────────────────────────────

class TestSignAttestation(unittest.TestCase):

    def setUp(self):
        self.priv, self.pub = _fresh_ca()

    def test_returns_cert_and_sig(self):
        iac, _, _ = _issue(priv_hex=self.priv, pub_hex=self.pub)
        self.assertIn("cert", iac)
        self.assertIn("sig", iac)

    def test_cert_contains_required_fields(self):
        iac, _, _ = _issue(priv_hex=self.priv)
        cert = iac["cert"]
        for f in ("version", "instance_id", "tier", "registered_at",
                  "expires_at", "ca_pubkey_fingerprint", "corvin_version"):
            self.assertIn(f, cert, f"missing field: {f}")

    def test_tier_in_cert(self):
        iac, _, _ = _issue(tier="verified", priv_hex=self.priv)
        self.assertEqual(iac["cert"]["tier"], "verified")

    def test_expires_after_ttl(self):
        iac, _, _ = _issue(ttl_days=30, priv_hex=self.priv)
        now = time.time()
        self.assertGreater(iac["cert"]["expires_at"], now + 29 * 86400)
        self.assertLess(iac["cert"]["expires_at"],    now + 32 * 86400)

    def test_invalid_tier_raises(self):
        with self.assertRaises(ValueError):
            att.sign_attestation(
                instance_id="x", tier="godmode",
                ca_privkey_hex=self.priv,
            )

    def test_sig_is_hex_string(self):
        iac, _, _ = _issue(priv_hex=self.priv)
        sig_bytes = bytes.fromhex(iac["sig"])
        self.assertEqual(len(sig_bytes), 64)  # Ed25519 sig = 64 bytes


# ── verify_attestation ────────────────────────────────────────────────────

class TestVerifyAttestation(unittest.TestCase):

    def setUp(self):
        self.priv, self.pub = _fresh_ca()
        self.iac, _, _ = _issue(priv_hex=self.priv, pub_hex=self.pub)

    def test_valid_community_iac(self):
        level = att.verify_attestation(self.iac, bytes.fromhex(self.pub))
        self.assertEqual(level, att.TrustLevel.COMMUNITY)

    def test_verified_tier(self):
        iac, priv, pub = _issue(tier="verified")
        level = att.verify_attestation(iac, bytes.fromhex(pub))
        self.assertEqual(level, att.TrustLevel.VERIFIED)

    def test_enterprise_tier(self):
        iac, priv, pub = _issue(tier="enterprise")
        level = att.verify_attestation(iac, bytes.fromhex(pub))
        self.assertEqual(level, att.TrustLevel.ENTERPRISE)

    def test_wrong_pubkey_rejected(self):
        _, wrong_pub = _fresh_ca()
        level = att.verify_attestation(self.iac, bytes.fromhex(wrong_pub))
        self.assertEqual(level, att.TrustLevel.UNVERIFIED)

    def test_tampered_cert_rejected(self):
        import copy
        bad = copy.deepcopy(self.iac)
        bad["cert"]["tier"] = "enterprise"  # tamper without re-signing
        level = att.verify_attestation(bad, bytes.fromhex(self.pub))
        self.assertEqual(level, att.TrustLevel.UNVERIFIED)

    def test_tampered_sig_rejected(self):
        import copy
        bad = copy.deepcopy(self.iac)
        bad["sig"] = "00" * 64
        level = att.verify_attestation(bad, bytes.fromhex(self.pub))
        self.assertEqual(level, att.TrustLevel.UNVERIFIED)

    def test_expired_iac_rejected(self):
        iac, _, pub = _issue(ttl_days=-1)  # already expired
        level = att.verify_attestation(iac, bytes.fromhex(pub))
        self.assertEqual(level, att.TrustLevel.UNVERIFIED)

    def test_none_ca_pubkey_returns_unverified(self):
        level = att.verify_attestation(self.iac, None)
        self.assertEqual(level, att.TrustLevel.UNVERIFIED)

    def test_none_dict_returns_unverified(self):
        level = att.verify_attestation(None, bytes.fromhex(self.pub))
        self.assertEqual(level, att.TrustLevel.UNVERIFIED)

    def test_missing_cert_field_rejected(self):
        bad = {"sig": self.iac["sig"]}  # no cert
        level = att.verify_attestation(bad, bytes.fromhex(self.pub))
        self.assertEqual(level, att.TrustLevel.UNVERIFIED)

    def test_wrong_ca_fingerprint_rejected(self):
        import copy
        bad = copy.deepcopy(self.iac)
        bad["cert"]["ca_pubkey_fingerprint"] = "sha256:000000000000000"
        # Re-sign with same key but tampered fingerprint
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
        from cryptography.hazmat.primitives.serialization import Encoding, NoEncryption, PrivateFormat
        priv = Ed25519PrivateKey.from_private_bytes(bytes.fromhex(self.priv))
        canonical = att._canonical(bad["cert"])
        bad["sig"] = priv.sign(canonical).hex()
        # Verification: fingerprint mismatch
        level = att.verify_attestation(bad, bytes.fromhex(self.pub))
        self.assertEqual(level, att.TrustLevel.UNVERIFIED)


# ── get_ca_pubkey_bytes (env resolution) ─────────────────────────────────

class TestGetCaPubkeyBytes(unittest.TestCase):

    def setUp(self):
        self._saved = os.environ.copy()

    def tearDown(self):
        for k in ("CORVIN_CA_PUBKEY_HEX", "CORVIN_CA_PUBKEY_PATH"):
            os.environ.pop(k, None)
        os.environ.update(self._saved)

    def test_env_hex_takes_priority(self):
        _, pub = _fresh_ca()
        os.environ["CORVIN_CA_PUBKEY_HEX"] = pub
        result = att.get_ca_pubkey_bytes()
        self.assertEqual(result, bytes.fromhex(pub))

    def test_path_env(self):
        _, pub = _fresh_ca()
        with tempfile.NamedTemporaryFile(mode="w", suffix=".pub", delete=False) as f:
            f.write(pub)
            f.flush()
            os.environ["CORVIN_CA_PUBKEY_PATH"] = f.name
            result = att.get_ca_pubkey_bytes()
            self.assertEqual(result, bytes.fromhex(pub))

    def test_no_env_returns_none_when_constant_is_none(self):
        old = att.CORVIN_CA_PUBKEY_HEX
        att.CORVIN_CA_PUBKEY_HEX = None
        try:
            result = att.get_ca_pubkey_bytes()
            self.assertIsNone(result)
        finally:
            att.CORVIN_CA_PUBKEY_HEX = old

    def test_bad_hex_in_env_falls_through(self):
        os.environ["CORVIN_CA_PUBKEY_HEX"] = "notvalidhex!!!"
        result = att.get_ca_pubkey_bytes()
        # Falls through to next source; if none → None
        # (we don't check the actual value, just that no exception is raised)
        self.assertIsInstance(result, (bytes, type(None)))


# ── load_attestation / save_attestation ──────────────────────────────────

class TestLoadSaveAttestation(unittest.TestCase):

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self._saved_env = os.environ.pop("CORVIN_ATTESTATION_PATH", None)
        os.environ["CORVIN_ATTESTATION_PATH"] = str(
            self.tmp / "instance_attestation.json"
        )

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)
        os.environ.pop("CORVIN_ATTESTATION_PATH", None)
        if self._saved_env is not None:
            os.environ["CORVIN_ATTESTATION_PATH"] = self._saved_env

    def test_absent_returns_none(self):
        self.assertIsNone(att.load_attestation())

    def test_save_and_load_roundtrip(self):
        iac, _, _ = _issue()
        att.save_attestation(iac)
        loaded = att.load_attestation()
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded["cert"]["instance_id"],
                         iac["cert"]["instance_id"])

    def test_saved_file_is_mode_0600(self):
        iac, _, _ = _issue()
        att.save_attestation(iac)
        path = att.attestation_path()
        mode = path.stat().st_mode
        self.assertFalse(mode & (stat.S_IRWXG | stat.S_IRWXO))

    def test_world_readable_file_ignored(self):
        iac, _, _ = _issue()
        att.save_attestation(iac)
        path = att.attestation_path()
        os.chmod(path, 0o644)  # world-readable
        result = att.load_attestation()
        self.assertIsNone(result)


# ── audit_fields projection ───────────────────────────────────────────────

class TestAuditFields(unittest.TestCase):

    def test_none_iac_fields(self):
        fields = att.attestation_audit_fields(None, att.TrustLevel.UNVERIFIED)
        self.assertFalse(fields["attestation_present"])
        self.assertNotIn("instance_id", fields)
        self.assertNotIn("sig", fields)

    def test_valid_iac_fields(self):
        iac, _, pub = _issue()
        level = att.verify_attestation(iac, bytes.fromhex(pub))
        fields = att.attestation_audit_fields(iac, level)
        self.assertTrue(fields["attestation_present"])
        self.assertIn("tier", fields)
        self.assertIn("trust_level", fields)
        self.assertNotIn("instance_id", fields)
        self.assertNotIn("sig", fields)


# ── CI lint ───────────────────────────────────────────────────────────────

class TestCILint(unittest.TestCase):
    def test_no_anthropic_import(self):
        import ast
        src = (_here / "instance_attestation.py").read_text("utf-8")
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    self.assertNotEqual(alias.name, "anthropic")
            elif isinstance(node, ast.ImportFrom):
                self.assertNotEqual(node.module, "anthropic")


if __name__ == "__main__":
    unittest.main(verbosity=2)
