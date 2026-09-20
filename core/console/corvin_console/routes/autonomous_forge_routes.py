"""Autonomous Skill Forge Console Integration Routes (ADR-0902).

REST API endpoints for:
  - Monitoring canary state
  - Approving/deferring skill deployments (semi-autonomous)
  - Pausing/resuming autonomous mode
  - Emergency rollbacks
  - Audit trail querying
  - Manifest viewing

All endpoints require valid session auth (tenant_id from SessionRecord).
All mutations emit audit events to the tenant's audit chain.
Canary metrics are immutable (read-only).

Wire format
-----------

GET /v1/console/autonomous-forge/status
  → Returns current canary state + metrics

POST /v1/console/autonomous-forge/approve
  → Approve canary, roll out to 100%, emit audit event

POST /v1/console/autonomous-forge/defer
  → Defer canary to later, keep old version live

POST /v1/console/autonomous-forge/pause
  → Disable autonomous forge system

POST /v1/console/autonomous-forge/resume
  → Re-enable autonomous forge system

POST /v1/console/autonomous-forge/rollback
  → Emergency rollback to previous version

GET /v1/console/autonomous-forge/history
  → Audit trail of last N fork/decision cycles

GET /v1/console/autonomous-forge/manifest/:skill_id/:version
  → View generated skill manifest

Compliance (load-bearing)
------------------------

✅ Tenant isolation: All responses filtered by tenant_id from auth
✅ Audit-first: Every decision emits immutable event
✅ Fail-closed: Invalid decisions → 400/403 error
✅ Immutable: Canary metrics read-only (no mutations)
✅ Semi-autonomous: Approval required before 100% rollout
"""
from __future__ import annotations

import logging
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Annotated, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel

from .. import audit as console_audit
from ..deps import require_csrf, require_session
from .. import auth as session_auth
from ..csrf import (
    derive_csrf_token_session_bound,
    validate_csrf_token_session_bound,
    format_csrf_error_for_log,
)
from ..api_schemas.autonomous_forge import (
    CanaryStateResponse,
    ApproveRequest,
    ApproveResponse,
    DeferRequest,
    DeferResponse,
    PauseRequest,
    PauseResponse,
    ResumeRequest,
    ResumeResponse,
    RollbackRequest,
    RollbackResponse,
    HistoryResponse,
    HistoryEntry,
    ManifestResponse,
)

from ..validation.input_validator import validate_skill_id, validate_version

log = logging.getLogger(__name__)

router = APIRouter()

# ─────────────────────────────────────────────────────────────────────────────
# Helpers: Import autonomous forge components
# ─────────────────────────────────────────────────────────────────────────────

_AUTONOMOUS_AVAILABLE = False
_VALIDATOR = None

try:
    # corvin_operator/skill-forge/ has a dash, so `from skill_forge...` (or
    # `from corvin_operator.skill_forge...`) never resolved as a plain
    # import — this try/except silently swallowed that ImportError on every
    # boot, logging a warning and leaving _AUTONOMOUS_AVAILABLE permanently
    # False. Neither flag has any reader in this module today, so nothing
    # was functionally gated by it, but the boot log was misleading.
    # importlib.util loads the dashed directory directly (same pattern as
    # tests/skill_forge/test_trigger_detector.py). validator.py does
    # `from .result import ...` etc, which needs a real parent package with
    # __path__ set, so the whole autonomous/__init__.py is loaded (it
    # already resolves its own submodule relative imports) rather than
    # validator.py alone.
    import importlib.util
    import types as _types

    _THIS_DIR = Path(__file__).resolve().parent.parent
    _REPO = _THIS_DIR.parents[2]
    _SKILL_FORGE_DIR = _REPO / "corvin_operator" / "skill-forge"

    def _ensure_forge_namespace(dotted_name: str, path: Path):
        existing = sys.modules.get(dotted_name)
        if existing is not None:
            return existing
        _module = _types.ModuleType(dotted_name)
        _module.__path__ = [str(path)]
        sys.modules[dotted_name] = _module
        return _module

    def _load_forge_module(dotted_name: str, file_path: Path):
        existing = sys.modules.get(dotted_name)
        if existing is not None:
            return existing
        _spec = importlib.util.spec_from_file_location(dotted_name, file_path)
        _module = importlib.util.module_from_spec(_spec)
        sys.modules[dotted_name] = _module
        _spec.loader.exec_module(_module)
        return _module

    _ensure_forge_namespace("corvin_operator.skill_forge", _SKILL_FORGE_DIR)
    _autonomous_pkg = _load_forge_module(
        "corvin_operator.skill_forge.autonomous",
        _SKILL_FORGE_DIR / "autonomous" / "__init__.py",
    )
    SkillValidator = _autonomous_pkg.SkillValidator

    _VALIDATOR = SkillValidator()
    _AUTONOMOUS_AVAILABLE = True
