"""A2A Licensing Gate Routes (Phase 2, ADR-0704)

FastAPI endpoints for A2A delegation licensing:
- POST /v1/licensing/a2a/verify - Verify signed delegation task
- POST /v1/licensing/a2a/credential/issue - Issue new credential (owner only)
- GET /v1/licensing/a2a/credential/{id} - Get credential info
- POST /v1/licensing/a2a/credential/{id}/revoke - Revoke credential (owner only)
- GET /v1/licensing/a2a/crl - Get revocation list
- GET /v1/licensing/a2a/audit - Get verification audit trail

Tenant scoping: every route resolves its store from the authenticated
session's tenant (``tenant_home(rec.tenant_id)``), never from an env var or a
host-global directory. Until 2026-11-19 one store under the install root was
shared by every tenant.

License: Apache-2.0
"""

from __future__ import annotations

from typing import Optional, List, Dict, Any, Tuple
import logging
import threading
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, Body, Path as PathParam
from pydantic import BaseModel, Field, field_validator

from core.licensing.member_credential import (
    SignedTask,
    MemberCredential,
    MAX_TASK_TTL_SECONDS,
    validate_credential_id,
    validate_member_id,
    validate_task_id,
)
from core.licensing.a2a_verifier import (
    A2ADelegationVerifier,
    VerificationResult,
)
from core.licensing.authority_server import (
    AuthorityServer,
    IssuanceResult,
    MAX_VALIDITY_DAYS,
    MIN_VALIDITY_DAYS,
)
from .. import auth as session_auth
from ..deps import require_csrf, require_session, require_session_csrf_on_mutation

logger = logging.getLogger(__name__)

router = APIRouter(dependencies=[Depends(require_session_csrf_on_mutation)], prefix="/v1/licensing", tags=["licensing"])

# Path-parameter shapes (the same shapes the core validators enforce; declared
# here too so a malformed id is a 422 before any handler runs).
_CREDENTIAL_ID_PATTERN = r"^[A-Za-z0-9_.-]{1,64}(:\d+:[0-9a-f]{8})?$"
_MEMBER_ID_PATTERN = r"^[A-Za-z0-9_.-]{1,64}$"

# Every handler below is a plain ``def``: FastAPI runs it on the worker
# threadpool. Each one takes a lock, reads or writes the store, verifies or
# generates an RSA key, or appends to the audit chain (flock + fsync) - as
# ``async def`` all of that ran on the event loop.

# Per-tenant service instances, keyed by (tenant_id, resolved tenant home).
_services: Dict[Tuple[str, str], Tuple[A2ADelegationVerifier, AuthorityServer]] = {}
_services_lock = threading.Lock()


# ============================================================================
# Pydantic Models (API Schemas)
# ============================================================================

def _member_id(value: str) -> str:
    return validate_member_id(value)


def _credential_id(value: str) -> str:
    return validate_credential_id(value)


class SignedTaskRequest(BaseModel):
    """API request to verify a signed task."""
    task_id: str
    member_id: str
    credential_id: str
    operation: str = Field(..., min_length=1, max_length=64)
    payload: Dict[str, Any]
    signed_at: str = Field(..., max_length=64)
    signature: str = Field(..., max_length=4096)
    # Signed by the member and capped server-side; a larger value is refused
    # here rather than silently clamped, so a client learns its TTL is wrong.
    ttl_seconds: int = Field(3600, ge=1, le=MAX_TASK_TTL_SECONDS)

    _v_task = field_validator("task_id")(lambda v: validate_task_id(v))
    _v_member = field_validator("member_id")(_member_id)
    _v_cred = field_validator("credential_id")(_credential_id)


class VerificationResultResponse(BaseModel):
    """API response for task verification."""
    is_valid: bool
    task_id: str
    member_id: str
    credential_id: str
    license_tier: str
    checks_passed: List[str]
    checks_failed: List[str]
    error_message: str
    verified_at: str
    verification_latency_ms: int


