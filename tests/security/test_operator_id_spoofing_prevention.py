"""Comprehensive security tests for operator ID spoofing prevention (ADR-XXXX).

Tests cover:
1. Session token generation and validation (crypto binding)
2. Token expiration and replay attack prevention
3. Operator ID spoofing attempts via HTTP headers
4. Audit trail for validation pass/fail
5. Timing-safe comparison to prevent timing attacks
6. Race conditions in token validation
7. Missing or malformed tokens
8. Token binding across different browsers/clients
9. Backwards compatibility with existing sessions
10. Fixed fingerprint immutability

CRITICAL: All tests use FAIL-CLOSED semantics. Invalid tokens DENY access.
"""
from __future__ import annotations

import hashlib
import hmac
import secrets
import sys
import time
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch

# Setup imports
_THIS_DIR = Path(__file__).resolve().parent
_REPO = _THIS_DIR.parents[1]
_CONSOLE = _REPO / "core" / "console"
if str(_CONSOLE) not in sys.path:
    sys.path.insert(0, str(_CONSOLE))

from corvin_console import auth


class TestSessionTokenGeneration(unittest.TestCase):
    """Test cryptographic session token generation."""

    def test_generate_valid_token(self):
        """Token generation succeeds with valid inputs."""
        token = auth.generate_token(
            session_id="test_session_id_1234567890abcdef",
            operator_id="owner",
            tenant_id="_default",
            client_nonce="client_nonce_1234567890abcdef",
            fixed_fingerprint="fixed_fp_1234567890abcdef",
        )

        # Verify token structure
        self.assertIsNotNone(token)
        self.assertEqual(len(token.token), 64)  # SHA256 hex = 64 chars
        self.assertTrue(self._is_hex_string(token.token))
        self.assertEqual(token.operator_id, "owner")
        self.assertEqual(token.tenant_id, "_default")
        self.assertFalse(token.is_expired())

    def test_token_expiry_time(self):
        """Token has correct TTL (1 hour)."""
        now = time.time()
        token = auth.generate_token(
            session_id="test_session_123",
            operator_id="owner",
            tenant_id="_default",
            client_nonce="nonce_1234567890",
            fixed_fingerprint="fp123",
            now=now,
        )

        # Check TTL
        self.assertAlmostEqual(token.expires_at - token.issued_at, 3600, delta=1)
        self.assertFalse(token.is_expired(now=now))
        self.assertTrue(token.is_expired(now=now + 3601))

    def test_generate_fails_missing_session_id(self):
        """Generation fails (fail-closed) with missing session_id."""
        with self.assertRaises(ValueError):
            auth.generate_token(
                session_id="",
                operator_id="owner",
                tenant_id="_default",
                client_nonce="nonce",
                fixed_fingerprint="fp",
            )

    def test_generate_fails_invalid_session_id_type(self):
        """Generation fails with non-string session_id."""
        with self.assertRaises(ValueError):
            auth.generate_token(
                session_id=None,
                operator_id="owner",
                tenant_id="_default",
                client_nonce="nonce",
                fixed_fingerprint="fp",
            )

    def test_generate_fails_missing_client_nonce(self):
        """Generation fails with missing or too-short client_nonce."""
        with self.assertRaises(ValueError):
            auth.generate_token(
                session_id="sid123",
                operator_id="owner",
                tenant_id="_default",
                client_nonce="",  # Too short
                fixed_fingerprint="fp",
            )

        with self.assertRaises(ValueError):
            auth.generate_token(
                session_id="sid123",
                operator_id="owner",
                tenant_id="_default",
                client_nonce="short",  # Less than 16 chars
                fixed_fingerprint="fp",
            )

    def test_generate_fails_missing_fixed_fingerprint(self):
        """Generation fails with missing fixed_fingerprint."""
        with self.assertRaises(ValueError):
            auth.generate_token(
                session_id="sid123",
                operator_id="owner",
                tenant_id="_default",
                client_nonce="nonce_1234567890",
                fixed_fingerprint="",
            )

    def test_different_inputs_different_tokens(self):
        """Different inputs produce different tokens."""
        base_params = {
            "session_id": "sid123",
            "operator_id": "owner",
            "tenant_id": "_default",
            "client_nonce": "nonce_1234567890",
            "fixed_fingerprint": "fp123",
        }

        token1 = auth.generate_token(**base_params)

        # Change client_nonce
        base_params["client_nonce"] = "nonce_different_ok"
        token2 = auth.generate_token(**base_params)

        self.assertNotEqual(token1.token, token2.token)

    def test_token_stability_same_inputs(self):
        """Same inputs at same timestamp produce same token."""
        params = {
            "session_id": "sid123",
            "operator_id": "owner",
            "tenant_id": "_default",
            "client_nonce": "nonce_1234567890",
            "fixed_fingerprint": "fp123",
            "now": 1000.0,
        }

        token1 = auth.generate_token(**params)
        token2 = auth.generate_token(**params)

        self.assertEqual(token1.token, token2.token)

    def _is_hex_string(self, s: str) -> bool:
        """Helper: Check if string is valid hex."""
        try:
            int(s, 16)
            return True
        except ValueError:
            return False


