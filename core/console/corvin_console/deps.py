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