class CredentialIssueRequest(BaseModel):
    """API request to issue a credential."""
    member_id: str
    license_tier: str  # "free", "member", "enterprise"
    validity_days: int = Field(90, ge=MIN_VALIDITY_DAYS, le=MAX_VALIDITY_DAYS)
    metadata: Optional[Dict[str, Any]] = None
    # The member's own RSA public key (PEM). Preferred: no private key then
    # ever exists on this host. Omitted -> a keypair is generated and its
    # private half is returned ONCE in the response.
    public_key_pem: Optional[str] = Field(None, max_length=8192)

    _v_member = field_validator("member_id")(_member_id)


class CredentialResponse(BaseModel):
    """API response with credential details."""
    credential_id: str
    member_id: str
    license_tier: str
    issued_at: str
    expires_at: str
    public_key_pem: str
    issuer: str
    metadata: Dict[str, Any]
    # Present only when the authority generated the keypair; shown once and
    # stored nowhere. ``None`` when the member submitted a public key.
    private_key_pem: Optional[str] = None


class CredentialInfoResponse(BaseModel):
    """Credential info for management."""
    credential_id: str
    member_id: str
    license_tier: str
    issued_at: str
    expires_at: str
    is_expired: bool
    is_revoked: bool
    # False when the stored credential does not carry the authority's
    # signature; ``is_valid`` and ``can_delegate`` are False then.
    signature_valid: bool = False
    is_valid: bool
    can_delegate: bool
    remaining_ttl_seconds: int
    revocation_reason: Optional[str]


class RevocationRequest(BaseModel):
    """Request to revoke a credential."""
    reason: str = "No reason provided"


class AuditEventResponse(BaseModel):
    """Audit event in trail."""
    timestamp: str
    event_type: str
    task_id: Optional[str] = None
    member_id: Optional[str] = None
    credential_id: Optional[str] = None
    is_valid: Optional[bool] = None
    license_tier: Optional[str] = None
    checks_passed: Optional[List[str]] = None
    checks_failed: Optional[List[str]] = None
    error_message: Optional[str] = None


# ============================================================================
# Service Resolution (per tenant)
# ============================================================================

def init_service(corvin_home: str) -> None:
    """Kept for the boot call in ``app.py``; holds no state anymore.

    The services used to be two host-global singletons rooted at
    ``corvin_home``. They are now built lazily per tenant from the session
    (see :func:`_tenant_services`), so there is nothing to initialise.
    """
    logger.info("A2A Licensing services resolve per tenant (lazy)")


def _tenant_root(tenant_id: str) -> Path:
    # The CANONICAL resolver (CLAUDE.md, Multi-tenant Axis): it is what admits
    # a tenant everywhere else. ``core.tenants`` additionally refuses a list of
    # reserved names (``test``, ``local``, ``admin``, ...) that the canonical
    # one accepts, so a session of such a tenant got 403 here.
    from forge import paths as _forge_paths  # noqa: PLC0415 - validates tenant_id
    return Path(_forge_paths.tenant_home(tenant_id))


def _tenant_services(
    rec: session_auth.SessionRecord,
) -> Tuple[A2ADelegationVerifier, AuthorityServer]:
    """Verifier + authority for the SESSION's tenant (never an env var)."""
    try:
        root = _tenant_root(rec.tenant_id)
    except ValueError:
        raise HTTPException(status_code=403, detail="invalid tenant")
    key = (rec.tenant_id, str(root))
    with _services_lock:
        pair = _services.get(key)
        if pair is None:
            pair = (
                A2ADelegationVerifier(str(root), tenant_id=rec.tenant_id),
                AuthorityServer(str(root), tenant_id=rec.tenant_id),
            )
            _services[key] = pair
        return pair


def _require_owner(rec: session_auth.SessionRecord) -> session_auth.SessionRecord:
    """The console has exactly one human role, ``owner`` (``auth.Tier``).

    Credential issuance and revocation are authority actions: fail closed for
    any other tier and for the synthetic record of an LLM-driven tool call.
    """
    if getattr(rec, "tier", None) != "owner" or getattr(rec, "is_internal_tool", False):
        raise HTTPException(status_code=403, detail="owner required")
    return rec


def _owner_mutation(
    rec: session_auth.SessionRecord = Depends(require_csrf),
) -> session_auth.SessionRecord:
    return _require_owner(rec)