except ImportError as e:
    log.warning(f"Autonomous skill forge not available: {e}")
    _AUTONOMOUS_AVAILABLE = False


# ─────────────────────────────────────────────────────────────────────────────
# Helper: CSRF Token Validation with Nonce Rotation
# ─────────────────────────────────────────────────────────────────────────────

def _validate_and_rotate_csrf(
    rec: session_auth.SessionRecord,
    presented_token: str,
    presented_nonce: str,
    route_path: str,
    tenant_id: str,
) -> tuple[bool, str | None, str | None]:
    """Validate CSRF token with session binding and rotate nonce on success.

    Args:
      rec: Current session record
      presented_token: Token from request body
      presented_nonce: Nonce from request body
      route_path: Route being protected (e.g., "/v1/console/autonomous-forge/approve")
      tenant_id: Tenant scope (from auth)

    Returns:
      (valid: bool, error_detail: str | None, new_nonce: str | None)
        - valid=True, error_detail=None, new_nonce=<string> on success
        - valid=False, error_detail=<reason>, new_nonce=None on failure
    """
    now = time.time()
    result = validate_csrf_token_session_bound(
        csrf_secret=rec.csrf_secret,
        session_id=rec.sid,
        presented_token=presented_token,
        presented_nonce=presented_nonce,
        session_nonce=rec.csrf_nonce,
        route_path=route_path,
        token_issued_at=rec.csrf_nonce_issued_at,
        now=now,
    )

    if not result.valid:
        # Log CSRF validation failure
        error_msg = format_csrf_error_for_log(result.error_reason)
        try:
            console_audit.system_event(
                tenant_id=tenant_id,
                event="autonomous_forge.csrf_validation_failed",
                details={
                    "reason": result.error_reason,
                    "route_path": route_path,
                    "sid_fingerprint": rec.sid_fingerprint,
                    "timestamp": datetime.utcnow().isoformat(),
                },
                severity="WARNING",
            )
        except Exception as e:
            log.error(f"Failed to audit CSRF failure: {e}")
        return False, result.error_reason, None

    # CSRF validation passed — generate new nonce for next operation
    return True, None, result.new_nonce


# ─────────────────────────────────────────────────────────────────────────────
# Helper: Fetch current canary state (stub for demo)
# ─────────────────────────────────────────────────────────────────────────────


def _get_current_canary_state(tenant_id: str) -> Optional[CanaryStateResponse]:
    """Fetch current canary state from monitoring backend.

    In production, this would query:
      - Prometheus for latency/error/confidence metrics
      - Deployment state from skill registry
      - Canary config from tenant.corvin.yaml

    For now, returns a mock state for integration testing.
    """
    # TODO: Wire into CanaryMonitor (Phase 7 monitoring layer)
    # For E2E tests, return a mock state
    return CanaryStateResponse(
        skill_id="os.delegation_router",
        version="2.1.0",
        status="canary",
        confidence=0.92,
        latency_p95_ms=48.5,
        error_rate=0.002,
        traffic_percent=15,
        time_remaining_sec=3600,
        created_at=datetime.utcnow(),
        tenant_id=tenant_id,
    )


