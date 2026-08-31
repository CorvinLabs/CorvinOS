"""ADR-0153 M5 — Protocol v8 CorvinID JWT unit tests.

Tests for the corvin_id_jwt field added to TaskEnvelope and the sender's
best-effort corvin_id_jwt injection in _build_envelope().

Run:
    python3 -m pytest operator/bridges/shared/test_corvinid_m5_a2a_v8.py -v --tb=short
"""
from __future__ import annotations

import dataclasses
import hmac as _hmac
import json
import sys
import time
import types
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

# Use the main repo's operator/bridges/shared directory (no worktree fallback
# needed since M5 changes have been merged into the main branch).
_THIS_DIR = Path(__file__).resolve().parent
if str(_THIS_DIR) not in sys.path:
    sys.path.insert(0, str(_THIS_DIR))

# Purge any stale cached copies so a fresh import picks up current code.
for _mod_name in list(sys.modules.keys()):
    if _mod_name in ("remote_trigger_receiver", "remote_trigger_sender"):
        del sys.modules[_mod_name]


# ---------------------------------------------------------------------------
# Helper: build a minimal valid raw envelope dict for from_dict() tests.
# ---------------------------------------------------------------------------

def _make_raw_envelope(**overrides) -> dict:
    base = {
        "task_id": "tid-001",
        "nonce": "abc123",
        "issued_at": time.time(),
        "origin_id": "origin-test",
        "instruction": "do something",
        "result_schema": {},
        "ttl_s": 60,
        "sender_instance_id": "inst-uuid-abc",
        "attachments": [],
        "signature": "deadbeef" * 8,
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# Test 1 — TaskEnvelope dataclass has corvin_id_jwt field
# ---------------------------------------------------------------------------

class TestTaskEnvelopeField(unittest.TestCase):
    def test_field_exists(self):
        """TaskEnvelope must declare corvin_id_jwt as an optional str field."""
        from remote_trigger_receiver import TaskEnvelope  # noqa: PLC0415

        fields = {f.name: f for f in dataclasses.fields(TaskEnvelope)}
        self.assertIn(
            "corvin_id_jwt", fields,
            "TaskEnvelope must have a corvin_id_jwt field (ADR-0153 M5)",
        )

    def test_field_default_is_none(self):
        """corvin_id_jwt must default to None."""
        from remote_trigger_receiver import TaskEnvelope  # noqa: PLC0415

        fields = {f.name: f for f in dataclasses.fields(TaskEnvelope)}
        field = fields["corvin_id_jwt"]
        self.assertIs(
            field.default, None,
            "corvin_id_jwt default must be None",
        )

    def test_field_position_after_instance_attestation(self):
        """corvin_id_jwt must come after instance_attestation (ADR-0153 M5 ordering)."""
        from remote_trigger_receiver import TaskEnvelope  # noqa: PLC0415

        names = [f.name for f in dataclasses.fields(TaskEnvelope)]
        att_idx = names.index("instance_attestation")
        jwt_idx = names.index("corvin_id_jwt")
        self.assertGreater(
            jwt_idx, att_idx,
            "corvin_id_jwt must be declared after instance_attestation",
        )

    def test_module_protocol_version_is_8(self):
        """PROTOCOL_VERSION constant must be 8 (ADR-0153 M5)."""
        import remote_trigger_receiver as rtr  # noqa: PLC0415
        self.assertEqual(rtr.PROTOCOL_VERSION, 8)


# ---------------------------------------------------------------------------
# Test 2 — from_dict() parses corvin_id_jwt correctly when present
# ---------------------------------------------------------------------------

class TestFromDictParsesJwt(unittest.TestCase):
    def test_parses_valid_jwt_string(self):
        """from_dict() must populate corvin_id_jwt from a valid JWT-like string."""
        from remote_trigger_receiver import TaskEnvelope  # noqa: PLC0415

        sample_jwt = "header.payload.signature"
        raw = _make_raw_envelope(corvin_id_jwt=sample_jwt)
        env = TaskEnvelope.from_dict(raw)
        self.assertEqual(env.corvin_id_jwt, sample_jwt)

    def test_parses_realistic_jwt(self):
        """from_dict() must preserve a realistic RS256 JWT header.payload.sig string."""
        from remote_trigger_receiver import TaskEnvelope  # noqa: PLC0415

        # Fabricated compact JWT-shaped string (not cryptographically valid, just shaped right).
        fake_jwt = (
            "eyJhbGciOiJSUzI1NiIsInR5cCI6IkpXVCJ9"
            ".eyJzdWIiOiJvcGVyYXRvci0xMjMiLCJqdGkiOiJhYmMtZGVmIn0"
            ".FAKESIG" + "A" * 200
        )
        raw = _make_raw_envelope(corvin_id_jwt=fake_jwt)
        env = TaskEnvelope.from_dict(raw)
        self.assertEqual(env.corvin_id_jwt, fake_jwt)


# ---------------------------------------------------------------------------
# Test 3 — from_dict() handles None / missing corvin_id_jwt gracefully
# ---------------------------------------------------------------------------

class TestFromDictMissingJwt(unittest.TestCase):
    def test_missing_field_gives_none(self):
        """from_dict() without corvin_id_jwt key must yield corvin_id_jwt=None."""
        from remote_trigger_receiver import TaskEnvelope  # noqa: PLC0415

        raw = _make_raw_envelope()
        # Ensure key is absent
        raw.pop("corvin_id_jwt", None)
        env = TaskEnvelope.from_dict(raw)
        self.assertIsNone(env.corvin_id_jwt)

    def test_explicit_none_gives_none(self):
        """from_dict() with corvin_id_jwt=None must yield corvin_id_jwt=None."""
        from remote_trigger_receiver import TaskEnvelope  # noqa: PLC0415

        raw = _make_raw_envelope(corvin_id_jwt=None)
        env = TaskEnvelope.from_dict(raw)
        self.assertIsNone(env.corvin_id_jwt)

    def test_non_string_type_gives_none(self):
        """from_dict() with a non-string corvin_id_jwt (int, dict) must yield None."""
        from remote_trigger_receiver import TaskEnvelope  # noqa: PLC0415

        for bad_val in [42, {"a": 1}, [], True]:
            with self.subTest(bad_val=bad_val):
                raw = _make_raw_envelope(corvin_id_jwt=bad_val)
                env = TaskEnvelope.from_dict(raw)
                self.assertIsNone(
                    env.corvin_id_jwt,
                    f"Non-string {type(bad_val).__name__} must produce None",
                )


# ---------------------------------------------------------------------------
# Test 4 — from_dict() caps corvin_id_jwt at 8192 chars
# ---------------------------------------------------------------------------

class TestFromDictJwtCap(unittest.TestCase):
    def test_cap_at_8192_chars(self):
        """from_dict() must truncate corvin_id_jwt to 8192 characters."""
        from remote_trigger_receiver import TaskEnvelope  # noqa: PLC0415

        oversized = "A" * 10_000
        raw = _make_raw_envelope(corvin_id_jwt=oversized)
        env = TaskEnvelope.from_dict(raw)
        self.assertIsNotNone(env.corvin_id_jwt)
        self.assertEqual(len(env.corvin_id_jwt), 8192)
        self.assertEqual(env.corvin_id_jwt, oversized[:8192])

    def test_exactly_8192_passes_unchanged(self):
        """from_dict() must not truncate a 8192-char corvin_id_jwt."""
        from remote_trigger_receiver import TaskEnvelope  # noqa: PLC0415

        exact = "B" * 8192
        raw = _make_raw_envelope(corvin_id_jwt=exact)
        env = TaskEnvelope.from_dict(raw)
        self.assertEqual(len(env.corvin_id_jwt), 8192)

    def test_short_jwt_passes_unchanged(self):
        """from_dict() must not alter a short JWT string."""
        from remote_trigger_receiver import TaskEnvelope  # noqa: PLC0415

        short = "short.jwt.token"
        raw = _make_raw_envelope(corvin_id_jwt=short)
        env = TaskEnvelope.from_dict(raw)
        self.assertEqual(env.corvin_id_jwt, short)


# ---------------------------------------------------------------------------
# Test 5 — canonical_payload() includes corvin_id_jwt when set, omits when None
# (TaskEnvelope has no to_dict — canonical_payload() is the serialisation path)
# ---------------------------------------------------------------------------

class TestCanonicalPayloadJwt(unittest.TestCase):
    def _make_env(self, **kw):
        from remote_trigger_receiver import TaskEnvelope  # noqa: PLC0415
        return TaskEnvelope.from_dict(_make_raw_envelope(**kw))

    def test_includes_jwt_when_set(self):
        """canonical_payload() must include corvin_id_jwt in HMAC bytes when set."""
        env = self._make_env(corvin_id_jwt="hdr.pay.sig")
        payload = json.loads(env.canonical_payload())
        self.assertIn("corvin_id_jwt", payload)
        self.assertEqual(payload["corvin_id_jwt"], "hdr.pay.sig")

    def test_omits_jwt_when_none(self):
        """canonical_payload() must omit corvin_id_jwt from HMAC bytes when None."""
        env = self._make_env()  # no corvin_id_jwt
        payload = json.loads(env.canonical_payload())
        self.assertNotIn("corvin_id_jwt", payload)

    def test_signature_field_absent_from_payload(self):
        """canonical_payload() must always omit 'signature' (existing invariant)."""
        env = self._make_env(corvin_id_jwt="hdr.pay.sig")
        payload = json.loads(env.canonical_payload())
        self.assertNotIn("signature", payload)

    def test_payload_differs_with_and_without_jwt(self):
        """Two otherwise-identical envelopes should produce different HMAC payloads
        based solely on the presence of corvin_id_jwt."""
        env_with = self._make_env(corvin_id_jwt="my.jwt.token")
        env_without = self._make_env()
        # Force same issued_at so the only difference is corvin_id_jwt.
        t = 1_700_000_000.0
        object.__setattr__(env_with, "issued_at", t)
        object.__setattr__(env_without, "issued_at", t)
        # Also align nonces so only corvin_id_jwt differs.
        object.__setattr__(env_with, "nonce", "same_nonce")
        object.__setattr__(env_without, "nonce", "same_nonce")
        self.assertNotEqual(env_with.canonical_payload(), env_without.canonical_payload())


# ---------------------------------------------------------------------------
# Test 6 — Sender: get_ibc_jwt() returning a value → protocol_version=8 + corvin_id_jwt
# ---------------------------------------------------------------------------

class TestSenderInjectsCorvinIdJwt(unittest.TestCase):
    """Verify that _build_envelope() sets protocol_version=8 and corvin_id_jwt
    when _IBC_AVAILABLE=True and _get_ibc_jwt() returns a non-empty string.
    """

    def _call_build_envelope(self, ibc_jwt_return_value):
        """Patch the module-level _IBC_AVAILABLE and _get_ibc_jwt, call
        _build_envelope, and return the resulting envelope dict."""
        import remote_trigger_sender as rts  # noqa: PLC0415

        hmac_key_hex = "0102030405060708" * 4  # 32 bytes

        # Patch _IBC_AVAILABLE=True and _get_ibc_jwt to return the desired value.
        # Also patch _sign_payload and _build_canonical_payload so IBC attestation
        # code in the *same* function doesn't blow up on missing Ed25519 keys.
        with (
            patch.object(rts, "_IBC_AVAILABLE", True),
            patch.object(rts, "_get_ibc_jwt", return_value=ibc_jwt_return_value),
            patch.object(rts, "_sign_payload", return_value="fakesig"),
            patch.object(rts, "_build_canonical_payload", return_value=b"canonical"),
        ):
            # instance_attestation block imports jwt dynamically; suppress it so
            # we don't need PyJWT installed for these unit tests.
            fake_jwt_mod = types.SimpleNamespace(
                decode=lambda token, options=None: {"jti": "test-jti"}
            )
            with patch.dict("sys.modules", {"jwt": fake_jwt_mod}):
                env = rts.RemoteTriggerSender._build_envelope(
                    task_id="t1",
                    nonce="n1",
                    origin_id="o1",
                    instruction="test",
                    result_schema={},
                    ttl_s=60,
                    hmac_key_hex=hmac_key_hex,
                    sender_instance_id="iid-test",
                )
        return env

    def test_does_not_set_protocol_version_in_envelope_dict(self):
        """_build_envelope() must NOT set a top-level protocol_version key in the envelope.
        PROTOCOL_VERSION=8 is a module-level constant for capability discovery, not a
        per-envelope wire field — setting it as a top-level key would break HMAC because
        canonical_payload() uses dataclasses.asdict() which only serialises declared
        TaskEnvelope fields. (ADR-0153 M5 fix; CRITICAL review finding)"""
        env = self._call_build_envelope("hdr.pay.sig")
        self.assertNotIn(
            "protocol_version", env,
            "protocol_version must NOT appear as a top-level envelope key",
        )

    def test_sets_corvin_id_jwt_field_when_jwt_available(self):
        """_build_envelope() must include corvin_id_jwt in the envelope dict."""
        sample_jwt = "hdr.pay.sig"
        env = self._call_build_envelope(sample_jwt)
        self.assertIn("corvin_id_jwt", env)
        self.assertEqual(env["corvin_id_jwt"], sample_jwt)

    def test_caps_corvin_id_jwt_at_8192(self):
        """_build_envelope() must cap corvin_id_jwt at 8192 chars."""
        oversized = "X" * 10_000
        env = self._call_build_envelope(oversized)
        self.assertIn("corvin_id_jwt", env)
        self.assertEqual(len(env["corvin_id_jwt"]), 8192)

    def test_no_corvin_id_jwt_when_jwt_is_empty(self):
        """_build_envelope() must not add corvin_id_jwt when get_ibc_jwt() returns ''."""
        env = self._call_build_envelope("")
        self.assertNotIn("corvin_id_jwt", env)
        self.assertNotEqual(env.get("protocol_version"), 8)

    def test_no_corvin_id_jwt_when_jwt_is_none(self):
        """_build_envelope() must not add corvin_id_jwt when get_ibc_jwt() returns None."""
        env = self._call_build_envelope(None)
        self.assertNotIn("corvin_id_jwt", env)

    def test_ibc_unavailable_skips_corvin_id_jwt(self):
        """When _IBC_AVAILABLE=False, corvin_id_jwt must not appear in envelope."""
        import remote_trigger_sender as rts  # noqa: PLC0415

        hmac_key_hex = "0102030405060708" * 4

        with patch.object(rts, "_IBC_AVAILABLE", False):
            env = rts.RemoteTriggerSender._build_envelope(
                task_id="t2",
                nonce="n2",
                origin_id="o2",
                instruction="test2",
                result_schema={},
                ttl_s=60,
                hmac_key_hex=hmac_key_hex,
                sender_instance_id="iid-test2",
            )

        self.assertNotIn("corvin_id_jwt", env)
        self.assertNotEqual(env.get("protocol_version"), 8)

    def test_exception_in_get_ibc_jwt_skips_field(self):
        """_build_envelope() must continue without corvin_id_jwt if get_ibc_jwt() raises."""
        import remote_trigger_sender as rts  # noqa: PLC0415

        hmac_key_hex = "0102030405060708" * 4

        def _raise():
            raise RuntimeError("vault unavailable")

        with (
            patch.object(rts, "_IBC_AVAILABLE", True),
            patch.object(rts, "_get_ibc_jwt", side_effect=_raise),
            patch.object(rts, "_sign_payload", return_value="fakesig"),
            patch.object(rts, "_build_canonical_payload", return_value=b"canonical"),
        ):
            fake_jwt_mod = types.SimpleNamespace(
                decode=lambda token, options=None: {"jti": "test-jti"}
            )
            with patch.dict("sys.modules", {"jwt": fake_jwt_mod}):
                env = rts.RemoteTriggerSender._build_envelope(
                    task_id="t3",
                    nonce="n3",
                    origin_id="o3",
                    instruction="no-raise-on-fail",
                    result_schema={},
                    ttl_s=60,
                    hmac_key_hex=hmac_key_hex,
                    sender_instance_id="iid-test3",
                )

        self.assertNotIn("corvin_id_jwt", env)


if __name__ == "__main__":
    unittest.main()
