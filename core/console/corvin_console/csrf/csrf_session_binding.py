"""CSRF Token Validation with Session Binding, Nonce Rotation, and Timestamp.

CRITICAL SECURITY FIX: Session-bound CSRF tokens prevent cross-session token reuse.

Architecture
============

Token Structure (HMAC-SHA256):
  token = HMAC-SHA256(
    key   = server_secret + session_id,
    msg   = timestamp + nonce + route_path
  )

Validation Flow:
  1. Extract token, nonce, timestamp from request
  2. Verify: token = HMAC-SHA256(key, msg)
  3. Verify: timestamp is within 1 hour (time-bound)
  4. Verify: nonce matches session.csrf_nonce (one-time use, not reusable)
  5. On success: Generate new nonce for next request (rotation)

Threat Model (Fixed):
  ❌ OLD: Token stolen from session A, used in session B → ATTACK SUCCEEDS
          (same token valid for both sessions)
  ✅ NEW: Token stolen from session A, used in session B → ATTACK FAILS
          (token includes session_id in HMAC key, so it's invalid for session B)
  ✅ NEW: Token reused in same session → ATTACK FAILS
          (nonce is one-time use; after validation, it's rotated and old token rejected)
  ✅ NEW: Token from 2 hours ago → ATTACK FAILS
          (timestamp is >1 hour, rejected by time check)

Session Record Extension
========================

SessionRecord now carries:
  - csrf_nonce: str (32-char hex, rotated after each validation)
  - csrf_nonce_issued_at: float (timestamp when nonce was issued)

Nonce Lifecycle:
  1. Session created → csrf_nonce = random 32-char hex
  2. GET /status → return { csrf_token, csrf_nonce }
     (token bound to current nonce)
  3. POST /approve → validate csrf_token + nonce
     - token = HMAC-SHA256(key, timestamp + nonce + route_path)
     - reject if nonce != session.csrf_nonce (stale/reused)
     - reject if timestamp > now (future-dated)
     - reject if timestamp + 3600s < now (expired)
  4. On success → rotate nonce (session.csrf_nonce = new random)
  5. Next POST → uses new nonce (old token invalid)

Compliance
==========

OWASP A05:2021 – Broken Access Control
  - Token bound to session (cannot steal and reuse cross-session)
  - Nonce prevents replay (one-time use per operation)
  - Timestamp prevents old-token attacks

Load-Bearing Rules
==================

1. HMAC key MUST include session_id (not just csrf_secret)
   - Ensures token is invalid in a different session
2. Nonce MUST be validated before operation (fail-closed)
   - Invalid nonce → 403 Forbidden (not 400 Bad Request)
3. Nonce MUST be rotated AFTER successful validation
   - Old token becomes invalid for next request
4. Timestamp MUST be checked (within 1 hour)
   - Rejects old tokens from hours/days ago
5. On validation failure → emit audit event (CSRF_VALIDATION_FAILED)
   - Never silent rejection; audit trail captures all attempts
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import secrets
import time
from datetime import datetime, timedelta
from typing import NamedTuple

log = logging.getLogger(__name__)

# CSRF validation constants
_CSRF_NONCE_BYTES = 16  # → 32-char hex
_CSRF_TOKEN_TTL_S = 3600  # 1 hour
_CSRF_HMAC_ALGO = hashlib.sha256


class CSRFValidationResult(NamedTuple):
    """Result of CSRF token validation."""
    valid: bool
    error_reason: str | None = None  # None if valid, reason string if invalid
    new_nonce: str | None = None  # New nonce for next request (only on success)


def generate_csrf_nonce() -> str:
    """Generate a new CSRF nonce (32-char hex, one-time use per request)."""
    return secrets.token_hex(_CSRF_NONCE_BYTES)


def derive_csrf_token_session_bound(
    csrf_secret: str,
    session_id: str,
    timestamp: float,
    nonce: str,
    route_path: str,
) -> str:
    """Derive a CSRF token bound to session + timestamp + nonce + route.

    Token is HMAC-SHA256:
      key = HMAC(csrf_secret, session_id)  [keyed with session_id]
      msg = "{timestamp}|{nonce}|{route_path}"

    The key includes session_id, so a token stolen from session A is
    invalid in session B (different key).

    Args:
      csrf_secret: Server-provided secret (from SessionRecord.csrf_secret)
      session_id: Session ID (from SessionRecord.sid)
      timestamp: Unix timestamp when token was generated (float)
      nonce: One-time nonce (from SessionRecord.csrf_nonce)
      route_path: Route being protected (e.g., "/v1/console/autonomous-forge/approve")

    Returns:
      64-char hex HMAC-SHA256 token
    """
    # Key is HMAC(csrf_secret, session_id) to bind token to this session
    key = hmac.new(
        csrf_secret.encode("utf-8"),
        session_id.encode("utf-8"),
        _CSRF_HMAC_ALGO,
    ).digest()

    # Message includes timestamp + nonce + route to prevent token reuse
    msg = f"{timestamp}|{nonce}|{route_path}".encode("utf-8")

    token = hmac.new(key, msg, _CSRF_HMAC_ALGO).hexdigest()
    return token


def validate_csrf_token_session_bound(
    csrf_secret: str,
    session_id: str,
    presented_token: str,
    presented_nonce: str,
    session_nonce: str,
    route_path: str,
    token_issued_at: float | None = None,
    now: float | None = None,
) -> CSRFValidationResult:
    """Validate a CSRF token with session binding, nonce, and timestamp.

    Validation steps:
      1. Check token format (64-char hex)
      2. Check nonce format (32-char hex)
      3. Check timestamp is not in the future
      4. Check timestamp is not older than 1 hour
      5. Check nonce matches session.csrf_nonce (one-time use)
      6. Check HMAC signature

    Args:
      csrf_secret: Server secret (from SessionRecord.csrf_secret)
      session_id: Session ID (from SessionRecord.sid)
      presented_token: Token from request header (64-char hex)
      presented_nonce: Nonce from request body (32-char hex)
      session_nonce: Current nonce in session (from SessionRecord.csrf_nonce)
      route_path: Route being protected (must match token generation)
      token_issued_at: Timestamp when token was issued (float)
        If None, use current time (assumes immediate use)
      now: Current time for validation (float, default: time.time())

    Returns:
      CSRFValidationResult:
        - valid=True if all checks pass (includes new_nonce)
        - valid=False with error_reason if any check fails
    """
    now = now if now is not None else time.time()
    token_issued_at = token_issued_at if token_issued_at is not None else now

    # 1. Check token format
    if not isinstance(presented_token, str) or len(presented_token) != 64:
        return CSRFValidationResult(
            valid=False,
            error_reason="invalid_token_format (must be 64-char hex)",
        )

    # 2. Check nonce format
    if not isinstance(presented_nonce, str) or len(presented_nonce) != 32:
        return CSRFValidationResult(
            valid=False,
            error_reason="invalid_nonce_format (must be 32-char hex)",
        )

    # 3. Check timestamp is not in the future (max 5s skew tolerance)
    if token_issued_at > now + 5:
        return CSRFValidationResult(
            valid=False,
            error_reason="token_future_dated (issued in the future)",
        )

    # 4. Check timestamp is not older than 1 hour
    if now - token_issued_at > _CSRF_TOKEN_TTL_S:
        return CSRFValidationResult(
            valid=False,
            error_reason="token_expired (>1 hour old)",
        )

    # 5. Check nonce matches session nonce (one-time use, not reusable)
    if not hmac.compare_digest(presented_nonce, session_nonce):
        return CSRFValidationResult(
            valid=False,
            error_reason="nonce_mismatch (stale or reused token)",
        )

    # 6. Check HMAC signature
    expected_token = derive_csrf_token_session_bound(
        csrf_secret=csrf_secret,
        session_id=session_id,
        timestamp=token_issued_at,
        nonce=presented_nonce,
        route_path=route_path,
    )

    if not hmac.compare_digest(presented_token, expected_token):
        return CSRFValidationResult(
            valid=False,
            error_reason="token_invalid (HMAC mismatch, possible tampering)",
        )

    # All checks passed — generate new nonce for next request
    new_nonce = generate_csrf_nonce()
    return CSRFValidationResult(
        valid=True,
        error_reason=None,
        new_nonce=new_nonce,
    )


def format_csrf_error_for_log(error_reason: str | None) -> str:
    """Format CSRF error reason for audit logging."""
    if error_reason is None:
        return "csrf_validation_success"
    return f"csrf_validation_failed: {error_reason}"