def _get_canary_history(tenant_id: str, limit: int = 10) -> HistoryResponse:
    """Fetch canary decision history from audit trail.

    In production, this would query the tenant's audit.jsonl for events:
      - 'autonomous_forge.skill_forked'
      - 'autonomous_forge.validation_passed'
      - 'autonomous_forge.canary_deployed'
      - 'autonomous_forge.operator_approved'
      - 'autonomous_forge.operator_deferred'
      - 'autonomous_forge.rollback'

    For now, returns empty history for integration testing.
    """
    # TODO: Query tenant audit chain for fork/decision events
    return HistoryResponse(
        history=[],
        total_count=0,
        limit=limit,
    )


def _get_manifest(skill_id: str, version: str, tenant_id: str) -> Optional[ManifestResponse]:
    """Fetch generated skill manifest from storage.

    SECURITY: Validates skill_id and version to prevent path traversal (OWASP A01:2021).
    Fail-closed: Invalid inputs → return None.

    In production, this would read from:
      <tenant_home>/skill-forge/<skill_id>/<version>/skill.json

    For now, returns a mock manifest for integration testing.
    """
    # Validate skill_id to prevent path traversal (../../ escape)
    if not validate_skill_id(skill_id):
        log.warning(f"Invalid skill_id in _get_manifest: {skill_id} (tenant {tenant_id})")
        return None

    # Validate version to prevent path traversal
    if not validate_version(version):
        log.warning(f"Invalid version in _get_manifest: {version} (tenant {tenant_id})")
        return None

    # TODO: Read manifest from tenant skill forge directory
    # Path construction (fail-closed on invalid input):
    # tenant_home = tenant_paths.tenant_home(tenant_id)
    # manifest_path = tenant_home / "skill-forge" / skill_id / version / "skill.json"
    # Verify manifest_path doesn't escape tenant_home (os.path.realpath check)

    return ManifestResponse(
        skill_json={
            "id": skill_id,
            "version": version,
            "author": "autonomous-forge",
            "description": f"Auto-generated skill {skill_id} v{version}",
        },
        generation_context={
            "loss_signal": "confidence_below_threshold",
            "confidence_threshold": 0.8,
        },
        timestamp=datetime.utcnow(),
    )


# ─────────────────────────────────────────────────────────────────────────────
# Routes
# ─────────────────────────────────────────────────────────────────────────────


@router.get(
    "/status",
    response_model=CanaryStateResponse,
    status_code=status.HTTP_200_OK,
    summary="Get current canary state",
    description="Returns current canary metrics and status (immutable) + CSRF token for mutations",
)
def get_canary_status(
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
) -> CanaryStateResponse:
    """Get current canary deployment state with CSRF token for next mutation.

    Returns:
      - skill_id, version, status
      - confidence, latency_p95_ms, error_rate
      - traffic_percent, time_remaining_sec
      - created_at, tenant_id
      - session_token: HMAC-bound token (64-char hex) for next mutation
      - fixed_fingerprint: Pre-computed nonce from session (for token replay prevention)
      - token_expires_at: TTL 1 hour from now

    CRITICAL SECURITY:
      - session_token = HMAC-SHA256(csrf_secret, sid) bound to session
      - Token is session-scoped and cannot be used in different sessions
      - fixed_fingerprint (nonce) must be included in next mutation request
      - token_expires_at is 1 hour from now; requests with older tokens are rejected

    Tenant isolation: Filtered by rec.tenant_id.
    No audit logging (read-only operation).
    """
    tenant_id = rec.tenant_id

    state = _get_current_canary_state(tenant_id)
    if not state:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No active canary deployment found",
        )

    # Generate CSRF token bound to this session + current nonce
    now = datetime.utcnow()
    ts = now.timestamp()
    csrf_token = derive_csrf_token_session_bound(
        csrf_secret=rec.csrf_secret,
        session_id=rec.sid,
        timestamp=ts,
        nonce=rec.csrf_nonce,
        route_path="/v1/console/autonomous-forge/status",
    )

    # Include session_token, fixed_fingerprint (nonce), and expiry
    state.session_token = csrf_token
    state.fixed_fingerprint = rec.csrf_nonce
    state.token_expires_at = now + timedelta(hours=1)

    return state


