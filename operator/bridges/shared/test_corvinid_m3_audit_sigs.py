"""test_corvinid_m3_audit_sigs.py — ADR-0153 M3: per-event instance_id / Ed25519 audit-signature attestation.

Coverage
--------
1. write_event() adds ``instance_id`` and ``instance_sig`` fields to hash-chained events
   when instance_identity is available (happy path).
2. write_event() works correctly (no crash, event still written) when instance_identity
   is NOT importable (best-effort behaviour).
3. ``instance_sig`` is a valid base64url string (URL-safe alphabet, no padding).
4. ``instance_sig`` signs over sha256(event_type + ":" + str(int(ts)) + ":" + hash).
5. Events written with hash_chain=False do NOT get instance_id / instance_sig.
6. verify_chain() with _VERIFY_SIGS=True detects a tampered instance_sig.
7. verify_chain() with _VERIFY_SIGS=True accepts a correct instance_sig.
8. verify_chain() with _VERIFY_SIGS=False skips sig checks entirely.
"""
from __future__ import annotations

import base64
import hashlib
import importlib
import json
import os
import sys
import tempfile
import unittest
import unittest.mock as mock
from pathlib import Path

# ---------------------------------------------------------------------------
# Path setup — make both forge (security_events) and shared (instance_identity)
# importable regardless of CWD.
# ---------------------------------------------------------------------------
_here = Path(__file__).resolve().parent                      # operator/bridges/shared
_forge_root = _here.parents[1] / "forge"                    # operator/forge
_shared = _here                                              # operator/bridges/shared

