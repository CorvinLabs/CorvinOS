"""ADR-0193 — internal bearer-token bridge for the ``corvin-browser`` MCP tool.

The browser action surface (``routes/browser.py``) is normally reached only by
the SPA over an authenticated cookie session (``require_session``/
``require_csrf``, see ``..deps``) — appropriate for a human's browser tab, but
unusable for the new native MCP tool, which runs as a *separate OS subprocess*
spawned alongside the ``claude`` CLI for one chat turn (see
``chat_runtime.py``'s ``stream_turn``/``_persona_mcp_config``) and has no
cookie jar.

``BrowserSessionManager`` itself is strictly in-process (its own docstring:
"a console restart drops live sessions") — the live-view page polls frames/
actions from the ONE manager singleton this console process owns. A separate
MCP subprocess importing the manager directly would spawn a second, isolated
set of browser sessions invisible to that live view. The only way for the MCP
tool to drive the SAME sessions the live view watches is to call back into
this ALREADY-RUNNING console process's own ``/v1/console/browser/*`` REST API
over loopback HTTP — which is exactly what ``corvin-browser``'s ``main.py``
does. This module is the credential that lets it authenticate that call
without a cookie.

Design (additive, not a replacement):
  * ``mint()`` is called once per chat-turn spawn (``chat_runtime.stream_turn``),
    binding a random token to the SAME ``(tenant_id, sid_fingerprint)`` pair
    the calling browser tab's login session already carries — so a browser
    session the MCP tool creates is owned by that same login (via
    ``owner_fingerprint=sid_fingerprint``, ``routes/browser.py``'s existing
    ownership check), and the user's OWN live-view link (opened with their own
    cookie) can watch/steer it exactly as if they'd started it from
    ``/browser`` chat command.
  * ``verify()`` is called by this module's own ``require_session_or_token``/
    ``require_csrf_or_token`` (drop-in replacements for ``..deps``'
    ``require_session``/``require_csrf``, wired into ``routes/browser.py``)
    — an ADDITIVE auth path alongside the existing cookie+CSRF one, never a
    replacement (the live-view SPA keeps using cookies unchanged). The token
    travels as ``X-Corvin-Browser-Token``, not ``Authorization: Bearer`` —
    see ``_bearer_record``'s docstring for why.
  * Tokens are short-lived (bounded by ``_TTL_S``, matched to the longest
    plausible single agentic turn), single-tenant-scoped, held only in
    process memory (never persisted to disk — a console restart invalidates
    every outstanding token, which is fine: the ``claude`` subprocess that
    held it is gone too), and best-effort-purged of expired entries on every
    ``verify()`` call so a long-running console doesn't accumulate garbage.
"""
from __future__ import annotations

import secrets
import time
from typing import Annotated

from fastapi import Cookie, Header, HTTPException, status

from .. import auth as session_auth
from .. import deps as _deps

# 30 minutes — generous enough for a long agentic browsing turn (many
# sequential tool calls), short enough that a leaked token (e.g. via a
# subprocess-environment-dumping bug elsewhere) is worthless soon after the
# turn that minted it ends.
_TTL_S = 1800.0

# token -> (tenant_id, sid_fingerprint, expires_at_monotonic)
_TOKENS: dict[str, tuple[str, str, float]] = {}


def _purge_expired(*, now: float) -> None:
    expired = [t for t, (_, _, exp) in _TOKENS.items() if exp <= now]
    for t in expired:
        _TOKENS.pop(t, None)


def mint(tenant_id: str, sid_fingerprint: str, *, ttl_s: float = _TTL_S) -> str:
    """Mint a fresh bearer token scoped to ``(tenant_id, sid_fingerprint)``.

    Called once per chat-turn spawn — cheap even when the turn never touches
    the browser tool, since no browser session is created until the MCP tool
    actually makes its first HTTP call.
    """
    now = time.monotonic()
    _purge_expired(now=now)
    token = secrets.token_urlsafe(32)
    _TOKENS[token] = (tenant_id, sid_fingerprint, now + ttl_s)
    return token


