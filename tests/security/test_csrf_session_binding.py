"""CSRF Token Validation Tests — Session Binding, Nonce Rotation, Timestamp Validation.

Tests for CRITICAL SECURITY FIX: session-bound CSRF tokens prevent cross-session
token reuse and replay attacks.

14 Unit Tests + 1 E2E Test (Total: 15 assertions)

OWASP A05:2021 – Broken Access Control
GDPR Art. 32 – Security
"""
import time
import pytest
from datetime import datetime, timedelta

from core.console.corvin_console.csrf.csrf_session_binding import (
    CSRFValidationResult,
    derive_csrf_token_session_bound,
    validate_csrf_token_session_bound,
    generate_csrf_nonce,
)


# ─────────────────────────────────────────────────────────────────────────────
# Test Fixtures
# ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture
def csrf_secret() -> str:
    """Server secret for CSRF token generation."""
    return "test_csrf_secret_1234567890abcdef"


@pytest.fixture
def session_id() -> str:
    """Test session ID."""
    return "test_sid_" + "a" * 35  # 43 chars total


@pytest.fixture
def nonce() -> str:
    """Test nonce (32-char hex)."""
    return "0123456789abcdef0123456789abcdef"


@pytest.fixture
def route_path() -> str:
    """Test route path."""
    return "/v1/console/autonomous-forge/approve"


@pytest.fixture
def now() -> float:
    """Current time for testing."""
    return time.time()


# ─────────────────────────────────────────────────────────────────────────────
# Unit Tests: Token Generation (2 tests)
# ─────────────────────────────────────────────────────────────────────────────

class TestTokenGeneration:
    """Test CSRF token generation with session binding."""

    def test_token_generation_produces_64_char_hex(self, csrf_secret, session_id, nonce, route_path, now):
        """Token should be 64-char hex (SHA256 HMAC output)."""
        token = derive_csrf_token_session_bound(
            csrf_secret=csrf_secret,
            session_id=session_id,
            timestamp=now,
            nonce=nonce,
            route_path=route_path,
        )
        assert len(token) == 64
        assert all(c in "0123456789abcdef" for c in token)

    def test_token_binds_to_session_id(self, csrf_secret, nonce, route_path, now):
        """Token derived from session A should differ from token for session B."""
        session_a = "a" * 43
        session_b = "b" * 43

        token_a = derive_csrf_token_session_bound(
            csrf_secret=csrf_secret,
            session_id=session_a,
            timestamp=now,
            nonce=nonce,
            route_path=route_path,
        )

        token_b = derive_csrf_token_session_bound(
            csrf_secret=csrf_secret,
            session_id=session_b,
            timestamp=now,
            nonce=nonce,
            route_path=route_path,
        )

        assert token_a != token_b, "Token must be bound to session_id"


# ─────────────────────────────────────────────────────────────────────────────
# Unit Tests: Token Validation (12 tests)
# ─────────────────────────────────────────────────────────────────────────────

