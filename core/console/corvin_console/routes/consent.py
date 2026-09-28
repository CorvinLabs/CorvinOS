"""Console consent self-service — grant / revoke / list the CALLING operator's
GDPR Art. 6/7 consent scopes (``core.compliance.consent_store``).

Why this exists: ``core.compliance.consent.consent_required(scope)`` gates
the learning feedback route (``learning_feedback``) and every control-plane
override route (``control_plane_override_operations``) deny-by-default — and
until 2026-09-27 nothing on the live service could GRANT a scope, so those
routes answered 403 for everyone (the gate's hint pointed at a
``/consent-manager`` that did not exist).

Invariants (load-bearing):

* Deny-by-default is untouched: a scope is active only after an explicit,
  human, CSRF-protected request by the same session. There is no auto-admit,
  no "grant all", no scope outside ``GRANTABLE_CONSENT_SCOPES``.
* A caller grants for ITSELF only — the subject is always derived from the
  caller's own session record (``consent_subject``); no route parameter
  names a subject. For a local-login session that is the install's one
  operator (``local-operator:<tenant>``), so a consent given in one login is
  visible in, and WITHDRAWABLE from, every other login of that operator
  (GDPR Art. 7(3)); a logout does not end it — the TTL cap or a withdrawal
  does. A session with a credential identity stays per-session. The raw
  session id is never stored, returned or audited; the audit records carry
  the subject AND the acting session's ``sid_fingerprint``.
* Human-only: the internal corvin-browser tool record is refused, so the
  model cannot grant itself consent.
* TTL-capped: ``ttl_days`` is clamped by the store to ``MAX_TTL_DAYS``.
* Audit-FIRST for grants: ``console.consent_granted`` is written to the
  tenant's one chain (``tenant_audit_chain``) BEFORE the store changes; if
  the record does not commit, nothing is granted (503).
* Revocation is never blocked by the audit writer (withdrawal must always
  work — GDPR Art. 7(3)); ``console.consent_revoked`` is written after the
  store change and a failed write is reported as ``audited: false``. A
  local operator's withdrawal also ends any row left under a legacy
  per-session subject (grants made before 2026-09-28), so no consent row
  survives that its owner can no longer reach.

Known limitation — ONE tenant per process: both audit records are tagged
with the session's ``tenant_id`` and ``forge.security_events.write_event``
refuses a record whose ``tenant_id`` is not the PROCESS tenant
(``CORVIN_TENANT_ID`` → ``_default``; ``AuditTenantMismatch``). A grant for
any other tenant therefore answers 503 and grants nothing (fail-closed), and
a withdrawal takes effect but reports ``audited: false``. Local-login always
mints ``_default`` sessions, so a console process started with a different
``CORVIN_TENANT_ID`` cannot grant consent at all. This surface is NOT
multi-tenant.
"""
from __future__ import annotations

import logging
import math
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, status as http_status
from pydantic import BaseModel

from .. import auth as session_auth
from ..audit import _forge_paths, _security_events
from ..deps import require_csrf, require_session
from core.compliance.consent import (
    CONSENT_SCOPES,
    GRANTABLE_CONSENT_SCOPES,
    ConsentError,
    consent_subject,
    is_local_operator_subject,
)

_log = logging.getLogger(__name__)

router = APIRouter()

EVENT_GRANTED = "console.consent_granted"
EVENT_REVOKED = "console.consent_revoked"

#: Content-free field set for both events: ids, a scope code, a window.
#: EVENT_SEVERITY entries for ``security_events.py`` (owner please add):
#:   "console.consent_granted": "INFO", "console.consent_revoked": "INFO"
CONSENT_EVENT_ALLOWLIST = frozenset({
    "tenant_id", "sid_fingerprint", "consent_subject", "consent_scope", "ttl_s",
    "expires_at", "surface", "legacy_subjects_revoked",
})


def _register_allowlists() -> None:
    for event_type in (EVENT_GRANTED, EVENT_REVOKED):
        _security_events.register_event_allowlist(event_type, CONSENT_EVENT_ALLOWLIST)


_register_allowlists()


class GrantConsentReq(BaseModel):
    ttl_days: float | None = None   # None → store default; clamped to MAX_TTL_DAYS
    model_config = {"extra": "forbid"}


def _human_only(rec: session_auth.SessionRecord) -> None:
    if getattr(rec, "is_internal_tool", False):
        raise HTTPException(
            status_code=http_status.HTTP_403_FORBIDDEN,
            detail="consent must be granted by the human operator in the console, "
                   "not by an automated tool",
        )


def _check_scope(scope: str) -> None:
    if scope not in GRANTABLE_CONSENT_SCOPES:
        raise HTTPException(status_code=http_status.HTTP_404_NOT_FOUND,
                            detail="unknown consent scope")


def _subject(rec: session_auth.SessionRecord) -> str:
    try:
        return consent_subject(rec)
    except ConsentError:
        raise HTTPException(status_code=http_status.HTTP_403_FORBIDDEN,
                            detail="session has no identity for consent") from None