for _p in (str(_forge_root), str(_shared)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

# Drop any stale cached version so we start clean.
for _key in list(sys.modules):
    if "security_events" in _key or _key in ("forge",):
        del sys.modules[_key]

import forge.security_events as se  # type: ignore[import]
from forge.security_events import write_event, verify_chain, set_verify_sigs  # type: ignore[import]

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

BASE64URL_RE = __import__("re").compile(r"^[A-Za-z0-9\-_]+$")


def _tmpfile() -> Path:
    fd, name = tempfile.mkstemp(suffix=".jsonl", prefix="m3_test_")
    os.close(fd)
    return Path(name)


def _read_last(path: Path) -> dict:
    lines = [l.strip() for l in path.read_text().splitlines() if l.strip()]
    return json.loads(lines[-1])


# ---------------------------------------------------------------------------
# Minimal fake instance_identity — deterministic Ed25519 key pair.
# We generate a real key so that sign/verify work without touching the
# filesystem.
# ---------------------------------------------------------------------------

try:
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives import serialization as _ser
    _CRYPTO_OK = True
except ImportError:
    _CRYPTO_OK = False


def _make_fake_iid():
    """Return a fake instance_identity namespace with a real Ed25519 key pair."""
    if not _CRYPTO_OK:
        return None

    privkey = Ed25519PrivateKey.generate()
    pubkey = privkey.public_key()
    raw_pub = pubkey.public_bytes(_ser.Encoding.Raw, _ser.PublicFormat.Raw)
    pubkey_b64 = base64.urlsafe_b64encode(raw_pub).rstrip(b"=").decode()

    _iid = mock.MagicMock()
    _iid.get_instance_id.return_value = "test-instance-uuid-1234"
    _iid.get_instance_pubkey_b64.return_value = pubkey_b64

    def _sign(payload: bytes) -> str:
        sig = privkey.sign(payload)
        return base64.urlsafe_b64encode(sig).rstrip(b"=").decode()

    def _verify(sig_b64: str, payload: bytes, pk_b64: str) -> bool:
        try:
            sig = base64.urlsafe_b64decode(sig_b64 + "==")
            raw = base64.urlsafe_b64decode(pk_b64 + "==")
            from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
            pub = Ed25519PublicKey.from_public_bytes(raw)
            pub.verify(sig, payload)
            return True
        except Exception:
            return False

    _iid.sign_payload.side_effect = _sign
    _iid.verify_instance_sig.side_effect = _verify

    return _iid


# ---------------------------------------------------------------------------
# Test cases
# ---------------------------------------------------------------------------

class TestM3WriteEventHappyPath(unittest.TestCase):
    """Test 1 & 3: instance_id / instance_sig present and valid."""

    def setUp(self):
        self.path = _tmpfile()
        self.fake_iid = _make_fake_iid()

    def tearDown(self):
        self.path.unlink(missing_ok=True)
        set_verify_sigs(False)

    @unittest.skipUnless(_CRYPTO_OK, "cryptography package not installed")
    def test_instance_sig_added_on_hash_chain_event(self):
        """write_event with hash_chain=True injects instance_id and instance_sig."""
        # Patch the two-try import pattern inside write_event.
        with mock.patch.dict("sys.modules", {
            "operator.bridges.shared.instance_identity": self.fake_iid,
            "instance_identity": self.fake_iid,
        }):
            rec = write_event(self.path, "tool.created", hash_chain=True)

        self.assertIn("instance_id", rec, "instance_id should be in the returned record")
        self.assertIn("instance_sig", rec, "instance_sig should be in the returned record")

        disk_rec = _read_last(self.path)
        self.assertIn("instance_id", disk_rec)
        self.assertIn("instance_sig", disk_rec)
        self.assertEqual(disk_rec["instance_id"], "test-instance-uuid-1234")

    @unittest.skipUnless(_CRYPTO_OK, "cryptography package not installed")
    def test_instance_sig_is_base64url(self):
        """Test 3: instance_sig uses URL-safe base64 alphabet with no padding."""
        with mock.patch.dict("sys.modules", {
            "operator.bridges.shared.instance_identity": self.fake_iid,
            "instance_identity": self.fake_iid,
        }):
            rec = write_event(self.path, "tool.created", hash_chain=True)

        sig = rec.get("instance_sig", "")
        self.assertTrue(sig, "instance_sig must be non-empty")
        self.assertRegex(sig, BASE64URL_RE,
                         "instance_sig must match base64url alphabet (no +/= padding)")
        # Verify it is decodeable
        decoded = base64.urlsafe_b64decode(sig + "==")
        self.assertEqual(len(decoded), 64, "Ed25519 signature is always 64 bytes")

    @unittest.skipUnless(_CRYPTO_OK, "cryptography package not installed")
    def test_instance_sig_payload_is_sha256_of_eventtype_ts_hash(self):
        """Test 4: instance_sig signs sha256(event_type + ':' + str(int(ts)) + ':' + hash)."""
        captured_payloads = []

        original_sign = self.fake_iid.sign_payload.side_effect

        def capturing_sign(payload: bytes) -> str:
            captured_payloads.append(payload)
            return original_sign(payload)

        self.fake_iid.sign_payload.side_effect = capturing_sign

        with mock.patch.dict("sys.modules", {
            "operator.bridges.shared.instance_identity": self.fake_iid,
            "instance_identity": self.fake_iid,
        }):
            rec = write_event(self.path, "tool.created", hash_chain=True)

        self.assertEqual(len(captured_payloads), 1, "sign_payload should be called exactly once")
        signed_bytes = captured_payloads[0]

        # Reconstruct expected payload from the record fields
        expected_inner = (
            rec["event_type"]
            + ":"
            + str(int(rec["ts"]))
            + ":"
            + rec["hash"]
        )
        expected_payload = hashlib.sha256(expected_inner.encode("utf-8")).digest()

        self.assertEqual(signed_bytes, expected_payload,
                         "sign_payload should receive sha256(event_type:int(ts):hash)")


class TestM3BestEffortWhenUnavailable(unittest.TestCase):
    """Test 2: write_event still works when instance_identity cannot be imported."""

    def setUp(self):
        self.path = _tmpfile()

    def tearDown(self):
        self.path.unlink(missing_ok=True)

    def test_no_crash_when_instance_identity_unavailable(self):
        """If instance_identity is not importable, write_event succeeds without sig."""
        # Simulate ImportError for both import paths
        bad_iid = mock.MagicMock()
        bad_iid.get_instance_id.side_effect = ImportError("not installed")

        # Patch both try-paths to raise ImportError
        import builtins
        original_import = builtins.__import__

        def failing_import(name, *args, **kwargs):
            if name in ("instance_identity", "operator.bridges.shared.instance_identity"):
                raise ImportError(f"mocked unavailable: {name}")
            return original_import(name, *args, **kwargs)

        with mock.patch("builtins.__import__", side_effect=failing_import):
            # Must not raise
            rec = write_event(self.path, "tool.created", hash_chain=True)

        # Event must have been written
        disk_rec = _read_last(self.path)
        self.assertEqual(disk_rec["event_type"], "tool.created")
        self.assertIn("hash", disk_rec, "hash_chain should still work without sig")

        # instance_sig must NOT be present (best-effort failed silently)
        self.assertNotIn("instance_sig", disk_rec,
                         "instance_sig must be absent when signing fails")
        self.assertNotIn("instance_id", disk_rec,
                         "instance_id must be absent when signing fails")

    def test_no_crash_when_sign_payload_raises(self):
        """If sign_payload raises, write_event succeeds without crashing."""
        bad_iid = mock.MagicMock()
        bad_iid.get_instance_id.return_value = "uuid-abc"
        bad_iid.sign_payload.side_effect = RuntimeError("key file missing")

        with mock.patch.dict("sys.modules", {
            "operator.bridges.shared.instance_identity": bad_iid,
            "instance_identity": bad_iid,
        }):
            rec = write_event(self.path, "tool.created", hash_chain=True)

        disk_rec = _read_last(self.path)
        self.assertIn("hash", disk_rec)
        self.assertNotIn("instance_sig", disk_rec)


class TestM3NoHashChainNoSig(unittest.TestCase):
    """Test 5: hash_chain=False events must not receive instance_sig."""

    def setUp(self):
        self.path = _tmpfile()
        self.fake_iid = _make_fake_iid()

    def tearDown(self):
        self.path.unlink(missing_ok=True)

    def test_no_sig_when_hash_chain_false(self):
        """Events written with hash_chain=False must not carry instance_sig."""
        with mock.patch.dict("sys.modules", {
            "operator.bridges.shared.instance_identity": self.fake_iid,
            "instance_identity": self.fake_iid,
        }):
            rec = write_event(self.path, "tool.created", hash_chain=False)

        self.assertNotIn("hash", rec, "hash_chain=False must not produce 'hash'")
        self.assertNotIn("instance_sig", rec, "hash_chain=False must not produce 'instance_sig'")
        self.assertNotIn("instance_id", rec, "hash_chain=False must not produce 'instance_id'")

        disk_rec = _read_last(self.path)
        self.assertNotIn("instance_sig", disk_rec)
        self.assertNotIn("instance_id", disk_rec)


@unittest.skipUnless(_CRYPTO_OK, "cryptography package not installed")
class TestM3VerifyChainSigChecks(unittest.TestCase):
    """Tests 6, 7, 8: verify_chain integration with _VERIFY_SIGS flag."""

    def setUp(self):
        self.path = _tmpfile()
        self.fake_iid = _make_fake_iid()

    def tearDown(self):
        self.path.unlink(missing_ok=True)
        se._VERIFY_SIGS = False  # Reset directly on module to avoid stale-ref issues

    def _write_signed_event(self):
        """Write a hash-chained event with a real instance sig."""
        with mock.patch.dict("sys.modules", {
            "operator.bridges.shared.instance_identity": self.fake_iid,
            "instance_identity": self.fake_iid,
        }):
            return write_event(self.path, "tool.created", hash_chain=True)

    def test_verify_chain_passes_with_valid_sig(self):
        """Test 7: verify_chain + _VERIFY_SIGS=True passes when sig is valid."""
        self._write_signed_event()

        se._VERIFY_SIGS = True
        with mock.patch.dict("sys.modules", {
            "operator.bridges.shared.instance_identity": self.fake_iid,
            "instance_identity": self.fake_iid,
        }):
            ok, problems = verify_chain(self.path)

        sig_problems = [p for p in problems if "instance_sig" in p.get("issue", "")]
        self.assertEqual(sig_problems, [],
                         f"Expected no sig problems but got: {sig_problems}")

    def test_verify_chain_fails_with_tampered_sig(self):
        """Test 6: verify_chain + _VERIFY_SIGS=True catches a tampered instance_sig."""
        self._write_signed_event()

        # Tamper with the sig: flip one char
        lines = self.path.read_text().splitlines()
        last = json.loads(lines[-1])
        self.assertIn("instance_sig", last,
                      "instance_sig must be present in written record")
        # Tamper the FIRST character (position 0 encodes the top 6 data bits — never
        # padding). Changing the last character of a 64-byte Ed25519 sig is unreliable:
        # Python's b64decode silently ignores the lower 4 bits at that position, so
        # 'A' and 'B' may decode to identical bytes and the verify call spuriously passes.
        orig_first = last["instance_sig"][0]
        bad_first = "B" if orig_first != "B" else "C"
        bad_sig = bad_first + last["instance_sig"][1:]
        last["instance_sig"] = bad_sig
        self.path.write_text("\n".join(lines[:-1] + [json.dumps(last)]) + "\n")

        # Set the flag directly on the active module object to avoid stale-reference issues.
        se._VERIFY_SIGS = True
        self.assertTrue(se._VERIFY_SIGS, "se._VERIFY_SIGS must be True before verify")
        with mock.patch.dict("sys.modules", {
            "operator.bridges.shared.instance_identity": self.fake_iid,
            "instance_identity": self.fake_iid,
        }):
            ok, problems = verify_chain(self.path)

        sig_problems = [p for p in problems if "instance_sig" in p.get("issue", "")]
        self.assertTrue(len(sig_problems) > 0,
                        f"Expected instance_sig_invalid problem, got problems={problems}")
        self.assertEqual(sig_problems[0]["issue"], "instance_sig_invalid")

    def test_verify_chain_skips_sig_when_flag_false(self):
        """Test 8: verify_chain with _VERIFY_SIGS=False skips instance_sig checks entirely."""
        self._write_signed_event()

        # Corrupt the sig — verify_chain should not catch it when flag is off
        lines = self.path.read_text().splitlines()
        last = json.loads(lines[-1])
        last["instance_sig"] = "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
        self.path.write_text("\n".join(lines[:-1] + [json.dumps(last)]) + "\n")

        set_verify_sigs(False)
        with mock.patch.dict("sys.modules", {
            "operator.bridges.shared.instance_identity": self.fake_iid,
            "instance_identity": self.fake_iid,
        }):
            ok, problems = verify_chain(self.path)

        sig_problems = [p for p in problems if "instance_sig" in p.get("issue", "")]
        self.assertEqual(sig_problems, [],
                         "With _VERIFY_SIGS=False, no instance_sig problems should be reported")

    def test_verify_chain_skips_foreign_instance_id(self):
        """Verify that records with a foreign instance_id are silently skipped."""
        self._write_signed_event()

        # Replace instance_id with a foreign one so verify cannot look up the pubkey
        lines = self.path.read_text().splitlines()
        last = json.loads(lines[-1])
        last["instance_id"] = "foreign-instance-id-9999"
        self.path.write_text("\n".join(lines[:-1] + [json.dumps(last)]) + "\n")

        set_verify_sigs(True)
        with mock.patch.dict("sys.modules", {
            "operator.bridges.shared.instance_identity": self.fake_iid,
            "instance_identity": self.fake_iid,
        }):
            ok, problems = verify_chain(self.path)

        sig_problems = [p for p in problems if "instance_sig" in p.get("issue", "")]
        self.assertEqual(sig_problems, [],
                         "Foreign instance_id should be skipped, not flagged as invalid")


if __name__ == "__main__":
    unittest.main()
