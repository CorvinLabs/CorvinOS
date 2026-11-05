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


def test_token_authenticated_record_is_flagged_is_internal_tool():
    # ADR-0193 adversarial-review fix: routes/browser.py's navigate route
    # keys the ADR-0187/0189 cross-host confirm off `rec.is_internal_tool` —
    # a regression here (e.g. reverting the `is_internal_tool=True` kwarg)
    # would silently re-open the "MCP tool navigation never confirms
    # cross-host" gap with every other test still green.
    token = internal_auth.mint("acme", "fp123")
    rec = internal_auth.require_session_or_token(
        corvin_console_sid=None, x_corvin_browser_token=token)
    assert rec.is_internal_tool is True


def test_cookie_session_record_defaults_is_internal_tool_false(monkeypatch):
    # A real cookie-authenticated SessionRecord must NOT be flagged as the
    # internal-tool path — that would wrongly force the cross-host confirm
    # onto a human's own manual URL-bar navigation in the SPA.
    from corvin_console import auth as session_auth
    fake_rec = session_auth.SessionRecord(
        sid="s1", sid_fingerprint="fp1", tier="owner", tenant_id="acme",
        token_fingerprint="", csrf_secret="", csrf_nonce="", csrf_nonce_issued_at=0.0,
        created_at=0.0, last_seen_at=0.0,
        expires_at=1e18,
    )
    monkeypatch.setattr(internal_auth._deps, "require_session", lambda **kw: fake_rec)
    rec = internal_auth.require_session_or_token(
        corvin_console_sid="some-cookie", x_corvin_browser_token=None)
    assert rec.is_internal_tool is False


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


def test_minted_token_authenticates_a_real_browser_route_over_http():
    """SRF-21: drive routes/browser.py through FastAPI with a token minted by the
    same function a chat turn calls. The record is built by the real
    ``_bearer_record`` path, so a future required ``SessionRecord`` field that
    internal_auth.py does not pass fails HERE (it shipped broken once: every
    token call answered 500 from 2026-09-20 on)."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from corvin_console.routes import browser as browser_routes

    app = FastAPI()
    app.include_router(browser_routes.router, prefix="/v1/console")
    client = TestClient(app, raise_server_exceptions=True)
    token = internal_auth.mint("_default", "fp-http")
    try:
        r = client.get("/v1/console/browser/attach/consent",
                       headers={"X-Corvin-Browser-Token": token})
        assert r.status_code == 200, r.text
        assert isinstance(r.json(), dict)
        # No token, no cookie -> the cookie path's 401, never a tenant guess.
        assert client.get("/v1/console/browser/attach/consent").status_code == 401
    finally:
        internal_auth.revoke(token)


def test_bearer_record_passes_every_required_session_field():
    """Every SessionRecord field without a default must be set by the token path."""
    import dataclasses

    from corvin_console import auth as session_auth

    required = {f.name for f in dataclasses.fields(session_auth.SessionRecord)
                if f.default is dataclasses.MISSING and f.default_factory is dataclasses.MISSING}
    token = internal_auth.mint("acme", "fp-req")
    try:
        rec = internal_auth._bearer_record(token)
        assert rec is not None
        for name in required:
            assert hasattr(rec, name)
    finally:
        internal_auth.revoke(token)