def _owner_read(
    rec: session_auth.SessionRecord = Depends(require_session),
) -> session_auth.SessionRecord:
    return _require_owner(rec)


# ============================================================================
# API Routes
# ============================================================================

@router.post("/a2a/verify")
def verify_signed_task(
    request: SignedTaskRequest = Body(...),
    rec: session_auth.SessionRecord = Depends(require_csrf),
) -> VerificationResultResponse:
    """Verify an RSA-signed A2A delegation task.

    Performs fail-closed validation:
    - Signature must be valid
    - Credential must exist and not expired
    - Credential must not be revoked
    - License tier must permit delegation
    - Task must not be expired

    Args:
        request: Signed task to verify

    Returns:
        VerificationResultResponse with validation details
    """
    verifier, _authority = _tenant_services(rec)

    try:
        signed_task = SignedTask(
            task_id=request.task_id,
            member_id=request.member_id,
            credential_id=request.credential_id,
            operation=request.operation,
            payload=request.payload,
            signed_at=request.signed_at,
            signature=request.signature,
            ttl_seconds=request.ttl_seconds,
        )

        result = verifier.verify_signed_task(signed_task)

        return VerificationResultResponse(
            is_valid=result.is_valid,
            task_id=result.task_id,
            member_id=result.member_id,
            credential_id=result.credential_id,
            license_tier=result.license_tier,
            checks_passed=result.checks_passed,
            checks_failed=result.checks_failed,
            error_message=result.error_message,
            verified_at=result.verified_at,
            verification_latency_ms=result.verification_latency_ms,
        )

    except Exception as e:
        logger.error("Verification error: %s", type(e).__name__)
        raise HTTPException(status_code=500, detail="Verification failed")