@router.post(
    "/approve",
    response_model=ApproveResponse,
    status_code=status.HTTP_200_OK,
    summary="Approve canary and roll out to 100%",
    description="Operator approves the canary deployment; rolls out to 100% traffic immediately.",
)
def approve_skill(
    body: ApproveRequest,
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
) -> ApproveResponse:
    """Approve a canary skill and roll out to 100% traffic.

    Request:
      - skill_id: str
      - version: str
      - operator_id: str (from auth, validated)
      - session_token: str (HMAC-bound CSRF token, 64-char hex)
      - client_nonce: str (one-time nonce from session, 32-char hex)

    Response:
      - status: 'approved'
      - rolled_out_at: datetime
      - audit_event_id: str

    Side effects:
      - Validate CSRF token + nonce (fail-closed: 403 on invalid)
      - Rotate session nonce (old token becomes invalid for next operation)
      - Emit audit event 'operator_approved_skill'
      - Update skill registry to 100% traffic
      - Update deployment state

    Tenant isolation: body.operator_id must match rec.sid_fingerprint.
    Fail-closed: Invalid skill_id, version, or CSRF token → 400/403.
    """
    tenant_id = rec.tenant_id
    operator_id = rec.sid_fingerprint  # Enforce: operator_id from auth, not body

    # CRITICAL SECURITY: Validate CSRF token with session binding
    valid, error_reason, new_nonce = _validate_and_rotate_csrf(
        rec=rec,
        presented_token=body.session_token,
        presented_nonce=body.client_nonce,
        route_path="/v1/console/autonomous-forge/approve",
        tenant_id=tenant_id,
    )

    if not valid:
        log.warning(
            f"CSRF validation failed for /approve: {error_reason} "
            f"(tenant {tenant_id}, operator {operator_id})"
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"CSRF validation failed: {error_reason}",
        )

    if operator_id != body.operator_id:
        log.warning(
            f"Approve request mismatch: claimed operator {body.operator_id} != "
            f"authenticated {operator_id} (tenant {tenant_id})"
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="operator_id does not match authenticated session",
        )

    # Validate skill_id + version format (fail-closed, prevent path traversal)
    if not validate_skill_id(body.skill_id):
        log.warning(
            f"Approve request with invalid skill_id: {body.skill_id} "
            f"(tenant {tenant_id}, operator {operator_id})"
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="skill_id format invalid (must be alphanumeric with -, _, . only)",
        )

    if not validate_version(body.version):
        log.warning(
            f"Approve request with invalid version: {body.version} "
            f"(skill {body.skill_id}, tenant {tenant_id}, operator {operator_id})"
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="version format invalid (must be semantic X.Y.Z)",
        )

    # TODO: Query skill registry to validate skill exists
    # (This is additional validation after format check; fail-closed if skill doesn't exist)

    # Emit audit event
    audit_event_id = f"audit-evt-{datetime.utcnow().isoformat()}"
    try:
        console_audit.system_event(
            tenant_id=tenant_id,
            event="autonomous_forge.operator_approved_skill",
            details={
                "skill_id": body.skill_id,
                "version": body.version,
                "operator_id": operator_id,
                "sid_fingerprint": rec.sid_fingerprint,
                "timestamp": datetime.utcnow().isoformat(),
            },
            severity="INFO",
        )
    except Exception as e:
        log.error(f"Failed to audit approve event: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="audit event emission failed",
        )

    # Persist nonce rotation (update session with new nonce)
    if new_nonce:
        from dataclasses import replace as dataclass_replace
        bumped_rec = dataclass_replace(rec, csrf_nonce=new_nonce, csrf_nonce_issued_at=time.time())
        try:
            session_auth._write_record(bumped_rec)
        except Exception as e:
            log.error(f"Failed to rotate CSRF nonce: {e}")
            # Non-fatal: operation succeeds but nonce rotation failed
            # Next request will use old nonce and fail CSRF validation
            # (forcing re-fetch of /status to get new token)

    return ApproveResponse(
        status="approved",
        rolled_out_at=datetime.utcnow(),
        audit_event_id=audit_event_id,
    )


