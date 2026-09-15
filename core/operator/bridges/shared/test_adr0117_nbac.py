"""Tests for ADR-0117 Network-Bound Audit Chains (NBAC).

Covers:
    - Genesis block creation and signature verification
    - Network ID mismatch detection
    - Epoch Certificate creation and verification
    - get_genesis_block / get_genesis_hash helpers
    - origin_genesis_hash / genesis_hash_matches helpers
    - TaskEnvelope sender_genesis_hash round-trip
    - RemoteTriggerReceiver chain DNA gate (grace period + strict mode)
    - voice_audit --peer-genesis-check

All tests are fully offline (no network, no Anthropic import).
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT / "operator" / "bridges" / "shared"))
VOICE_SCRIPTS = REPO_ROOT / "operator" / "voice" / "scripts"

# ── Key generation (test-only) ──────────────────────────────────────────────

def _generate_test_keypair() -> tuple[bytes, bytes]:
    """Return (privkey_pem, pubkey_pem) for a fresh 2048-bit RSA test keypair."""
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.hazmat.primitives.serialization import (
        Encoding, NoEncryption, PrivateFormat, PublicFormat,
    )
    privkey = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    priv_pem = privkey.private_bytes(Encoding.PEM, PrivateFormat.TraditionalOpenSSL, NoEncryption())
    pub_pem = privkey.public_key().public_bytes(Encoding.PEM, PublicFormat.SubjectPublicKeyInfo)
    return priv_pem, pub_pem


# ── nbac module tests ──────────────────────────────────────────────────────

class TestGenesisBlock(unittest.TestCase):

    def setUp(self):
        self._priv_pem, self._pub_pem = _generate_test_keypair()

    def _patch_pubkey(self):
        """Context manager that patches nbac.load_network_pubkey to use test key."""
        from cryptography.hazmat.primitives.serialization import load_pem_public_key
        pub = load_pem_public_key(self._pub_pem)
        return patch("nbac.load_network_pubkey", return_value=pub)

    def test_sign_and_verify(self):
        """Signed genesis block verifies correctly with the matching public key."""
        import nbac
        with self._patch_pubkey():
            block = nbac.sign_genesis_block(self._priv_pem, instance_id="test-inst-001")
        self.assertIn("genesis_sig", block)
        self.assertEqual(block["prev_hash"], "0" * 64)
        with self._patch_pubkey():
            self.assertTrue(nbac.verify_genesis_block(block))

    def test_tampered_block_fails(self):
        """A tampered genesis block must not verify."""
        import nbac
        with self._patch_pubkey():
            block = nbac.sign_genesis_block(self._priv_pem, instance_id="test-inst-002")
            block["network_id"] = "evil-network"
            self.assertFalse(nbac.verify_genesis_block(block))

    def test_wrong_pubkey_fails(self):
        """Verification with wrong pubkey must fail."""
        import nbac
        _, wrong_pub_pem = _generate_test_keypair()
        from cryptography.hazmat.primitives.serialization import load_pem_public_key
        wrong_pub = load_pem_public_key(wrong_pub_pem)
        with self._patch_pubkey():
            block = nbac.sign_genesis_block(self._priv_pem, instance_id="test-inst-003")
        with patch("nbac.load_network_pubkey", return_value=wrong_pub):
            self.assertFalse(nbac.verify_genesis_block(block))

    def test_missing_sig_fails(self):
        """Block without genesis_sig must not verify."""
        import nbac
        with self._patch_pubkey():
            block = nbac.sign_genesis_block(self._priv_pem, instance_id="test-inst-004")
        del block["genesis_sig"]
        with self._patch_pubkey():
            self.assertFalse(nbac.verify_genesis_block(block))

    def test_network_id_field_present(self):
        """genesis block must contain network_id."""
        import nbac
        with self._patch_pubkey():
            block = nbac.sign_genesis_block(self._priv_pem, instance_id="inst-net")
        self.assertIn("network_id", block)

    def test_verify_genesis_network_match(self):
        """verify_genesis_network returns True for matching network_id."""
        import nbac
        with self._patch_pubkey():
            block = nbac.sign_genesis_block(self._priv_pem, instance_id="inst-net2")
        net = block["network_id"]
        self.assertTrue(nbac.verify_genesis_network(block, expected_network_id=net))

    def test_verify_genesis_network_mismatch(self):
        """verify_genesis_network returns False for wrong network_id."""
        import nbac
        with self._patch_pubkey():
            block = nbac.sign_genesis_block(self._priv_pem, instance_id="inst-net3")
        self.assertFalse(nbac.verify_genesis_network(block, expected_network_id="other-net"))

    def test_build_genesis_payload_deterministic_fields(self):
        """build_genesis_payload includes all required fields."""
        import nbac
        with self._patch_pubkey():
            payload = nbac.build_genesis_payload(instance_id="inst-payload")
        for key in ("type", "network_id", "instance_id", "software_commit",
                    "network_pubkey_fp", "issued_at"):
            self.assertIn(key, payload)
        self.assertEqual(payload["type"], nbac.GENESIS_EVENT_TYPE)


class TestChainReadHelpers(unittest.TestCase):

    def setUp(self):
        self._priv_pem, self._pub_pem = _generate_test_keypair()
        from cryptography.hazmat.primitives.serialization import load_pem_public_key
        self._pub = load_pem_public_key(self._pub_pem)

    def _make_chain_file(self, tmpdir: str) -> tuple[Path, str]:
        """Write a minimal audit.jsonl with a genesis block; return (path, hash)."""
        import nbac
        with patch("nbac.load_network_pubkey", return_value=self._pub):
            block = nbac.sign_genesis_block(self._priv_pem, instance_id="chain-test")
        # Simulate write_event wrapping: event_type at top, details nested.
        # The 'hash' field in the chain record is a 16-char prefix, NOT used as
        # the chain identity fingerprint.  get_genesis_hash() computes
        # SHA-256(_canonical_json(details)) for a stable 64-char fingerprint.
        expected_hash = hashlib.sha256(
            json.dumps(block, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
        ).hexdigest()
        line = json.dumps({
            "event_type": nbac.GENESIS_EVENT_TYPE,
            "hash": hashlib.sha256(json.dumps(block).encode()).hexdigest()[:16],  # realistic chain hash
            "prev_hash": "0" * 64,
            "details": block,
        })
        path = Path(tmpdir) / "audit.jsonl"
        path.write_text(line + "\n")
        return path, expected_hash

    def test_get_genesis_block_returns_block(self):
        import nbac
        with tempfile.TemporaryDirectory() as td:
            path, _ = self._make_chain_file(td)
            block = nbac.get_genesis_block(path)
            self.assertIsNotNone(block)
            self.assertEqual(block["event_type"], nbac.GENESIS_EVENT_TYPE)

    def test_get_genesis_hash_returns_hash(self):
        import nbac
        with tempfile.TemporaryDirectory() as td:
            path, expected_hash = self._make_chain_file(td)
            gh = nbac.get_genesis_hash(path)
            self.assertEqual(gh, expected_hash)

    def test_get_genesis_block_missing_file(self):
        import nbac
        self.assertIsNone(nbac.get_genesis_block(Path("/nonexistent/audit.jsonl")))

    def test_get_genesis_block_empty_file(self):
        import nbac
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "audit.jsonl"
            path.write_text("")
            self.assertIsNone(nbac.get_genesis_block(path))

    def test_get_genesis_block_no_genesis_event(self):
        import nbac
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "audit.jsonl"
            path.write_text(json.dumps({"event_type": "A2A.envelope_received", "hash": "abc"}) + "\n")
            self.assertIsNone(nbac.get_genesis_block(path))


class TestEpochCertificate(unittest.TestCase):

    def setUp(self):
        self._priv_pem, self._pub_pem = _generate_test_keypair()
        from cryptography.hazmat.primitives.serialization import load_pem_public_key
        self._pub = load_pem_public_key(self._pub_pem)

    def test_create_and_verify(self):
        import nbac
        with patch("nbac.load_network_pubkey", return_value=self._pub):
            cert = nbac.create_epoch_cert(
                self._priv_pem,
                instance_id="epoch-inst",
                genesis_hash="a" * 64,
                chain_tail="b" * 64,
                epoch_number=1,
                ttl_s=3600,
            )
            self.assertTrue(cert.verify())

    def test_tampered_cert_fails(self):
        import nbac
        with patch("nbac.load_network_pubkey", return_value=self._pub):
            cert = nbac.create_epoch_cert(
                self._priv_pem,
                instance_id="epoch-inst2",
                genesis_hash="c" * 64,
                chain_tail="d" * 64,
                epoch_number=2,
                ttl_s=3600,
            )
            cert.genesis_hash = "e" * 64
            self.assertFalse(cert.verify())

    def test_is_expired(self):
        import nbac
        with patch("nbac.load_network_pubkey", return_value=self._pub):
            cert = nbac.create_epoch_cert(
                self._priv_pem,
                instance_id="epoch-inst3",
                genesis_hash="f" * 64,
                chain_tail="g" * 64,
                epoch_number=3,
                ttl_s=-1,  # already expired
            )
        self.assertTrue(cert.is_expired())

    def test_save_and_load_latest(self):
        import nbac
        with patch("nbac.load_network_pubkey", return_value=self._pub):
            cert = nbac.create_epoch_cert(
                self._priv_pem,
                instance_id="epoch-save",
                genesis_hash="h" * 64,
                chain_tail="i" * 64,
                epoch_number=5,
                ttl_s=3600,
            )
        with tempfile.TemporaryDirectory() as td:
            nbac_dir = Path(td) / "nbac"
            nbac.save_epoch_cert(cert, nbac_dir)
            loaded = nbac.load_latest_epoch_cert(nbac_dir)
            self.assertIsNotNone(loaded)
            self.assertEqual(loaded.epoch_number, 5)
            self.assertEqual(loaded.genesis_hash, "h" * 64)

    def test_load_latest_picks_highest(self):
        import nbac
        with patch("nbac.load_network_pubkey", return_value=self._pub):
            with tempfile.TemporaryDirectory() as td:
                nbac_dir = Path(td) / "nbac"
                for epoch in (1, 3, 2):
                    cert = nbac.create_epoch_cert(
                        self._priv_pem,
                        instance_id="epoch-multi",
                        genesis_hash="j" * 64,
                        chain_tail="k" * 64,
                        epoch_number=epoch,
                        ttl_s=3600,
                    )
                    nbac.save_epoch_cert(cert, nbac_dir)
                latest = nbac.load_latest_epoch_cert(nbac_dir)
                self.assertEqual(latest.epoch_number, 3)


class TestOriginHelpers(unittest.TestCase):

    def test_origin_genesis_hash_present(self):
        import nbac
        cfg = {"peer_genesis_hash": "a" * 64}
        self.assertEqual(nbac.origin_genesis_hash(cfg), "a" * 64)

    def test_origin_genesis_hash_absent(self):
        import nbac
        self.assertIsNone(nbac.origin_genesis_hash({}))

    def test_genesis_hash_matches_grace_period(self):
        """Missing expected hash → True (grace period: pre-ADR-0117 origin)."""
        import nbac
        self.assertTrue(nbac.genesis_hash_matches("a" * 64, {}))

    def test_genesis_hash_matches_sender_absent_grace(self):
        """Missing sender hash → True (sender predates M4)."""
        import nbac
        cfg = {"peer_genesis_hash": "a" * 64}
        self.assertTrue(nbac.genesis_hash_matches(None, cfg))

    def test_genesis_hash_matches_correct(self):
        import nbac
        cfg = {"peer_genesis_hash": "a" * 64}
        self.assertTrue(nbac.genesis_hash_matches("a" * 64, cfg))

    def test_genesis_hash_matches_mismatch(self):
        import nbac
        cfg = {"peer_genesis_hash": "a" * 64}
        self.assertFalse(nbac.genesis_hash_matches("b" * 64, cfg))


# ── TaskEnvelope sender_genesis_hash tests ────────────────────────────────

class TestTaskEnvelopeGenesisHash(unittest.TestCase):

    def _make_envelope_dict(self, sender_genesis_hash=None) -> dict:
        d = {
            "task_id": "task-001",
            "nonce": "nonce-abc",
            "issued_at": time.time(),
            "origin_id": "test-origin",
            "instruction": "do something",
            "result_schema": {},
            "ttl_s": 300,
            "signature": "deadbeef" * 8,
            "sender_instance_id": "inst-001",
            "attachments": [],
        }
        if sender_genesis_hash is not None:
            d["sender_genesis_hash"] = sender_genesis_hash
        return d

    def test_from_dict_with_valid_genesis_hash(self):
        from remote_trigger_receiver import TaskEnvelope
        env_dict = self._make_envelope_dict(sender_genesis_hash="a" * 64)
        env = TaskEnvelope.from_dict(env_dict)
        self.assertEqual(env.sender_genesis_hash, "a" * 64)

    def test_from_dict_without_genesis_hash(self):
        from remote_trigger_receiver import TaskEnvelope
        env_dict = self._make_envelope_dict()
        env = TaskEnvelope.from_dict(env_dict)
        self.assertIsNone(env.sender_genesis_hash)

    def test_from_dict_invalid_genesis_hash_non_hex(self):
        """Non-hex genesis hash is silently dropped (security: no oracle for bad data)."""
        from remote_trigger_receiver import TaskEnvelope
        env_dict = self._make_envelope_dict(sender_genesis_hash="zz" + "a" * 62)
        env = TaskEnvelope.from_dict(env_dict)
        self.assertIsNone(env.sender_genesis_hash)

    def test_from_dict_invalid_genesis_hash_too_short(self):
        """Too-short genesis hash is silently dropped."""
        from remote_trigger_receiver import TaskEnvelope
        env_dict = self._make_envelope_dict(sender_genesis_hash="a" * 32)
        env = TaskEnvelope.from_dict(env_dict)
        self.assertIsNone(env.sender_genesis_hash)

    def test_canonical_payload_includes_genesis_hash(self):
        """When sender_genesis_hash is present it appears in canonical payload."""
        from remote_trigger_receiver import TaskEnvelope
        env_dict = self._make_envelope_dict(sender_genesis_hash="b" * 64)
        env = TaskEnvelope.from_dict(env_dict)
        payload = json.loads(env.canonical_payload())
        self.assertIn("sender_genesis_hash", payload)
        self.assertEqual(payload["sender_genesis_hash"], "b" * 64)

    def test_canonical_payload_omits_absent_genesis_hash(self):
        """When sender_genesis_hash is absent it is NOT in canonical payload."""
        from remote_trigger_receiver import TaskEnvelope
        env_dict = self._make_envelope_dict()
        env = TaskEnvelope.from_dict(env_dict)
        payload = json.loads(env.canonical_payload())
        self.assertNotIn("sender_genesis_hash", payload)


# ── RemoteTriggerSender genesis hash in _build_envelope ────────────────────

class TestSenderBuildsGenesisHash(unittest.TestCase):

    def test_build_envelope_includes_genesis_hash(self):
        from remote_trigger_sender import RemoteTriggerSender
        import hmac as _hmac
        key = "00" * 32
        env = RemoteTriggerSender._build_envelope(
            task_id="t1",
            nonce="n1",
            origin_id="orig",
            instruction="hello",
            result_schema={},
            ttl_s=300,
            hmac_key_hex=key,
            sender_instance_id="inst1",
            sender_genesis_hash="c" * 64,
        )
        self.assertEqual(env.get("sender_genesis_hash"), "c" * 64)

    def test_build_envelope_omits_none_genesis_hash(self):
        from remote_trigger_sender import RemoteTriggerSender
        key = "00" * 32
        env = RemoteTriggerSender._build_envelope(
            task_id="t2",
            nonce="n2",
            origin_id="orig",
            instruction="hello",
            result_schema={},
            ttl_s=300,
            hmac_key_hex=key,
            sender_instance_id="inst2",
            sender_genesis_hash=None,
        )
        self.assertNotIn("sender_genesis_hash", env)

    def test_genesis_hash_in_hmac_payload(self):
        """Changing sender_genesis_hash changes the HMAC signature."""
        from remote_trigger_sender import RemoteTriggerSender
        key = "aa" * 32
        kwargs = dict(
            task_id="t3", nonce="n3", origin_id="orig", instruction="hi",
            result_schema={}, ttl_s=300, hmac_key_hex=key, sender_instance_id="inst3",
        )
        env_with = RemoteTriggerSender._build_envelope(**kwargs, sender_genesis_hash="d" * 64)
        env_without = RemoteTriggerSender._build_envelope(**kwargs, sender_genesis_hash=None)
        self.assertNotEqual(env_with["signature"], env_without["signature"])


# ── RemoteTriggerReceiver chain DNA gate ─────────────────────────────────

RECV_KEY = "bb" * 32  # 64-hex recv_key for signing responses in tests


def _write_origin_file(tmpdir: Path, origin_id: str, hmac_key: str,
                       peer_genesis_hash: str | None = None,
                       nbac_strict: bool = False) -> Path:
    origins_dir = tmpdir / "remote_origins"
    origins_dir.mkdir(exist_ok=True)
    cfg: dict = {
        "enabled": True,
        "origin_id": origin_id,
        "hmac_key": hmac_key,
        "recv_key": RECV_KEY,
        "spawn_worker": False,
    }
    if peer_genesis_hash is not None:
        cfg["peer_genesis_hash"] = peer_genesis_hash
    if nbac_strict:
        cfg["nbac_strict"] = True
    origin_file = origins_dir / f"{origin_id}.json"
    origin_file.write_text(json.dumps(cfg))
    origin_file.chmod(0o600)
    return origin_file


class TestReceiverChainDNAGate(unittest.TestCase):
    """Test NBAC chain DNA verification inside RemoteTriggerReceiver.receive()."""

    HMAC_KEY = "00" * 32
    INSTANCE_ID = "test-recv-instance"

    def _build_signed_envelope(self, sender_genesis_hash: str | None = None) -> dict:
        """Build a signed TaskEnvelope dict with an optional sender_genesis_hash."""
        from remote_trigger_sender import RemoteTriggerSender
        return RemoteTriggerSender._build_envelope(
            task_id="dna-task-001",
            nonce=os.urandom(8).hex(),
            origin_id="dna-origin",
            instruction="test chain dna",
            result_schema={},
            ttl_s=300,
            hmac_key_hex=self.HMAC_KEY,
            sender_instance_id="sender-inst-001",
            sender_genesis_hash=sender_genesis_hash,
        )

    def _make_receiver(self, tmpdir: Path) -> "RemoteTriggerReceiver":
        from remote_trigger_receiver import RemoteTriggerReceiver
        mock_se = MagicMock()
        mock_se.get_audit_chain_tail.return_value = "t" * 64
        mock_se.write_event.return_value = None
        return RemoteTriggerReceiver(
            origins_dir=tmpdir / "remote_origins",
            instance_id=self.INSTANCE_ID,
            forge_se=mock_se,
        )

    def test_no_genesis_in_envelope_emits_warning(self):
        """Envelope without sender_genesis_hash emits chain_dna_genesis_absent."""
        with tempfile.TemporaryDirectory() as td:
            tmpdir = Path(td)
            _write_origin_file(tmpdir, "dna-origin", self.HMAC_KEY,
                               peer_genesis_hash="a" * 64)
            recv = self._make_receiver(tmpdir)
            env_dict = self._build_signed_envelope(sender_genesis_hash=None)
            # Should NOT reject (grace period)
            resp = recv.receive(env_dict)
            self.assertIsNotNone(resp)
            # verify audit calls include chain_dna_genesis_absent
            audit_calls = [
                str(c) for c in recv._inst_forge_se.write_event.call_args_list
            ]
            dna_absent = any("chain_dna_genesis_absent" in c for c in audit_calls)
            self.assertTrue(dna_absent, "Expected chain_dna_genesis_absent audit event")

    def test_matching_genesis_hash_emits_verified(self):
        """Matching genesis hash emits A2A.chain_dna_verified."""
        genesis = "b" * 64
        with tempfile.TemporaryDirectory() as td:
            tmpdir = Path(td)
            _write_origin_file(tmpdir, "dna-origin", self.HMAC_KEY,
                               peer_genesis_hash=genesis)
            recv = self._make_receiver(tmpdir)
            env_dict = self._build_signed_envelope(sender_genesis_hash=genesis)
            resp = recv.receive(env_dict)
            self.assertIsNotNone(resp)
            audit_calls = [str(c) for c in recv._inst_forge_se.write_event.call_args_list]
            dna_ok = any("chain_dna_verified" in c for c in audit_calls)
            self.assertTrue(dna_ok, "Expected chain_dna_verified audit event")

    def test_mismatched_genesis_hash_grace_period_passes(self):
        """Mismatched genesis hash in grace mode (nbac_strict=False) MUST NOT reject."""
        with tempfile.TemporaryDirectory() as td:
            tmpdir = Path(td)
            _write_origin_file(tmpdir, "dna-origin", self.HMAC_KEY,
                               peer_genesis_hash="a" * 64, nbac_strict=False)
            recv = self._make_receiver(tmpdir)
            env_dict = self._build_signed_envelope(sender_genesis_hash="b" * 64)
            resp = recv.receive(env_dict)
            # Grace mode → should not hard-reject based on genesis mismatch
            audit_calls = [str(c) for c in recv._inst_forge_se.write_event.call_args_list]
            dna_mismatch = any("chain_dna_mismatch" in c for c in audit_calls)
            self.assertTrue(dna_mismatch, "Expected chain_dna_mismatch audit event")
            # Status must NOT be "rejected" (only a warning)
            self.assertNotEqual(resp.status, "rejected",
                                "Grace mode should not reject — got 'rejected'")

    def test_mismatched_genesis_hash_strict_rejects(self):
        """Mismatched genesis hash in strict mode MUST reject the envelope."""
        with tempfile.TemporaryDirectory() as td:
            tmpdir = Path(td)
            _write_origin_file(tmpdir, "dna-origin", self.HMAC_KEY,
                               peer_genesis_hash="a" * 64, nbac_strict=True)
            recv = self._make_receiver(tmpdir)
            env_dict = self._build_signed_envelope(sender_genesis_hash="c" * 64)
            resp = recv.receive(env_dict)
            self.assertIsNotNone(resp)
            # In strict mode, the response status must be "rejected"
            self.assertEqual(resp.status, "rejected",
                             f"Expected rejected response, got status={resp.status!r}")


# ── voice_audit --peer-genesis-check ──────────────────────────────────────

class TestVoiceAuditGenesisCheck(unittest.TestCase):
    """Integration-level: _nbac_cross_genesis_check() in voice_audit.py."""

    def setUp(self):
        sys.path.insert(0, str(VOICE_SCRIPTS))
        self._priv_pem, self._pub_pem = _generate_test_keypair()
        from cryptography.hazmat.primitives.serialization import load_pem_public_key
        self._pub = load_pem_public_key(self._pub_pem)

    def _make_chain(self, tmpdir: Path, instance_id: str,
                    network_id: str = "test-net") -> Path:
        import nbac
        with patch("nbac.load_network_pubkey", return_value=self._pub), \
             patch("nbac.network_id", return_value=network_id):
            block = nbac.sign_genesis_block(self._priv_pem, instance_id=instance_id)
        entry = {
            "event_type": nbac.GENESIS_EVENT_TYPE,
            "hash": hashlib.sha256(json.dumps(block, sort_keys=True).encode()).hexdigest(),
            "prev_hash": "0" * 64,
            "details": block,
        }
        chain_path = tmpdir / f"audit_{instance_id}.jsonl"
        chain_path.write_text(json.dumps(entry) + "\n")
        return chain_path

    def test_same_network_id_passes(self):
        import voice_audit
        with tempfile.TemporaryDirectory() as td:
            tmpdir = Path(td)
            path_a = self._make_chain(tmpdir, "inst-a", "corvinlabs-public")
            path_b = self._make_chain(tmpdir, "inst-b", "corvinlabs-public")
            with patch("nbac.load_network_pubkey", return_value=self._pub):
                ok, msg = voice_audit._nbac_cross_genesis_check(path_a, path_b)
            self.assertTrue(ok, msg)
            self.assertIn("corvinlabs-public", msg)

    def test_different_network_id_fails(self):
        import voice_audit
        with tempfile.TemporaryDirectory() as td:
            tmpdir = Path(td)
            path_a = self._make_chain(tmpdir, "inst-c", "corvinlabs-public")
            path_b = self._make_chain(tmpdir, "inst-d", "fork-network")
            with patch("nbac.load_network_pubkey", return_value=self._pub):
                ok, msg = voice_audit._nbac_cross_genesis_check(path_a, path_b)
            self.assertFalse(ok, msg)
            self.assertIn("mismatch", msg)

    def test_peer_chain_missing_genesis_fails(self):
        import voice_audit
        with tempfile.TemporaryDirectory() as td:
            tmpdir = Path(td)
            path_a = self._make_chain(tmpdir, "inst-e", "corvinlabs-public")
            path_no_genesis = tmpdir / "audit_no_genesis.jsonl"
            path_no_genesis.write_text(
                json.dumps({"event_type": "A2A.envelope_received",
                            "hash": "x" * 64}) + "\n"
            )
            with patch("nbac.load_network_pubkey", return_value=self._pub):
                ok, msg = voice_audit._nbac_cross_genesis_check(path_a, path_no_genesis)
            self.assertFalse(ok, msg)


if __name__ == "__main__":
    unittest.main()