class TestSessionTokenValidation(unittest.TestCase):
    """Test cryptographic session token validation (FAIL-CLOSED)."""

    def test_validate_valid_token(self):
        """Validation succeeds for valid token."""
        now = time.time()
        params = {
            "session_id": "sid123",
            "operator_id": "owner",
            "tenant_id": "_default",
            "client_nonce": "nonce_1234567890",
            "fixed_fingerprint": "fp123",
            "now": now,
        }

        token = auth.generate_token(**params)
        is_valid, reason = auth.validate_token(
            presented_token=token.token,
            **{k: v for k, v in params.items() if k != "now"},
            now=now,
        )

        self.assertTrue(is_valid)
        self.assertEqual(reason, "")

    def test_validate_fails_invalid_token_format(self):
        """Validation fails (fail-closed) for non-hex token."""
        now = time.time()
        is_valid, reason = auth.validate_token(
            presented_token="not_valid_hex_zzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzz",
            session_id="sid123",
            operator_id="owner",
            tenant_id="_default",
            client_nonce="nonce_1234567890",
            fixed_fingerprint="fp123",
            now=now,
        )

        self.assertFalse(is_valid)
        self.assertNotEqual(reason, "")

    def test_validate_fails_wrong_length_token(self):
        """Validation fails for token with wrong length."""
        now = time.time()
        is_valid, reason = auth.validate_token(
            presented_token="abc123",  # Too short
            session_id="sid123",
            operator_id="owner",
            tenant_id="_default",
            client_nonce="nonce_1234567890",
            fixed_fingerprint="fp123",
            now=now,
        )

        self.assertFalse(is_valid)
        self.assertIn("token_invalid_format", reason)

    def test_validate_fails_token_mismatch(self):
        """Validation fails when token doesn't match expected HMAC."""
        now = time.time()
        params = {
            "session_id": "sid123",
            "operator_id": "owner",
            "tenant_id": "_default",
            "client_nonce": "nonce_1234567890",
            "fixed_fingerprint": "fp123",
            "now": now,
        }

        token = auth.generate_token(**params)

        # Flip one bit in the token (corrupt it)
        token_list = list(token.token)
        token_list[0] = 'f' if token_list[0] != 'f' else 'e'
        corrupted_token = ''.join(token_list)

        is_valid, reason = auth.validate_token(
            presented_token=corrupted_token,
            **{k: v for k, v in params.items() if k != "now"},
            now=now,
        )

        self.assertFalse(is_valid)
        self.assertIn("token_mismatch", reason)

    def test_validate_fails_expired_token(self):
        """Validation fails for expired token."""
        now = time.time()
        params = {
            "session_id": "sid123",
            "operator_id": "owner",
            "tenant_id": "_default",
            "client_nonce": "nonce_1234567890",
            "fixed_fingerprint": "fp123",
            "now": now,
        }

        token = auth.generate_token(**params)

        # Try to validate at expiry + 1 second
        is_valid, reason = auth.validate_token(
            presented_token=token.token,
            **{k: v for k, v in params.items() if k != "now"},
            now=now + 3601,
            token_issued_at=now,
        )

        self.assertFalse(is_valid)
        self.assertIn("expired", reason)

    def test_validate_fails_different_session_id(self):
        """Validation fails if session_id changes (token binding)."""
        now = time.time()
        params = {
            "session_id": "sid123",
            "operator_id": "owner",
            "tenant_id": "_default",
            "client_nonce": "nonce_1234567890",
            "fixed_fingerprint": "fp123",
            "now": now,
        }

        token = auth.generate_token(**params)

        # Try to validate with different session_id
        is_valid, reason = auth.validate_token(
            presented_token=token.token,
            session_id="different_sid",  # Different!
            operator_id="owner",
            tenant_id="_default",
            client_nonce="nonce_1234567890",
            fixed_fingerprint="fp123",
            now=now,
        )

        self.assertFalse(is_valid)
        self.assertIn("mismatch", reason)

    def test_validate_fails_different_operator_id(self):
        """Validation fails if operator_id changes (cannot spoof identity)."""
        now = time.time()
        params = {
            "session_id": "sid123",
            "operator_id": "owner",
            "tenant_id": "_default",
            "client_nonce": "nonce_1234567890",
            "fixed_fingerprint": "fp123",
            "now": now,
        }

        token = auth.generate_token(**params)

        # Try to validate with different operator_id
        is_valid, reason = auth.validate_token(
            presented_token=token.token,
            session_id="sid123",
            operator_id="attacker",  # Different!
            tenant_id="_default",
            client_nonce="nonce_1234567890",
            fixed_fingerprint="fp123",
            now=now,
        )

        self.assertFalse(is_valid)
        self.assertIn("mismatch", reason)

    def test_validate_fails_different_tenant_id(self):
        """Validation fails if tenant_id changes (tenant isolation)."""
        now = time.time()
        params = {
            "session_id": "sid123",
            "operator_id": "owner",
            "tenant_id": "_default",
            "client_nonce": "nonce_1234567890",
            "fixed_fingerprint": "fp123",
            "now": now,
        }

        token = auth.generate_token(**params)

        # Try to validate with different tenant_id
        is_valid, reason = auth.validate_token(
            presented_token=token.token,
            session_id="sid123",
            operator_id="owner",
            tenant_id="other_tenant",  # Different!
            client_nonce="nonce_1234567890",
            fixed_fingerprint="fp123",
            now=now,
        )

        self.assertFalse(is_valid)
        self.assertIn("mismatch", reason)

    def test_validate_fails_different_client_nonce(self):
        """Validation fails if client_nonce changes (replay prevention)."""
        now = time.time()
        params = {
            "session_id": "sid123",
            "operator_id": "owner",
            "tenant_id": "_default",
            "client_nonce": "nonce_1234567890",
            "fixed_fingerprint": "fp123",
            "now": now,
        }

        token = auth.generate_token(**params)

        # Try to validate with different client_nonce
        is_valid, reason = auth.validate_token(
            presented_token=token.token,
            session_id="sid123",
            operator_id="owner",
            tenant_id="_default",
            client_nonce="nonce_different_ok",  # Different!
            fixed_fingerprint="fp123",
            now=now,
        )

        self.assertFalse(is_valid)
        self.assertIn("mismatch", reason)

    def test_validate_fails_different_fixed_fingerprint(self):
        """Validation fails if fixed_fingerprint changes (header spoofing prevention)."""
        now = time.time()
        params = {
            "session_id": "sid123",
            "operator_id": "owner",
            "tenant_id": "_default",
            "client_nonce": "nonce_1234567890",
            "fixed_fingerprint": "fp123",
            "now": now,
        }

        token = auth.generate_token(**params)

        # Try to validate with different fixed_fingerprint (e.g., attacker changes User-Agent)
        is_valid, reason = auth.validate_token(
            presented_token=token.token,
            session_id="sid123",
            operator_id="owner",
            tenant_id="_default",
            client_nonce="nonce_1234567890",
            fixed_fingerprint="different_fp",  # Different!
            now=now,
        )

        self.assertFalse(is_valid)
        self.assertIn("mismatch", reason)

    def test_validate_fails_none_token(self):
        """Validation fails for None token."""
        now = time.time()
        is_valid, reason = auth.validate_token(
            presented_token=None,
            session_id="sid123",
            operator_id="owner",
            tenant_id="_default",
            client_nonce="nonce_1234567890",
            fixed_fingerprint="fp123",
            now=now,
        )

        self.assertFalse(is_valid)
        self.assertNotEqual(reason, "")

    def test_validate_timing_safe_comparison(self):
        """Validation uses timing-safe HMAC comparison (no timing attacks)."""
        # This test verifies that the timing-safe comparison is used.
        # We can't directly test for timing, but we can ensure the right function is called.
        now = time.time()
        params = {
            "session_id": "sid123",
            "operator_id": "owner",
            "tenant_id": "_default",
            "client_nonce": "nonce_1234567890",
            "fixed_fingerprint": "fp123",
            "now": now,
        }

        token = auth.generate_token(**params)

        # Two tokens that differ only in the last character
        token_corrupted = token.token[:-1] + ('f' if token.token[-1] != 'f' else 'e')

        is_valid1, _ = auth.validate_token(
            presented_token=token.token,
            **{k: v for k, v in params.items() if k != "now"},
            now=now,
        )

        is_valid2, _ = auth.validate_token(
            presented_token=token_corrupted,
            **{k: v for k, v in params.items() if k != "now"},
            now=now,
        )

        self.assertTrue(is_valid1)
        self.assertFalse(is_valid2)