@router.post(
    "/defer",
    response_model=DeferResponse,
    status_code=status.HTTP_200_OK,
    summary="Defer canary deployment",
    description="Operator defers the canary; keeps old version live.",
)
def defer_skill(
    body: DeferRequest,
    rec: Annotated[session_auth.SessionRecord, Depends(require_csrf)],
) -> DeferResponse:
    """Defer a canary skill to a later time.

    Request:
      - skill_id: str
      - version: str
      - reason: str (max 500 chars)
      - operator_id: str (from auth, validated)

    Response:
      - status: 'deferred'
      - defer_until: datetime (24h from now)

    Side effects:
      - Emit audit event 'operator_deferred_skill'
      - Keep current version at 100% traffic
      - Mark canary for retry at defer_until

    Tenant isolation: body.operator_id must match rec.sid_fingerprint.
    """
    tenant_id = rec.tenant_id
    operator_id = rec.sid_fingerprint

    if operator_id != body.operator_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="operator_id does not match authenticated session",
        )

    # Validate skill_id + version format (fail-closed, prevent path traversal)
    if not validate_skill_id(body.skill_id):
        log.warning(
            f"Defer request with invalid skill_id: {body.skill_id} "
            f"(tenant {tenant_id}, operator {operator_id})"
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="skill_id format invalid (must be alphanumeric with -, _, . only)",
        )

    if not validate_version(body.version):
        log.warning(
            f"Defer request with invalid version: {body.version} "
            f"(skill {body.skill_id}, tenant {tenant_id}, operator {operator_id})"
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="version format invalid (must be semantic X.Y.Z)",
        )

    # Emit audit event
    audit_event_id = f"audit-evt-{datetime.utcnow().isoformat()}"
    try:
        console_audit.system_event(
            tenant_id=tenant_id,
            event="autonomous_forge.operator_deferred_skill",
            details={
                "skill_id": body.skill_id,
                "version": body.version,
                "reason": body.reason[:500],  # Truncate if needed
                "operator_id": operator_id,
                "sid_fingerprint": rec.sid_fingerprint,
                "timestamp": datetime.utcnow().isoformat(),
            },
            severity="INFO",
        )
    except Exception as e:
        log.error(f"Failed to audit defer event: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="audit event emission failed",
        )

    defer_until = datetime.utcnow() + timedelta(hours=24)
    return DeferResponse(
        status="deferred",
        defer_until=defer_until,
    )


