"""Login / logout / whoami for the console UI.

Wire format
-----------

``GET /v1/console/auth/local-login`` — localhost auto-login.
  Only works when the HTTP client is 127.0.0.1 / ::1.
  Creates a session directly and redirects to /console/.
  Disable with CORVIN_LOCAL_AUTOLOGIN=0.

``POST /v1/console/auth/logout`` — clears cookie + deletes session.

``GET /v1/console/auth/whoami`` — returns current session details.

Static atlr_* token auth has been removed. For local deployments the
loopback binding is the security boundary. OIDC/Google OAuth will be
wired in the cloud deployment phase (POST /auth/login will return then).

Rate limit
----------

local-login is localhost-only + credential-less → NO rate limit (a cap
    only locked the owner out and caused a redirect loop; removed in 0.9.6).
"""
from __future__ import annotations

import os
from typing import Annotated

from fastapi import APIRouter, Cookie, Depends, Header, HTTPException, Request, Response, status
from fastapi.responses import RedirectResponse
from pydantic import BaseModel

from .. import auth as session_auth
from .. import audit as console_audit
from ..deps import require_csrf

router = APIRouter()


# NOTE: the local-login rate limiter was REMOVED (0.9.6). local-login is
# localhost-only + credential-less, so a cap only locked the owner out ("too many
# login attempts") and drove a redirect loop. See local_login() for the rationale.


# ── Response shapes ───────────────────────────────────────────────────


class WhoamiResponse(BaseModel):
    tier: str
    tenant_id: str
    fingerprint: str
    csrf_token: str
    expires_at: float


# ── Routes ────────────────────────────────────────────────────────────


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(
    request: Request,
    response: Response,
    rec: Annotated[session_auth.SessionRecord, Depends(require_csrf)],
    corvin_console_sid: Annotated[str | None, Cookie()] = None,
) -> Response:
    if corvin_console_sid:
        session_auth.end_session(corvin_console_sid)
        console_audit.session_ended(
            tenant_id=rec.tenant_id,
            sid_fingerprint=rec.sid_fingerprint,
            reason="logout",
        )
    is_https = request.url.scheme == "https"
    response.delete_cookie(
        key=session_auth.COOKIE_NAME,
        path="/",
        httponly=True,
        secure=is_https,
        samesite="strict",
    )
    response.status_code = status.HTTP_204_NO_CONTENT
    return response


@router.get("/whoami", response_model=WhoamiResponse)
def whoami(
    corvin_console_sid: Annotated[str | None, Cookie()] = None,
    user_agent: Annotated[str | None, Header(alias="user-agent")] = None,
) -> WhoamiResponse:
    if not corvin_console_sid:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="no session",
        )
    rec = session_auth.load_session(corvin_console_sid)
    if rec is None:
        console_audit.session_denied(
            reason="session-expired",
            user_agent=user_agent,
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="session expired",
        )
    return WhoamiResponse(
        tier=rec.tier,
        tenant_id=rec.tenant_id,
        fingerprint=rec.sid_fingerprint,
        csrf_token=session_auth.derive_csrf_token(rec.csrf_secret, rec.sid),
        expires_at=rec.expires_at,
    )


# ── Localhost auto-login ──────────────────────────────────────────────


def _is_localhost(request: Request) -> bool:
    """True only when the TCP peer is 127.0.0.1 or ::1.

    Intentionally ignores X-Forwarded-For — proxies must not be able to
    forge a localhost identity.
    """
    client = request.client
    if client is None:
        return False
    return client.host in ("127.0.0.1", "::1", "localhost")


@router.get("/local-login", include_in_schema=False)
def local_login(
    request: Request,
    response: Response,
    user_agent: Annotated[str | None, Header(alias="user-agent")] = None,
) -> RedirectResponse:
    """Auto-login for localhost operators — no token needed.

    Creates a console session directly for the local tenant and
    redirects to /console/. Only available from 127.0.0.1 or ::1.

    Disable by setting CORVIN_LOCAL_AUTOLOGIN=0.
    """
    if not _is_localhost(request):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="local-login is only available from localhost",
        )

    if os.environ.get("CORVIN_LOCAL_AUTOLOGIN", "1") == "0":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="local-login is disabled (set CORVIN_LOCAL_AUTOLOGIN=1 to enable)",
        )

    # NO rate limit on local-login. It is localhost-ONLY (gated above) and
    # credential-less — every caller is the legitimate local owner, so there is
    # nothing to brute-force. A cap only ever locked the owner out ("too many
    # login attempts") AND created a redirect loop: the SPA's LoginPage navigates
    # here, a 429 leaves it with no session, it lands back on /console/login and
    # retries — rapidly exhausting any finite cap. Removing the limit makes the
    # local auto-login always succeed → no login friction, no loop. (A local
    # process could call session_auth.create_session directly anyway, so the cap
    # added no real protection.) See the cap-raise history in 0.9.4.

    # Local login always uses _default tenant (never env var)
    tenant_id = "_default"
    rec = session_auth.create_session(
        tenant_id=tenant_id,
        token_fingerprint="",
        persistent=False,
    )
    console_audit.session_started(
        tenant_id=tenant_id,
        token_fingerprint="",
        sid_fingerprint=rec.sid_fingerprint,
        user_agent=user_agent,
    )

    is_https = request.url.scheme == "https"
    # In headless mode (ADR-0241/0243) there is no /console/ to land on, so this
    # 302 would hand a freshly-authenticated caller a 404. The session is still
    # created and the cookie still set — only the destination changes, because
    # an API-only deployment logging in is a legitimate thing to do.
    _target = "/console/"
    try:
        from ..app import headless_enabled

        if headless_enabled(tenant_id):
            _target = "/"
    except Exception:  # noqa: BLE001 — unreadable flag means "console as usual"
        pass
    redirect = RedirectResponse(_target, status_code=302)
    redirect.set_cookie(
        key=session_auth.COOKIE_NAME,
        value=rec.sid,
        httponly=True,
        secure=is_https,
        samesite="strict",
        max_age=session_auth.ABSOLUTE_TIMEOUT_S,
        path="/",
    )
    return redirect