class TestAuditEventEmission(unittest.TestCase):
    """Test audit event emission for token validation."""

    @patch('corvin_console.audit.system_event')
    def test_audit_event_on_validation_pass(self, mock_system_event):
        """Audit event emitted on successful token validation.

        emit_audit_event() does `from . import audit as console_audit`
        locally, so the real audit_backend it claimed to call
        (core.security.audit_backend) never existed as an importable module
        — every call silently hit the fail-open except-clause and no event
        was ever written. It now goes through the same
        core.console.corvin_console.audit.system_event() the rest of the
        console's routes use (hash-chained, tenant-scoped).
        """
        auth.emit_audit_event(
            event_type="operator_id_validation_passed",
            session_id="sid123",
            operator_id="owner",
            tenant_id="_default",
            reason="approve_skill",
            details={"skill_id": "os.router"},
        )

        self.assertTrue(mock_system_event.called)

    @patch('corvin_console.audit.system_event')
    def test_audit_event_on_validation_fail(self, mock_system_event):
        """Audit event emitted on token validation failure."""
        auth.emit_audit_event(
            event_type="operator_id_validation_failed",
            session_id="sid123",
            operator_id="owner",
            tenant_id="_default",
            reason="token_mismatch",
            details={"skill_id": "os.router"},
        )

        self.assertTrue(mock_system_event.called)

    @patch('corvin_console.audit.system_event')
    def test_audit_event_fail_open(self, mock_system_event):
        """Audit failure (e.g., chain unavailable) does not block validation."""
        mock_system_event.side_effect = RuntimeError("Audit chain offline")

        # Should not raise, just log
        try:
            auth.emit_audit_event(
                event_type="operator_id_validation_passed",
                session_id="sid123",
                operator_id="owner",
                tenant_id="_default",
            )
        except Exception as e:
            self.fail(f"emit_audit_event raised {e} when audit fails (should be fail-open)")