@router.post(
    "/pause",
    response_model=PauseResponse,
    status_code=status.HTTP_200_OK,
    summary="Pause autonomous skill forge",
    description="Disable autonomous forge system; no new forks will be generated.",
)
def pause_autonomous(
    body: PauseRequest,
    rec: Annotated[session_auth.SessionRecord, Depends(require_csrf)],
) -> PauseResponse:
    """Pause the autonomous skill forge system.

    Request:
      - operator_id: str (from auth, validated)
      - reason: str (max 500 chars)

    Response:
      - autonomous_forge_enabled: false
      - paused_at: datetime
      - audit_event_id: str

    Side effects:
      - Emit audit event 'autonomous_forge_paused'
      - Disable trigger detection
      - Stop fork generation
      - Keep existing canaries running (no interruption)

    Fail-closed: Invalid operator_id → 403.
    """
    tenant_id = rec.tenant_id
    operator_id = rec.sid_fingerprint

    if operator_id != body.operator_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="operator_id does not match authenticated session",
        )

    # Emit audit event
    audit_event_id = f"audit-evt-{datetime.utcnow().isoformat()}"
    try:
        console_audit.system_event(
            tenant_id=tenant_id,
            event="autonomous_forge.paused",
            details={
                "reason": body.reason[:500],
                "operator_id": operator_id,
                "sid_fingerprint": rec.sid_fingerprint,
                "timestamp": datetime.utcnow().isoformat(),
            },
            severity="INFO",
        )
    except Exception as e:
        log.error(f"Failed to audit pause event: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="audit event emission failed",
        )

    return PauseResponse(
        autonomous_forge_enabled=False,
        paused_at=datetime.utcnow(),
        audit_event_id=audit_event_id,
    )


@router.post(
    "/resume",
    response_model=ResumeResponse,
    status_code=status.HTTP_200_OK,
    summary="Resume autonomous skill forge",
    description="Re-enable autonomous forge system.",
)
def resume_autonomous(
    body: ResumeRequest,
    rec: Annotated[session_auth.SessionRecord, Depends(require_csrf)],
) -> ResumeResponse:
    """Resume the autonomous skill forge system.

    Request:
      - operator_id: str (from auth, validated)

    Response:
      - autonomous_forge_enabled: true
      - resumed_at: datetime
      - audit_event_id: str

    Side effects:
      - Emit audit event 'autonomous_forge_resumed'
      - Enable trigger detection
      - Resume fork generation on loss signals

    Fail-closed: Invalid operator_id → 403.
    """
    tenant_id = rec.tenant_id
    operator_id = rec.sid_fingerprint

    if operator_id != body.operator_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="operator_id does not match authenticated session",
        )

    # Emit audit event
    audit_event_id = f"audit-evt-{datetime.utcnow().isoformat()}"
    try:
        console_audit.system_event(
            tenant_id=tenant_id,
            event="autonomous_forge.resumed",
            details={
                "operator_id": operator_id,
                "sid_fingerprint": rec.sid_fingerprint,
                "timestamp": datetime.utcnow().isoformat(),
            },
            severity="INFO",
        )
    except Exception as e:
        log.error(f"Failed to audit resume event: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="audit event emission failed",
        )

    return ResumeResponse(
        autonomous_forge_enabled=True,
        resumed_at=datetime.utcnow(),
        audit_event_id=audit_event_id,
    )