def verify(token: str) -> tuple[str, str] | None:
    """Return ``(tenant_id, sid_fingerprint)`` for a live token, else ``None``.

    Fail-closed: an empty/unknown/expired token is indistinguishable from
    "no token presented" to the caller — never raises, never guesses a
    tenant.
    """
    if not token:
        return None
    now = time.monotonic()
    _purge_expired(now=now)
    entry = _TOKENS.get(token)
    if entry is None:
        return None
    tenant_id, sid_fingerprint, _expires_at = entry
    return tenant_id, sid_fingerprint


def revoke(token: str) -> None:
    """Best-effort early revocation (not currently called anywhere — the TTL
    is the primary defense — but kept available for a future explicit
    'end this turn' hook without needing a second mechanism)."""
    _TOKENS.pop(token, None)


def _bearer_record(token: str | None) -> session_auth.SessionRecord | None:
    """The ``X-Corvin-Browser-Token`` header -> a synthetic SessionRecord, or
    ``None`` if the header is absent/invalid — callers fall back to the
    existing cookie path in that case, never treat it as an error.

    Deliberately NOT ``Authorization: Bearer`` (found the hard way, live
    end-to-end test): ``corvin_gateway.app._jwt_guard`` is a GLOBAL app-wide
    dependency that rejects any ``Authorization: Bearer`` value that isn't
    JWT-shaped with 401 ``non-jwt-bearer-rejected`` — a deliberate anti
    token-downgrade-attack gate for the cloud/OIDC path, and this token is a
    different, internal, loopback-only credential, not a JWT/OIDC token. A
    distinct header name is honest about that distinction and never risks
    touching (or needing to weaken) that unrelated gate."""
    if not token:
        return None
    resolved = verify(token.strip())
    if resolved is None:
        return None
    tenant_id, sid_fingerprint = resolved
    now = time.time()
    return session_auth.SessionRecord(
        sid=f"mcp:{secrets.token_hex(6)}",  # sentinel — no real login session
        sid_fingerprint=sid_fingerprint,
        tier="owner",
        tenant_id=tenant_id,
        token_fingerprint="",
        csrf_secret="",
        created_at=now,
        last_seen_at=now,
        expires_at=now + _TTL_S,
        persistent=False,
        lic_proof="",
    )


def require_session_or_token(
    corvin_console_sid: Annotated[str | None, Cookie()] = None,
    x_corvin_browser_token: Annotated[str | None, Header(alias="x-corvin-browser-token")] = None,
) -> session_auth.SessionRecord:
    """Drop-in replacement for ``deps.require_session`` on routes the
    ``corvin-browser`` MCP tool also calls: a valid ``X-Corvin-Browser-Token``
    (minted per-chat-turn, see module docstring) authenticates the same way a
    cookie session does. The cookie path (the live-view SPA) is completely
    unchanged when no token is presented."""
    rec = _bearer_record(x_corvin_browser_token)
    if rec is not None:
        return rec
    return _deps.require_session(corvin_console_sid=corvin_console_sid)


def require_csrf_or_token(
    corvin_console_sid: Annotated[str | None, Cookie()] = None,
    x_csrf_token: Annotated[str | None, Header(alias="x-csrf-token")] = None,
    x_corvin_browser_token: Annotated[str | None, Header(alias="x-corvin-browser-token")] = None,
) -> session_auth.SessionRecord:
    """Drop-in replacement for ``deps.require_csrf``. CSRF exists to defend
    ambient COOKIE auth against a malicious page riding the browser's own
    session — it has no meaning for an explicit, non-cookie token the caller
    had to read out of its own process environment, so a valid token skips
    the CSRF check entirely (mirrors ``require_session_or_token`` above); the
    cookie+CSRF path for the SPA is unchanged."""
    rec = _bearer_record(x_corvin_browser_token)
    if rec is not None:
        return rec
    return _deps.require_csrf(corvin_console_sid=corvin_console_sid, x_csrf_token=x_csrf_token)