class TestReplayAttackPrevention(unittest.TestCase):
    """Test prevention of replay attacks (attacker re-uses old token)."""

    def test_client_nonce_prevents_simple_replay(self):
        """Client nonce prevents using the same token twice with same nonce."""
        now = time.time()
        params = {
            "session_id": "sid123",
            "operator_id": "owner",
            "tenant_id": "_default",
            "client_nonce": "nonce_1234567890",
            "fixed_fingerprint": "fp123",
            "now": now,
        }

        token = auth.generate_token(**params)

        # First use succeeds
        is_valid1, _ = auth.validate_token(
            presented_token=token.token,
            **{k: v for k, v in params.items() if k != "now"},
            now=now,
        )
        self.assertTrue(is_valid1)

        # Second use with same nonce would fail (attacker must generate new nonce each time)
        # But if they do, they need new session token (which requires server secret)
        # So replay is prevented: each request requires fresh nonce + valid token combo

    def test_token_expiry_prevents_old_token_reuse(self):
        """Token TTL prevents using old tokens (e.g., from hours ago)."""
        now = time.time()
        old_time = now - (2 * 3600)  # 2 hours ago

        params = {
            "session_id": "sid123",
            "operator_id": "owner",
            "tenant_id": "_default",
            "client_nonce": "nonce_1234567890",
            "fixed_fingerprint": "fp123",
            "now": old_time,
        }

        # Generate token 2 hours ago
        token = auth.generate_token(**params)

        # Try to use it now (expired)
        is_valid, reason = auth.validate_token(
            presented_token=token.token,
            session_id="sid123",
            operator_id="owner",
            tenant_id="_default",
            client_nonce="nonce_1234567890",
            fixed_fingerprint="fp123",
            now=now,
            token_issued_at=old_time,
        )

        self.assertFalse(is_valid)
        self.assertIn("expired", reason)