def _store(tenant_id: str):
    from core.compliance.consent_store import get_consent_store
    return get_consent_store(tenant_id=tenant_id)


def _audit(event_type: str, tenant_id: str, details: dict[str, Any]) -> str | None:
    """Write one record to the tenant's chain; return its hash or None."""
    body = {k: v for k, v in details.items() if v is not None}
    body["tenant_id"] = tenant_id
    try:
        chain = _forge_paths.tenant_audit_chain(tenant_id)
        chain.parent.mkdir(parents=True, exist_ok=True)
        record = _security_events.write_event(chain, event_type, details=body, severity="INFO")
    except Exception as exc:  # noqa: BLE001 — caller decides fail direction
        _log.error("consent audit write failed for %s: %s", event_type, type(exc).__name__)
        return None
    return (record or {}).get("hash") if isinstance(record, dict) else None


@router.get("/consent")
async def list_consent(
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
) -> dict[str, Any]:
    """The calling session's state for every grantable scope."""
    subject = _subject(rec)
    store = _store(rec.tenant_id)
    active = {r.scope: r for r in store.list_active_consents(subject)}
    scopes = []
    for scope in sorted(GRANTABLE_CONSENT_SCOPES):
        r = active.get(scope)
        scopes.append({
            "scope": scope,
            "description": CONSENT_SCOPES[scope],
            "active": bool(r and r.is_active()),
            "granted_at": r.granted_at if r else None,
            "expires_at": r.expires_at if r else None,
        })
    return {"subject": subject, "scopes": scopes}


def _sweep_legacy_subjects(store, subject: str, scope: str) -> int:
    """Revoke rows of ``scope`` held under a legacy per-session subject.

    Only for the local operator: on a local-login install every session is
    that one operator's, and before 2026-09-28 each grant was keyed on the
    granting session's fingerprint — rows nothing consults any more but that
    would otherwise stay un-withdrawn until their TTL ran out. Revoking is
    the safe direction; nothing here can grant.
    """
    if not is_local_operator_subject(subject):
        return 0
    n = 0
    for other in store.unrevoked_subjects(scope):
        if other != subject and not is_local_operator_subject(other):
            if store.revoke_consent(user_id=other, scope=scope) is not None:
                n += 1
    return n


@router.post("/consent/{scope}")
async def grant_consent(
    scope: str,
    rec: Annotated[session_auth.SessionRecord, Depends(require_csrf)],
    body: GrantConsentReq | None = None,
) -> dict[str, Any]:
    _human_only(rec)
    _check_scope(scope)
    subject = _subject(rec)
    from core.compliance.consent_store import MAX_TTL_DAYS, ConsentStoreError

    ttl_days = body.ttl_days if body and body.ttl_days is not None else MAX_TTL_DAYS
    if not isinstance(ttl_days, (int, float)) or not math.isfinite(ttl_days) or ttl_days <= 0:
        raise HTTPException(status_code=http_status.HTTP_400_BAD_REQUEST,
                            detail="ttl_days must be a positive number")
    ttl_days = min(float(ttl_days), float(MAX_TTL_DAYS))

    ref = _audit(EVENT_GRANTED, rec.tenant_id, {
        "consent_subject": subject, "sid_fingerprint": rec.sid_fingerprint,
        "consent_scope": scope,
        "ttl_s": int(ttl_days * 86400), "surface": "console",
    })
    if not ref:
        raise HTTPException(status_code=http_status.HTTP_503_SERVICE_UNAVAILABLE,
                            detail="consent not granted: the audit record could not be written")
    try:
        record = _store(rec.tenant_id).grant_consent(user_id=subject, scope=scope, ttl_days=ttl_days)
    except ConsentStoreError:
        raise HTTPException(status_code=http_status.HTTP_503_SERVICE_UNAVAILABLE,
                            detail="consent store unavailable") from None
    return {"scope": scope, "active": True, "granted_at": record.granted_at,
            "expires_at": record.expires_at, "audit_ref": ref}


@router.delete("/consent/{scope}")
async def revoke_consent(
    scope: str,
    rec: Annotated[session_auth.SessionRecord, Depends(require_csrf)],
) -> dict[str, Any]:
    _human_only(rec)
    _check_scope(scope)
    subject = _subject(rec)
    from core.compliance.consent_store import ConsentStoreError

    try:
        store = _store(rec.tenant_id)
        revoked = store.revoke_consent(user_id=subject, scope=scope)
        legacy = _sweep_legacy_subjects(store, subject, scope)
    except ConsentStoreError:
        raise HTTPException(status_code=http_status.HTTP_503_SERVICE_UNAVAILABLE,
                            detail="consent store unavailable") from None
    was_active = revoked is not None or legacy > 0
    ref = None
    if was_active:
        ref = _audit(EVENT_REVOKED, rec.tenant_id, {
            "consent_subject": subject, "sid_fingerprint": rec.sid_fingerprint,
            "consent_scope": scope, "surface": "console",
            "legacy_subjects_revoked": legacy or None,
        })
    return {"scope": scope, "active": False, "was_active": was_active,
            "audited": bool(ref) if was_active else None}
