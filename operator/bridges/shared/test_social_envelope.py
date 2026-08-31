"""Tests for social_envelope.py — Layer 39 CorvinFed."""
from __future__ import annotations

import os
import sys
import time
import unittest
import uuid
from pathlib import Path

_here = Path(__file__).resolve().parent
if str(_here) not in sys.path:
    sys.path.insert(0, str(_here))

import social_envelope  # noqa: E402
from social_envelope import (  # noqa: E402
    EnvelopeError,
    build_envelope,
    generate_keypair,
    sign_envelope,
    validate_envelope_schema,
    verify_envelope,
)


def _make_envelope(**overrides) -> dict:
    """Build a minimal, valid unsigned envelope dict."""
    priv, pub = generate_keypair()
    env = build_envelope(
        actor_id="actor-001",
        post_type="status",
        visibility="public",
        content="Hello CorvinFed",
        is_ai=True,
        key_id="https://example.com/actor/actor-001#key",
    )
    env.update(overrides)
    return env, priv, pub


def _signed(**overrides):
    """Return (signed_envelope_dict, public_key_hex)."""
    env, priv, pub = _make_envelope(**overrides)
    env["signature"] = sign_envelope(env, priv)
    return env, pub


# ---------------------------------------------------------------------------
# generate_keypair
# ---------------------------------------------------------------------------


class TestGenerateKeypair(unittest.TestCase):
    def test_keypair_length(self):
        priv, pub = generate_keypair()
        self.assertEqual(len(priv), 64, "private key must be 64 hex chars")
        self.assertEqual(len(pub), 64, "public key must be 64 hex chars")

    def test_keypair_unique(self):
        priv1, pub1 = generate_keypair()
        priv2, pub2 = generate_keypair()
        self.assertNotEqual(priv1, priv2, "two private keys must differ")
        self.assertNotEqual(pub1, pub2, "two public keys must differ")


# ---------------------------------------------------------------------------
# sign_and_verify — happy path
# ---------------------------------------------------------------------------


class TestSignAndVerify(unittest.TestCase):
    def test_sign_verify_status(self):
        env, pub = _signed(post_type="status")
        self.assertTrue(verify_envelope(env, pub))

    def test_sign_verify_follow(self):
        env, pub = _signed(post_type="follow")
        self.assertTrue(verify_envelope(env, pub))

    def test_sign_verify_retract(self):
        env, pub = _signed(post_type="retract")
        self.assertTrue(verify_envelope(env, pub))

    def test_sign_verify_boost(self):
        env, pub = _signed(post_type="boost", boost_of=str(uuid.uuid4()))
        self.assertTrue(verify_envelope(env, pub))

    def test_sign_verify_with_tags(self):
        env, pub = _signed(tags=["ai", "news"])
        self.assertTrue(verify_envelope(env, pub))

    def test_sign_verify_is_ai_true(self):
        env, pub = _signed(is_ai=True)
        self.assertTrue(verify_envelope(env, pub))


# ---------------------------------------------------------------------------
# tamper detection
# ---------------------------------------------------------------------------


class TestTamperDetection(unittest.TestCase):
    def test_tamper_content(self):
        env, pub = _signed()
        env["content"] = "tampered content"
        self.assertFalse(verify_envelope(env, pub))

    def test_tamper_is_ai(self):
        """Flipping is_ai True→False must invalidate the signature.

        This is a KEY invariant from ADR-0053: is_ai is part of the signed payload.
        """
        env, pub = _signed(is_ai=True)
        env["is_ai"] = False
        self.assertFalse(verify_envelope(env, pub))

    def test_tamper_actor_id(self):
        env, pub = _signed()
        env["actor_id"] = "attacker-999"
        self.assertFalse(verify_envelope(env, pub))

    def test_tamper_post_type(self):
        env, pub = _signed(post_type="status")
        env["post_type"] = "announce"
        self.assertFalse(verify_envelope(env, pub))

    def test_wrong_key(self):
        env, _ = _signed()
        _, other_pub = generate_keypair()
        self.assertFalse(verify_envelope(env, other_pub))


# ---------------------------------------------------------------------------
# build_envelope defaults
# ---------------------------------------------------------------------------


class TestBuildEnvelopeDefaults(unittest.TestCase):
    def setUp(self):
        self._env, self._priv, self._pub = _make_envelope()

    def test_build_defaults(self):
        env = self._env
        self.assertIsNone(env["content_warning"])
        self.assertIsNone(env["in_reply_to"])
        self.assertIsNone(env["boost_of"])
        self.assertIsNone(env["ai_model"])
        self.assertEqual(env["tags"], [])
        self.assertEqual(env["attachments"], [])

    def test_build_uuid4_format(self):
        env = self._env
        parsed = uuid.UUID(env["post_id"], version=4)
        self.assertEqual(str(parsed), env["post_id"])

    def test_build_issued_at_recent(self):
        env = self._env
        now = time.time()
        self.assertAlmostEqual(env["issued_at"], now, delta=5.0)


# ---------------------------------------------------------------------------
# validate_envelope_schema
# ---------------------------------------------------------------------------


def _valid_envelope_dict() -> dict:
    env, priv, _ = _make_envelope()
    env["signature"] = sign_envelope(env, priv)
    return env


class TestValidateEnvelopeSchema(unittest.TestCase):
    def test_schema_ok(self):
        env = _valid_envelope_dict()
        # must not raise
        validate_envelope_schema(env)

    def test_schema_missing_post_id(self):
        env = _valid_envelope_dict()
        del env["post_id"]
        with self.assertRaises(EnvelopeError):
            validate_envelope_schema(env)

    def test_schema_missing_is_ai(self):
        env = _valid_envelope_dict()
        del env["is_ai"]
        with self.assertRaises(EnvelopeError):
            validate_envelope_schema(env)

    def test_schema_invalid_post_type(self):
        env = _valid_envelope_dict()
        env["post_type"] = "INVALID_TYPE"
        with self.assertRaises(EnvelopeError):
            validate_envelope_schema(env)

    def test_schema_invalid_visibility(self):
        env = _valid_envelope_dict()
        env["visibility"] = "secret"
        with self.assertRaises(EnvelopeError):
            validate_envelope_schema(env)

    def test_schema_content_too_long(self):
        env = _valid_envelope_dict()
        env["content"] = "x" * 2001
        with self.assertRaises(EnvelopeError):
            validate_envelope_schema(env)

    def test_schema_too_many_tags(self):
        env = _valid_envelope_dict()
        env["tags"] = [f"tag{i}" for i in range(11)]
        with self.assertRaises(EnvelopeError):
            validate_envelope_schema(env)

    def test_schema_too_many_attachments(self):
        env = _valid_envelope_dict()
        env["attachments"] = [{} for _ in range(5)]
        with self.assertRaises(EnvelopeError):
            validate_envelope_schema(env)


# ---------------------------------------------------------------------------
# verify returns False (never raises) on bad input
# ---------------------------------------------------------------------------


class TestVerifyReturnsFalseOnBadInput(unittest.TestCase):
    def test_verify_corrupt_signature(self):
        env, pub = _signed()
        env["signature"] = "deadbeef" * 8
        self.assertFalse(verify_envelope(env, pub))

    def test_verify_missing_signature(self):
        env, pub = _signed()
        del env["signature"]
        self.assertFalse(verify_envelope(env, pub))

    def test_verify_bad_public_key(self):
        env, _ = _signed()
        self.assertFalse(verify_envelope(env, "00112233"))  # too short


if __name__ == "__main__":
    unittest.main()
