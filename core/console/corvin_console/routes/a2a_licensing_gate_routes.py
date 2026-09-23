"""A2A Licensing Gate Routes (Phase 2, ADR-0704)

FastAPI endpoints for A2A delegation licensing:
- POST /v1/licensing/a2a/verify - Verify signed delegation task
- POST /v1/licensing/a2a/credential/issue - Issue new credential (authority only)
- GET /v1/licensing/a2a/credential/{id} - Get credential info
- POST /v1/licensing/a2a/credential/{id}/revoke - Revoke credential
- GET /v1/licensing/a2a/crl - Get revocation list
- GET /v1/licensing/a2a/audit - Get verification audit trail

License: Apache-2.0
"""

from __future__ import annotations

from typing import Optional, List, Dict, Any
import logging
from datetime import datetime

from fastapi import APIRouter, HTTPException, Query, Body, Path as PathParam
from pydantic import BaseModel

from core.licensing.member_credential import (
    SignedTask,
    MemberCredential,
)
from core.licensing.a2a_verifier import (
    A2ADelegationVerifier,
    VerificationResult,
)
from core.licensing.authority_server import (
    AuthorityServer,
    IssuanceResult,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/licensing", tags=["licensing"])

# Global service instances
_verifier: Optional[A2ADelegationVerifier] = None
_authority: Optional[AuthorityServer] = None


# ============================================================================
# Pydantic Models (API Schemas)
# ============================================================================

class SignedTaskRequest(BaseModel):
    """API request to verify a signed task."""
    task_id: str
    member_id: str
    credential_id: str
    operation: str
    payload: Dict[str, Any]
    signed_at: str
    signature: str
    ttl_seconds: int = 3600


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
    validity_days: int = 90
    metadata: Optional[Dict[str, Any]] = None


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


class CredentialInfoResponse(BaseModel):
    """Credential info for management."""
    credential_id: str
    member_id: str
    license_tier: str
    issued_at: str
    expires_at: str
    is_expired: bool
    is_revoked: bool
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
# Service Initialization
# ============================================================================

def init_service(corvin_home: str) -> None:
    """Initialize A2A licensing services.

    Args:
        corvin_home: Path to ~/.corvin
    """
    global _verifier, _authority
    _verifier = A2ADelegationVerifier(corvin_home)
    _authority = AuthorityServer(corvin_home)
    logger.info("A2A Licensing services initialized")


def get_verifier() -> A2ADelegationVerifier:
    """Get the verifier service."""
    if not _verifier:
        raise RuntimeError("Licensing services not initialized")
    return _verifier


def get_authority() -> AuthorityServer:
    """Get the authority service."""
    if not _authority:
        raise RuntimeError("Licensing services not initialized")
    return _authority


# ============================================================================
# API Routes
# ============================================================================

@router.post("/a2a/verify")
async def verify_signed_task(
    request: SignedTaskRequest = Body(...),
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
    verifier = get_verifier()

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
        logger.error(f"Verification error: {e}")
        raise HTTPException(status_code=500, detail="Verification failed")


@router.post("/a2a/credential/issue")
async def issue_credential(
    request: CredentialIssueRequest = Body(...),
) -> CredentialResponse:
    """Issue a new credential to a member.

    **AUTHORITY ONLY** - This endpoint should be protected by admin-only middleware.

    Args:
        request: Credential issuance request

    Returns:
        CredentialResponse with issued credential details
    """
    authority = get_authority()

    try:
        # TODO: Add admin-only authorization check

        result = authority.issue_credential(
            member_id=request.member_id,
            license_tier=request.license_tier,
            validity_days=request.validity_days,
            metadata=request.metadata,
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
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Issuance error: {e}")
        raise HTTPException(status_code=500, detail="Credential issuance failed")


@router.get("/a2a/credential/{credential_id}")
async def get_credential_info(
    credential_id: str = PathParam(..., description="Credential ID"),
) -> CredentialInfoResponse:
    """Get credential information.

    Args:
        credential_id: The credential to query

    Returns:
        CredentialInfoResponse with credential status
    """
    authority = get_authority()

    try:
        info = authority.get_credential_info(credential_id)

        if not info:
            raise HTTPException(
                status_code=404,
                detail=f"Credential not found: {credential_id}"
            )

        return CredentialInfoResponse(**info)

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting credential info: {e}")
        raise HTTPException(status_code=500, detail="Failed to get credential info")


@router.post("/a2a/credential/{credential_id}/revoke")
async def revoke_credential(
    credential_id: str = PathParam(..., description="Credential ID"),
    request: RevocationRequest = Body(...),
) -> Dict[str, Any]:
    """Revoke a credential.

    **AUTHORITY ONLY** - This endpoint should be protected by admin-only middleware.

    Revocation is immediate and irreversible.

    Args:
        credential_id: The credential to revoke
        request: Revocation request with reason

    Returns:
        Confirmation of revocation
    """
    authority = get_authority()

    try:
        # TODO: Add admin-only authorization check

        success = authority.revoke_credential(
            credential_id=credential_id,
            reason=request.reason,
        )

        if not success:
            raise HTTPException(
                status_code=400,
                detail=f"Failed to revoke credential: {credential_id}"
            )

        return {
            "revoked": True,
            "credential_id": credential_id,
            "revoked_at": datetime.utcnow().isoformat(),
            "reason": request.reason,
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Revocation error: {e}")
        raise HTTPException(status_code=500, detail="Revocation failed")


@router.get("/a2a/crl")
async def get_revocation_list() -> Dict[str, Any]:
    """Get the Certificate Revocation List (CRL).

    Lists all revoked credentials.

    Returns:
        Dictionary with revoked credential IDs and reasons
    """
    verifier = get_verifier()

    try:
        crl = verifier.revocation_list

        revoked_list = []
        for cred_id, entry in crl.revoked.items():
            revoked_list.append({
                "credential_id": cred_id,
                "revoked_at": entry.get("revoked_at"),
                "reason": entry.get("reason", ""),
            })

        return {
            "total_revoked": len(revoked_list),
            "updated_at": datetime.utcnow().isoformat(),
            "revoked": revoked_list,
        }

    except Exception as e:
        logger.error(f"Error getting CRL: {e}")
        raise HTTPException(status_code=500, detail="Failed to get CRL")


@router.get("/a2a/audit")
async def get_audit_trail(
    member_id: Optional[str] = Query(None, description="Filter by member ID"),
    limit: int = Query(100, ge=1, le=1000, description="Max events"),
) -> Dict[str, Any]:
    """Get A2A verification audit trail.

    Returns recent verification events.

    Args:
        member_id: Filter to specific member (optional)
        limit: Max events to return

    Returns:
        Audit events list
    """
    verifier = get_verifier()

    try:
        events = verifier.get_verification_audit(limit=limit, member_id=member_id)

        return {
            "total_events": len(events),
            "member_id_filter": member_id,
            "events": events,
        }

    except Exception as e:
        logger.error(f"Error getting audit trail: {e}")
        raise HTTPException(status_code=500, detail="Failed to get audit trail")


@router.get("/a2a/member/{member_id}/credentials")
async def list_member_credentials(
    member_id: str = PathParam(..., description="Member ID"),
    include_expired: bool = Query(False, description="Include expired credentials"),
) -> Dict[str, Any]:
    """List all credentials for a member.

    Args:
        member_id: The member to query
        include_expired: Include expired credentials

    Returns:
        List of credential infos
    """
    authority = get_authority()

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
        logger.error(f"Error listing member credentials: {e}")
        raise HTTPException(status_code=500, detail="Failed to list credentials")