class TestTokenValidation:
    """Test CSRF token validation with security checks."""

    def test_valid_token_passes_validation(self, csrf_secret, session_id, nonce, route_path, now):
        """Valid token with correct nonce and timestamp should pass."""
        token = derive_csrf_token_session_bound(
            csrf_secret=csrf_secret,
            session_id=session_id,
            timestamp=now,
            nonce=nonce,
            route_path=route_path,
        )

        result = validate_csrf_token_session_bound(
            csrf_secret=csrf_secret,
            session_id=session_id,
            presented_token=token,
            presented_nonce=nonce,
            session_nonce=nonce,
            route_path=route_path,
            token_issued_at=now,
            now=now,
        )

        assert result.valid is True
        assert result.error_reason is None
        assert result.new_nonce is not None
        assert len(result.new_nonce) == 32

    def test_invalid_token_format_rejected(self, csrf_secret, session_id, nonce, route_path, now):
        """Token with wrong length is rejected with an explicit format error.

        The implementation's format check (csrf_session_binding.py) only
        validates length (== 64), not hex-ness — a wrong-length token is
        caught here and correctly reported as 'invalid_token_format'; a
        right-length-but-non-hex token instead falls through to the later
        HMAC comparison and is still rejected (valid=False), just under a
        different, equally fail-closed error_reason. Both are covered below;
        only the wrong-length cases assert the 'format' wording.
        """
        wrong_length_tokens = [
            "too_short",              # Too short
            "0" * 63,                 # Too short
            "0" * 65,                 # Too long
            None,                     # None
            123,                      # Not a string
        ]
        for bad_token in wrong_length_tokens:
            result = validate_csrf_token_session_bound(
                csrf_secret=csrf_secret,
                session_id=session_id,
                presented_token=bad_token,
                presented_nonce=nonce,
                session_nonce=nonce,
                route_path=route_path,
                token_issued_at=now,
                now=now,
            )
            assert result.valid is False
            assert "format" in result.error_reason.lower()

        # Right length, not actually hex/HMAC-valid: still fail-closed.
        result = validate_csrf_token_session_bound(
            csrf_secret=csrf_secret,
            session_id=session_id,
            presented_token="x" * 64,
            presented_nonce=nonce,
            session_nonce=nonce,
            route_path=route_path,
            token_issued_at=now,
            now=now,
        )
        assert result.valid is False
        assert result.error_reason is not None

    def test_invalid_nonce_format_rejected(self, csrf_secret, session_id, nonce, route_path, now):
        """Nonce with wrong length is rejected with an explicit format error.

        Same distinction as test_invalid_token_format_rejected: only wrong
        length hits the dedicated format check. A right-length-but-non-hex
        nonce falls through to the nonce-match comparison instead, still
        fail-closed but under 'nonce_mismatch'.
        """
        token = derive_csrf_token_session_bound(
            csrf_secret=csrf_secret,
            session_id=session_id,
            timestamp=now,
            nonce=nonce,
            route_path=route_path,
        )

        wrong_length_nonces = [
            "too_short",         # Too short
            "0" * 31,            # Too short
            "0" * 33,            # Too long
        ]
        for bad_nonce in wrong_length_nonces:
            result = validate_csrf_token_session_bound(
                csrf_secret=csrf_secret,
                session_id=session_id,
                presented_token=token,
                presented_nonce=bad_nonce,
                session_nonce=nonce,
                route_path=route_path,
                token_issued_at=now,
                now=now,
            )
            assert result.valid is False
            assert "format" in result.error_reason.lower()

        # Right length, not actually hex, doesn't match session nonce:
        # still fail-closed, just via the nonce-match check instead.
        result = validate_csrf_token_session_bound(
            csrf_secret=csrf_secret,
            session_id=session_id,
            presented_token=token,
            presented_nonce="x" * 32,
            session_nonce=nonce,
            route_path=route_path,
            token_issued_at=now,
            now=now,
        )
        assert result.valid is False
        assert result.error_reason is not None

    def test_nonce_mismatch_rejected(self, csrf_secret, session_id, nonce, route_path, now):
        """Token from session A used in session B (different nonce) should be rejected."""
        token = derive_csrf_token_session_bound(
            csrf_secret=csrf_secret,
            session_id=session_id,
            timestamp=now,
            nonce=nonce,
            route_path=route_path,
        )

        different_nonce = "fedcba9876543210fedcba9876543210"
        result = validate_csrf_token_session_bound(
            csrf_secret=csrf_secret,
            session_id=session_id,
            presented_token=token,
            presented_nonce=different_nonce,
            session_nonce=nonce,  # Session has original nonce
            route_path=route_path,
            token_issued_at=now,
            now=now,
        )

        assert result.valid is False
        assert "nonce" in result.error_reason.lower()

    def test_token_tampering_detected(self, csrf_secret, session_id, nonce, route_path, now):
        """Token with tampered bits should fail HMAC verification."""
        token = derive_csrf_token_session_bound(
            csrf_secret=csrf_secret,
            session_id=session_id,
            timestamp=now,
            nonce=nonce,
            route_path=route_path,
        )

        # Flip a bit in the token
        token_list = list(token)
        token_list[0] = "f" if token_list[0] != "f" else "0"
        tampered_token = "".join(token_list)

        result = validate_csrf_token_session_bound(
            csrf_secret=csrf_secret,
            session_id=session_id,
            presented_token=tampered_token,
            presented_nonce=nonce,
            session_nonce=nonce,
            route_path=route_path,
            token_issued_at=now,
            now=now,
        )

        assert result.valid is False
        assert "invalid" in result.error_reason.lower()

    def test_timestamp_in_future_rejected(self, csrf_secret, session_id, nonce, route_path, now):
        """Token with future timestamp should be rejected."""
        future_time = now + 3600  # 1 hour in future
        token = derive_csrf_token_session_bound(
            csrf_secret=csrf_secret,
            session_id=session_id,
            timestamp=future_time,
            nonce=nonce,
            route_path=route_path,
        )

        result = validate_csrf_token_session_bound(
            csrf_secret=csrf_secret,
            session_id=session_id,
            presented_token=token,
            presented_nonce=nonce,
            session_nonce=nonce,
            route_path=route_path,
            token_issued_at=future_time,
            now=now,
        )

        assert result.valid is False
        assert "future" in result.error_reason.lower()

    def test_token_expires_after_1_hour(self, csrf_secret, session_id, nonce, route_path, now):
        """Token older than 1 hour should be rejected."""
        old_time = now - 3661  # 1 hour + 1 second
        token = derive_csrf_token_session_bound(
            csrf_secret=csrf_secret,
            session_id=session_id,
            timestamp=old_time,
            nonce=nonce,
            route_path=route_path,
        )

        result = validate_csrf_token_session_bound(
            csrf_secret=csrf_secret,
            session_id=session_id,
            presented_token=token,
            presented_nonce=nonce,
            session_nonce=nonce,
            route_path=route_path,
            token_issued_at=old_time,
            now=now,
        )

        assert result.valid is False
        assert "expired" in result.error_reason.lower()

    def test_token_valid_up_to_1_hour(self, csrf_secret, session_id, nonce, route_path, now):
        """Token exactly at 1 hour should be accepted."""
        old_time = now - 3600  # Exactly 1 hour
        token = derive_csrf_token_session_bound(
            csrf_secret=csrf_secret,
            session_id=session_id,
            timestamp=old_time,
            nonce=nonce,
            route_path=route_path,
        )

        result = validate_csrf_token_session_bound(
            csrf_secret=csrf_secret,
            session_id=session_id,
            presented_token=token,
            presented_nonce=nonce,
            session_nonce=nonce,
            route_path=route_path,
            token_issued_at=old_time,
            now=now,
        )

        assert result.valid is True

    def test_nonce_rotation_on_success(self, csrf_secret, session_id, nonce, route_path, now):
        """On successful validation, new nonce should be different from old."""
        token = derive_csrf_token_session_bound(
            csrf_secret=csrf_secret,
            session_id=session_id,
            timestamp=now,
            nonce=nonce,
            route_path=route_path,
        )

        result = validate_csrf_token_session_bound(
            csrf_secret=csrf_secret,
            session_id=session_id,
            presented_token=token,
            presented_nonce=nonce,
            session_nonce=nonce,
            route_path=route_path,
            token_issued_at=now,
            now=now,
        )

        assert result.new_nonce is not None
        assert result.new_nonce != nonce
        assert len(result.new_nonce) == 32

    def test_token_invalid_in_different_session(self, csrf_secret, nonce, route_path, now):
        """Token from session A should be invalid when used in session B."""
        session_a = "a" * 43
        session_b = "b" * 43

        # Generate token for session A
        token_a = derive_csrf_token_session_bound(
            csrf_secret=csrf_secret,
            session_id=session_a,
            timestamp=now,
            nonce=nonce,
            route_path=route_path,
        )

        # Try to use it in session B (should fail)
        result = validate_csrf_token_session_bound(
            csrf_secret=csrf_secret,
            session_id=session_b,  # Different session
            presented_token=token_a,
            presented_nonce=nonce,
            session_nonce=nonce,
            route_path=route_path,
            token_issued_at=now,
            now=now,
        )

        assert result.valid is False
        assert "invalid" in result.error_reason.lower()

    def test_token_reuse_prevented_by_nonce_rotation(self, csrf_secret, session_id, nonce, route_path, now):
        """After first use, token should be invalid (nonce rotated)."""
        token = derive_csrf_token_session_bound(
            csrf_secret=csrf_secret,
            session_id=session_id,
            timestamp=now,
            nonce=nonce,
            route_path=route_path,
        )

        # First validation (succeeds)
        result1 = validate_csrf_token_session_bound(
            csrf_secret=csrf_secret,
            session_id=session_id,
            presented_token=token,
            presented_nonce=nonce,
            session_nonce=nonce,
            route_path=route_path,
            token_issued_at=now,
            now=now,
        )
        assert result1.valid is True
        new_nonce = result1.new_nonce

        # Second use of same token (fails because nonce rotated)
        result2 = validate_csrf_token_session_bound(
            csrf_secret=csrf_secret,
            session_id=session_id,
            presented_token=token,  # Reusing old token
            presented_nonce=nonce,   # With old nonce
            session_nonce=new_nonce,  # But session has new nonce
            route_path=route_path,
            token_issued_at=now,
            now=now,
        )
        assert result2.valid is False
        assert "nonce" in result2.error_reason.lower()

    def test_route_path_binding(self, csrf_secret, session_id, nonce, now):
        """Token for /approve should be invalid for /defer (route-specific)."""
        approve_route = "/v1/console/autonomous-forge/approve"
        defer_route = "/v1/console/autonomous-forge/defer"

        # Generate token for /approve
        token_approve = derive_csrf_token_session_bound(
            csrf_secret=csrf_secret,
            session_id=session_id,
            timestamp=now,
            nonce=nonce,
            route_path=approve_route,
        )

        # Try to use it for /defer (should fail)
        result = validate_csrf_token_session_bound(
            csrf_secret=csrf_secret,
            session_id=session_id,
            presented_token=token_approve,
            presented_nonce=nonce,
            session_nonce=nonce,
            route_path=defer_route,  # Different route
            token_issued_at=now,
            now=now,
        )

        assert result.valid is False
        assert "invalid" in result.error_reason.lower()