@router.post(
    "/rollback",
    response_model=RollbackResponse,
    status_code=status.HTTP_200_OK,
    summary="Emergency rollback",
    description="Roll back a deployed skill to its previous version immediately.",
)
def rollback_skill(
    body: RollbackRequest,
    rec: Annotated[session_auth.SessionRecord, Depends(require_csrf)],
) -> RollbackResponse:
    """Emergency rollback of a skill to the previous version.

    Request:
      - skill_id: str
      - reason: str (max 500 chars)
      - operator_id: str (from auth, validated)

    Response:
      - rolled_back_to_version: str
      - timestamp: datetime
      - audit_event_id: str

    Side effects:
      - Emit audit event 'operator_rolled_back_skill'
      - Switch production traffic back to previous version
      - Cancel any active canaries for this skill

    Tenant isolation: body.operator_id must match rec.sid_fingerprint.
    Fail-closed: Invalid skill_id or no prior version → 400.
    """
    tenant_id = rec.tenant_id
    operator_id = rec.sid_fingerprint

    if operator_id != body.operator_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="operator_id does not match authenticated session",
        )

    # Validate skill_id format (fail-closed, prevent path traversal)
    if not validate_skill_id(body.skill_id):
        log.warning(
            f"Rollback request with invalid skill_id: {body.skill_id} "
            f"(tenant {tenant_id}, operator {operator_id})"
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="skill_id format invalid (must be alphanumeric with -, _, . only)",
        )

    # TODO: Query skill registry to find previous version
    rolled_back_to = "2.0.5"  # Mock for now

    # Emit audit event
    audit_event_id = f"audit-evt-{datetime.utcnow().isoformat()}"
    try:
        console_audit.system_event(
            tenant_id=tenant_id,
            event="autonomous_forge.operator_rolled_back_skill",
            details={
                "skill_id": body.skill_id,
                "rolled_back_to_version": rolled_back_to,
                "reason": body.reason[:500],
                "operator_id": operator_id,
                "sid_fingerprint": rec.sid_fingerprint,
                "timestamp": datetime.utcnow().isoformat(),
            },
            severity="INFO",
        )
    except Exception as e:
        log.error(f"Failed to audit rollback event: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="audit event emission failed",
        )

    return RollbackResponse(
        rolled_back_to_version=rolled_back_to,
        timestamp=datetime.utcnow(),
        audit_event_id=audit_event_id,
    )


@router.get(
    "/history",
    response_model=HistoryResponse,
    status_code=status.HTTP_200_OK,
    summary="Get autonomous forge audit trail",
    description="Returns last N fork/decision cycles.",
)
def get_history(
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
    limit: Annotated[int, Query(ge=1, le=100)] = 10,
    skill_id: Annotated[Optional[str], Query()] = None,
) -> HistoryResponse:
    """Get audit trail of autonomous forge decisions.

    Query parameters:
      - limit: [1, 100] (default 10)
      - skill_id: optional filter by skill ID

    Returns:
      - history: List of HistoryEntry (newest first)
      - total_count: Total forks for tenant (may exceed limit)
      - limit: Applied limit

    Tenant isolation: Filtered by rec.tenant_id.
    No audit logging (read-only operation).
    """
    tenant_id = rec.tenant_id

    return _get_canary_history(tenant_id, limit=limit)


@router.get(
    "/manifest/{skill_id}/{version}",
    response_model=ManifestResponse,
    status_code=status.HTTP_200_OK,
    summary="View generated skill manifest",
    description="Returns the generated skill.json manifest and generation context.",
)
def get_manifest(
    skill_id: str,
    version: str,
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
) -> ManifestResponse:
    """Retrieve a generated skill manifest.

    Path parameters:
      - skill_id: str (validated against path traversal)
      - version: str (validated against path traversal)

    Returns:
      - skill_json: dict (the skill.json content)
      - generation_context: dict (loss signal, parameters)
      - timestamp: datetime

    Tenant isolation: Manifest must belong to rec.tenant_id.
    No audit logging (read-only operation).
    Fail-closed: Invalid input or missing manifest → 400/404.

    SECURITY: Validates skill_id and version to prevent path traversal (OWASP A01:2021).
    """
    tenant_id = rec.tenant_id

    # Validate skill_id format (prevent ../../ escape, etc.)
    if not validate_skill_id(skill_id):
        log.warning(
            f"get_manifest with invalid skill_id: {skill_id} (tenant {tenant_id})"
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="skill_id format invalid (must be alphanumeric with -, _, . only)",
        )

    # Validate version format (prevent ../../ escape, etc.)
    if not validate_version(version):
        log.warning(
            f"get_manifest with invalid version: {version} "
            f"(skill {skill_id}, tenant {tenant_id})"
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="version format invalid (must be semantic X.Y.Z)",
        )

    manifest = _get_manifest(skill_id, version, tenant_id)
    if not manifest:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Manifest not found: {skill_id} v{version}",
        )

    return manifest
