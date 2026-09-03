"""FastAPI dependencies for the console UI.

Phase A — ``require_session`` validates cookie + loads session.
Phase E — adds ``require_csrf`` (every mutation).

``verify_reauth`` is a possession-proof gate on sensitive mutating routes.
The SPA may present the ``sid_fingerprint`` as an optional extra factor.
When no token is presented the CSRF check (already enforced upstream on every
mutation) is treated as sufficient possession proof.  When a token IS
presented it must match the fingerprint via constant-time comparison;
wrong tokens are always rejected.
"""
from __future__ import annotations

import hmac
from typing import Annotated

from fastapi import Cookie, Header, HTTPException, status

from . import auth as session_auth


def require_session(
    corvin_console_sid: Annotated[str | None, Cookie()] = None,
) -> session_auth.SessionRecord:
    """Return the live session record or raise 401."""
    if not corvin_console_sid:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="no session",
        )
    rec = session_auth.load_session(corvin_console_sid)
    if rec is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="session expired",
        )
    return rec


def require_csrf(
    corvin_console_sid: Annotated[str | None, Cookie()] = None,
    x_csrf_token: Annotated[str | None, Header(alias="x-csrf-token")] = None,
) -> session_auth.SessionRecord:
    """Validate session AND CSRF token. For every mutation."""
    rec = require_session(corvin_console_sid=corvin_console_sid)
    if not x_csrf_token:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="missing CSRF token",
        )
    if not session_auth.verify_csrf_token(rec.csrf_secret, rec.sid, x_csrf_token):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="invalid CSRF token",
        )
    return rec


def verify_reauth(rec: session_auth.SessionRecord, presented_token: str | None) -> bool:
    """Possession-proof re-authentication check.

    When ``presented_token`` is absent or empty the check passes: CSRF
    (already enforced by ``require_csrf`` on every mutation endpoint)
    provides sufficient possession proof that the caller holds the active
    session cookie.

    When a token IS presented it must equal ``rec.sid_fingerprint`` (the
    12-character hex SHA-256 prefix of the session id) via a constant-time
    comparison — wrong tokens are always rejected.

    When OIDC re-auth is introduced this function will be replaced with a
    proper token-verification call; the call-sites stay unchanged.
    """
    if not presented_token:
        # No token presented — CSRF upstream is the possession gate.
        return True
    return hmac.compare_digest(presented_token, rec.sid_fingerprint)


# ── Public-route allowlist (single source of truth) ─────────────────────────
#
# Every mounted console route MUST carry ``require_session`` / ``require_csrf``
# (or a token-based ``*_or_token`` equivalent) in its dependency tree — EXCEPT
# the exact paths listed here, each with the reason it is public by design.
# Two consumers read this list so they can never drift apart:
#
#   * ``core/console/tests/test_route_auth_guard.py`` walks the live route table
#     of ``standalone.create_app()`` and fails on any unauthenticated route
#     that is not in this allowlist (adversarial review E-01/E-02/E-03).
#   * ``standalone.create_app()`` derives the dual-gate middleware's
#     ``skip_paths`` from ``PUBLIC_PATH_PREFIXES`` (ADR-0300/0301, E-05) so an
#     anonymous caller can reach exactly these paths and nothing else.
#
# Adding an entry is a security decision: name the reason, and keep the
# route itself loopback-gated or secret-bearing where the reason says so.
PUBLIC_ROUTES: dict[str, str] = {
    "/": "302 redirect into local-login; no data",
    "/local-stats": "bare HTML page; its data endpoint below is loopback-gated",
    "/v1/console/healthz": "liveness probe; constant body",
    "/v1/console/version": "package version only (no tenant data)",
    "/v1/console/auth/local-login": "the credential-less local-owner login itself (loopback-gated)",
    "/v1/console/local-stats": "PENTEST-10: loopback-gated in the handler (403 otherwise)",
    "/v1/console/instance-stats": "loopback-gated in the handler (403 otherwise); anonymous aggregate",
    "/v1/console/landing/personas": "pre-login landing card; publishable persona fields only",
    "/v1/console/grants/templates": "static grant template catalogue; no tenant data",
    "/v1/console/stats/features": "ADR-0212 local feature-adoption percentages; anonymous aggregate",
    "/v1/console/setup/onboarding/detect": "ADR-0120 M1: loopback-only until onboarding completes, session afterwards",
    "/v1/console/remote-trigger/pair/accept": "ADR-0048 A2A pairing: authenticated by the pairing token in the body",
    "/v1/console/webhook/{tenant_id}/{channel_id}": "inbound bridge webhook; authenticated by the per-channel shared secret",
    "/v1/a2a/receive": "ADR-0048/0199 A2A inbound envelope; instance-attested + friendship token",
    "/v1/a2a/ping": "ADR-0048/0199 A2A liveness; friendship token",
    "/v1/a2a/friendship-ack": "ADR-0048/0199 A2A pairing acknowledgement; friendship token",
}

# Path prefixes the dual-gate middleware passes through WITHOUT a session
# (everything under them is either in PUBLIC_ROUTES or a static/SPA mount).
PUBLIC_PATH_PREFIXES: tuple[str, ...] = (
    "/healthz",
    "/v1/console/healthz",
    "/v1/console/version",
    "/v1/console/auth/local-login",
    "/v1/console/local-stats",
    "/v1/console/instance-stats",
    "/v1/console/landing/",
    "/v1/console/grants/templates",
    "/v1/console/stats/features",
    "/v1/console/setup/onboarding/detect",
    "/v1/console/remote-trigger/pair/accept",
    "/v1/console/webhook/",
    "/v1/a2a/",
    "/local-stats",
    "/console",      # SPA shell + hashed assets
    "/static/",
    "/ws-live/",
    "/.well-known/",
)