@router.post("/a2a/credential/issue")
def issue_credential(
    request: CredentialIssueRequest = Body(...),
    rec: session_auth.SessionRecord = Depends(_owner_mutation),
) -> CredentialResponse:
    """Issue a new credential to a member.

    **AUTHORITY ONLY** - owner session + CSRF (``_owner_mutation``); 403 otherwise.

    When no ``public_key_pem`` is submitted the response carries the generated
    ``private_key_pem`` exactly once. It is not stored and cannot be fetched
    again.

    Args:
        request: Credential issuance request

    Returns:
        CredentialResponse with issued credential details
    """
    _verifier, authority = _tenant_services(rec)

    try:
        result = authority.issue_credential(
            member_id=request.member_id,
            license_tier=request.license_tier,
            validity_days=request.validity_days,
            metadata=request.metadata,
            public_key_pem=request.public_key_pem,
        )

        if not result.success:
            raise HTTPException(
                status_code=400,
                detail=f"Issuance failed: {result.error_message}"
            )

        cred = result.credential
        return CredentialResponse(
            credential_id=cred.credential_id,
            member_id=cred.member_id,
            license_tier=cred.license_tier,
            issued_at=cred.issued_at,
            expires_at=cred.expires_at,
            public_key_pem=cred.public_key_pem,
            issuer=cred.issuer,
            metadata=cred.metadata,
            private_key_pem=result.private_key_pem,
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error("Issuance error: %s", type(e).__name__)
        raise HTTPException(status_code=500, detail="Credential issuance failed")


@router.get("/a2a/credential/{credential_id}")
def get_credential_info(
    credential_id: str = PathParam(..., description="Credential ID", pattern=_CREDENTIAL_ID_PATTERN),
    rec: session_auth.SessionRecord = Depends(require_session),
) -> CredentialInfoResponse:
    """Get credential information.

    Args:
        credential_id: The credential to query

    Returns:
        CredentialInfoResponse with credential status
    """
    _verifier, authority = _tenant_services(rec)

    try:
        info = authority.get_credential_info(credential_id)

        if not info:
            raise HTTPException(status_code=404, detail="Credential not found")

        return CredentialInfoResponse(**info)

    except HTTPException:
        raise
    except Exception as e:
        logger.error("Error getting credential info: %s", type(e).__name__)
        raise HTTPException(status_code=500, detail="Failed to get credential info")


@router.post("/a2a/credential/{credential_id}/revoke")
def revoke_credential(
    credential_id: str = PathParam(..., description="Credential ID", pattern=_CREDENTIAL_ID_PATTERN),
    request: RevocationRequest = Body(...),
    rec: session_auth.SessionRecord = Depends(_owner_mutation),
) -> Dict[str, Any]:
    """Revoke a credential.

    **AUTHORITY ONLY** - owner session + CSRF (``_owner_mutation``); 403 otherwise.

    Revocation is immediate and irreversible.

    Args:
        credential_id: The credential to revoke
        request: Revocation request with reason

    Returns:
        Confirmation of revocation
    """
    _verifier, authority = _tenant_services(rec)

    try:
        success = authority.revoke_credential(
            credential_id=credential_id,
            reason=request.reason,
        )

        if not success:
            raise HTTPException(status_code=400, detail="Failed to revoke credential")

        return {
            "revoked": True,
            "credential_id": credential_id,
            "revoked_at": datetime.now(timezone.utc).isoformat(),
            "reason": request.reason,
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error("Revocation error: %s", type(e).__name__)
        raise HTTPException(status_code=500, detail="Revocation failed")


@router.get("/a2a/crl")
def get_revocation_list(
    rec: session_auth.SessionRecord = Depends(require_session),
) -> Dict[str, Any]:
    """Get the Certificate Revocation List (CRL).

    Lists all revoked credentials.

    Returns:
        Dictionary with revoked credential IDs and reasons
    """
    verifier, _authority = _tenant_services(rec)

    try:
        # One consistent copy: iterating the live dict raced a concurrent
        # revocation ("dictionary changed size during iteration" -> 500).
        load_failed, entries = verifier.revocation_list.snapshot()
        if load_failed:
            # Honest state: the list cannot be read, so every credential is
            # being treated as revoked. Never an empty "nothing revoked".
            raise HTTPException(
                status_code=503,
                detail="Revocation list is unreadable; all credentials are treated as revoked",
            )

        revoked_list = []
        for cred_id, entry in entries:
            revoked_list.append({
                "credential_id": cred_id,
                "revoked_at": entry.get("revoked_at"),
                "reason": entry.get("reason", ""),
            })

        return {
            "total_revoked": len(revoked_list),
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "revoked": revoked_list,
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error("Error getting CRL: %s", type(e).__name__)
        raise HTTPException(status_code=500, detail="Failed to get CRL")


@router.get("/a2a/audit")
def get_audit_trail(
    member_id: Optional[str] = Query(None, description="Filter by member ID", pattern=_MEMBER_ID_PATTERN),
    limit: int = Query(100, ge=1, le=1000, description="Max events"),
    rec: session_auth.SessionRecord = Depends(require_session),
) -> Dict[str, Any]:
    """Get A2A verification audit trail.

    Returns recent verification events.

    Args:
        member_id: Filter to specific member (optional)
        limit: Max events to return

    Returns:
        Audit events list
    """
    verifier, _authority = _tenant_services(rec)

    try:
        events = verifier.get_verification_audit(limit=limit, member_id=member_id)

        return {
            "total_events": len(events),
            "member_id_filter": member_id,
            "events": events,
        }

    except Exception as e:
        logger.error("Error getting audit trail: %s", type(e).__name__)
        raise HTTPException(status_code=500, detail="Failed to get audit trail")


@router.get("/a2a/member/{member_id}/credentials")
def list_member_credentials(
    member_id: str = PathParam(..., description="Member ID", pattern=_MEMBER_ID_PATTERN),
    include_expired: bool = Query(False, description="Include expired credentials"),
    rec: session_auth.SessionRecord = Depends(require_session),
) -> Dict[str, Any]:
    """List all credentials for a member.

    Args:
        member_id: The member to query
        include_expired: Include expired credentials

    Returns:
        List of credential infos
    """
    _verifier, authority = _tenant_services(rec)

    try:
        credentials = authority.list_member_credentials(
            member_id=member_id,
            include_expired=include_expired,
        )

        return {
            "member_id": member_id,
            "total": len(credentials),
            "credentials": credentials,
        }

    except Exception as e:
        logger.error("Error listing member credentials: %s", type(e).__name__)
        raise HTTPException(status_code=500, detail="Failed to list credentials")