# ─────────────────────────────────────────────────────────────────────────────
# E2E Test: Autonomous Forge Approval with CSRF (1 test)
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.integration
class TestAutonomousForgeCSRFE2E:
    """End-to-end test: autonomous forge approval with CSRF token validation."""

    def test_autonomous_forge_approval_flow(self, csrf_secret, session_id, route_path, now):
        """E2E: GET /status, then POST /approve with valid CSRF token."""
        # Step 1: Initial nonce from session
        session_nonce = generate_csrf_nonce()

        # Step 2: Operator requests status (GET /status)
        #   - Server generates CSRF token bound to session + nonce
        #   - Returns token + nonce to client
        csrf_token = derive_csrf_token_session_bound(
            csrf_secret=csrf_secret,
            session_id=session_id,
            timestamp=now,
            nonce=session_nonce,
            route_path="/v1/console/autonomous-forge/status",
        )
        assert len(csrf_token) == 64

        # Step 3: Operator sends POST /approve with token + nonce
        #   - Server validates token + nonce
        #   - Token binds to session, nonce, and route
        result = validate_csrf_token_session_bound(
            csrf_secret=csrf_secret,
            session_id=session_id,
            presented_token=csrf_token,
            presented_nonce=session_nonce,
            session_nonce=session_nonce,
            route_path="/v1/console/autonomous-forge/approve",
            token_issued_at=now,
            now=now,
        )

        # Validation should fail because token was generated for /status, not /approve
        assert result.valid is False

        # Step 4: Client re-fetches with correct route
        csrf_token_approve = derive_csrf_token_session_bound(
            csrf_secret=csrf_secret,
            session_id=session_id,
            timestamp=now,
            nonce=session_nonce,
            route_path="/v1/console/autonomous-forge/approve",
        )

        result_approve = validate_csrf_token_session_bound(
            csrf_secret=csrf_secret,
            session_id=session_id,
            presented_token=csrf_token_approve,
            presented_nonce=session_nonce,
            session_nonce=session_nonce,
            route_path="/v1/console/autonomous-forge/approve",
            token_issued_at=now,
            now=now,
        )

        # Validation should pass
        assert result_approve.valid is True
        new_nonce = result_approve.new_nonce

        # Step 5: Nonce rotated, old token invalid for next operation
        result_next = validate_csrf_token_session_bound(
            csrf_secret=csrf_secret,
            session_id=session_id,
            presented_token=csrf_token_approve,  # Reusing old token
            presented_nonce=session_nonce,        # With old nonce
            session_nonce=new_nonce,             # But session has new nonce
            route_path="/v1/console/autonomous-forge/defer",
            token_issued_at=now,
            now=now,
        )

        assert result_next.valid is False
        assert "nonce" in result_next.error_reason.lower()