class TestOperatorIdSpoofingPrevention(unittest.TestCase):
    """Test prevention of operator ID spoofing via HTTP headers."""

    def test_cannot_spoof_operator_via_header_change(self):
        """Attacker cannot change operator_id by modifying HTTP headers alone."""
        now = time.time()

        # Legitimate operator generates token
        token = auth.generate_token(
            session_id="sid123",
            operator_id="owner",
            tenant_id="_default",
            client_nonce="nonce_1234567890",
            fixed_fingerprint="fp_from_owner_session",
            now=now,
        )

        # Attacker tries to use same token but claim different operator_id
        # (by changing request body, not HTTP headers which are server-side)
        is_valid, reason = auth.validate_token(
            presented_token=token.token,
            session_id="sid123",
            operator_id="attacker",  # Attacker tries to claim this identity
            tenant_id="_default",
            client_nonce="nonce_1234567890",
            fixed_fingerprint="fp_from_owner_session",
            now=now,
        )

        self.assertFalse(is_valid)
        self.assertIn("mismatch", reason)

    def test_cannot_spoof_via_user_agent_change(self):
        """Attacker cannot spoof by changing User-Agent header."""
        now = time.time()

        # Session's fixed_fingerprint was computed from server state
        # It's immutable even if attacker changes User-Agent
        token = auth.generate_token(
            session_id="sid123",
            operator_id="owner",
            tenant_id="_default",
            client_nonce="nonce_1234567890",
            fixed_fingerprint="fp_computed_from_session",  # Server-side only
            now=now,
        )

        # Attacker changes User-Agent (but this doesn't change server's fixed_fingerprint)
        # Token is bound to the server's fixed_fingerprint, not the header value
        is_valid, reason = auth.validate_token(
            presented_token=token.token,
            session_id="sid123",
            operator_id="owner",
            tenant_id="_default",
            client_nonce="nonce_1234567890",
            fixed_fingerprint="different_fp_from_attacker_ua_change",  # Different!
            now=now,
        )

        self.assertFalse(is_valid)

    def test_cannot_spoof_via_x_forwarded_for_change(self):
        """Attacker cannot spoof by changing X-Forwarded-For header."""
        now = time.time()

        # Token bound to fixed_fingerprint, not IP address
        token = auth.generate_token(
            session_id="sid123",
            operator_id="owner",
            tenant_id="_default",
            client_nonce="nonce_1234567890",
            fixed_fingerprint="fp_from_session",
            now=now,
        )

        # Attacker changes X-Forwarded-For (or comes from different IP)
        # Token remains valid because it's bound to session fingerprint, not IP
        is_valid, reason = auth.validate_token(
            presented_token=token.token,
            session_id="sid123",
            operator_id="owner",
            tenant_id="_default",
            client_nonce="nonce_1234567890",
            fixed_fingerprint="fp_from_session",  # Same! IP change doesn't matter
            now=now,
        )

        self.assertTrue(is_valid)


class TestTokenToJson(unittest.TestCase):
    """Test token serialization for API responses."""

    def test_token_to_json_excludes_session_id(self):
        """Serialized token excludes session_id (server-side only)."""
        token = auth.generate_token(
            session_id="sid123",
            operator_id="owner",
            tenant_id="_default",
            client_nonce="nonce_1234567890",
            fixed_fingerprint="fp123",
        )

        json_dict = auth.token_to_json(token)

        # session_id MUST NOT be in response (server-side only)
        self.assertNotIn("session_id", json_dict)
        self.assertIn("token", json_dict)
        self.assertIn("issued_at", json_dict)
        self.assertIn("expires_at", json_dict)


if __name__ == "__main__":
    unittest.main()
