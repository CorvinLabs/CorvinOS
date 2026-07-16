"""ADR-0193 — the internal bearer-token bridge for the corvin-browser MCP tool.

Unit-level: mint/verify roundtrip, expiry, and that the new
require_session_or_token/require_csrf_or_token dependencies (a) accept a
valid token without any cookie, (b) fall back to the EXISTING cookie/CSRF
path unchanged when no token is presented, and (c) reject a bad/expired
token exactly like "no session" rather than ever guessing a tenant.
"""
from __future__ import annotations

import time

import pytest
from fastapi import HTTPException

from corvin_console.browser import internal_auth


def test_mint_then_verify_roundtrip():
    token = internal_auth.mint("acme", "fp123")
    assert internal_auth.verify(token) == ("acme", "fp123")


def test_verify_unknown_token_returns_none():
    assert internal_auth.verify("not-a-real-token") is None


def test_verify_empty_token_returns_none():
    assert internal_auth.verify("") is None


def test_verify_expired_token_returns_none(monkeypatch):
    token = internal_auth.mint("acme", "fp123", ttl_s=1.0)
    # internal_auth._purge_expired uses time.monotonic(); jump it forward
    # rather than sleeping in a test.
    real_monotonic = time.monotonic()
    monkeypatch.setattr(time, "monotonic", lambda: real_monotonic + 10.0)
    assert internal_auth.verify(token) is None


def test_revoke_invalidates_immediately():
    token = internal_auth.mint("acme", "fp123")
    internal_auth.revoke(token)
    assert internal_auth.verify(token) is None


def test_require_session_or_token_accepts_valid_bearer_with_no_cookie():
    token = internal_auth.mint("acme", "fp123")
    rec = internal_auth.require_session_or_token(
        corvin_console_sid=None, x_corvin_browser_token=token)
    assert rec.tenant_id == "acme"
    assert rec.sid_fingerprint == "fp123"
    assert rec.tier == "owner"


def test_require_csrf_or_token_accepts_valid_bearer_without_csrf_header():
    token = internal_auth.mint("acme", "fp123")
    rec = internal_auth.require_csrf_or_token(
        corvin_console_sid=None, x_csrf_token=None, x_corvin_browser_token=token)
    assert rec.tenant_id == "acme"
    assert rec.sid_fingerprint == "fp123"


def test_require_session_or_token_falls_back_to_401_with_no_cookie_and_no_token():
    with pytest.raises(HTTPException) as exc_info:
        internal_auth.require_session_or_token(corvin_console_sid=None, x_corvin_browser_token=None)
    assert exc_info.value.status_code == 401


def test_require_session_or_token_rejects_bad_token_falls_back_to_cookie_path():
    # A malformed/unknown token must not be treated as authenticated — it
    # falls through to the ordinary cookie check, which then 401s exactly
    # like "no session" (never a distinguishable error for a wrong token).
    with pytest.raises(HTTPException) as exc_info:
        internal_auth.require_session_or_token(
            corvin_console_sid=None, x_corvin_browser_token="garbage-token")
    assert exc_info.value.status_code == 401


def test_require_csrf_or_token_falls_back_to_401_with_no_cookie_and_no_token():
    # No token, no real cookie session -> the ordinary require_csrf path runs
    # unchanged (require_session fails first: "no session"/"session expired").
    with pytest.raises(HTTPException) as exc_info:
        internal_auth.require_csrf_or_token(
            corvin_console_sid=None, x_csrf_token=None, x_corvin_browser_token=None)
    assert exc_info.value.status_code == 401
