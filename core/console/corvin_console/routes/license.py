"""License management — view, upload, revoke Enterprise license keys.

Endpoints
---------
  GET    /v1/console/license/status        → current license status + tier
  POST   /v1/console/license/upload        → upload new license.jwt file
  POST   /v1/console/license/revoke        → revoke active license
  GET    /v1/console/license/audit-tail    → recent license audit events

Compliance baseline mirrors ``routes/settings.py``:
  * Owner-only access (rec.tier == "owner")
  * CSRF required on all POST operations
  * Input validation with Pydantic ``extra="forbid"``
  * Audit events for all mutations (license.uploaded, license.removed)
  * File mode 0o600 enforced on license.jwt writes
"""
from __future__ import annotations

import json
import os
import re
import sys
import tempfile
import time
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi import status as http_status
from pydantic import BaseModel, Field

from .. import auth as session_auth
from .. import audit as console_audit
from .. import _bootstrap
from ..deps import require_csrf, require_session

_forge_paths = _bootstrap.forge_paths

_THIS_DIR = Path(__file__).resolve().parent
_REPO = _THIS_DIR.parents[3]

# ADR-0092: operator/license/ module (new, primary)
_OPERATOR = _REPO / "operator"
if str(_OPERATOR) not in sys.path:
    sys.path.insert(0, str(_OPERATOR))

try:
    from license.validator import (  # type: ignore
        get_limit as _lic_get_limit,
        get_feature as _lic_get_feature,
        get_custom as _lic_get_custom,
        active_tier as _lic_active_tier,
        is_loaded as _lic_is_loaded,
    )
    from license.limits import FREE_TIER as _FREE_TIER  # type: ignore
    _ADR0092_OK = True
except Exception:
    _ADR0092_OK = False
    _lic_get_limit = lambda f: 0  # type: ignore[assignment]  # noqa: E731
    _lic_get_feature = lambda f: False  # type: ignore[assignment]  # noqa: E731
    _lic_get_custom = lambda f, **kw: None  # type: ignore[assignment]  # noqa: E731
    _lic_active_tier = lambda: "free"  # type: ignore[assignment]  # noqa: E731
    _lic_is_loaded = lambda: False  # type: ignore[assignment]  # noqa: E731
    _FREE_TIER: dict = {}  # type: ignore[assignment]

# Legacy corvin_license plugin (ADR-0017) — kept for upload/revoke flow
_LICENSE_PATH = _REPO / "core" / "license"
if str(_LICENSE_PATH) not in sys.path:
    sys.path.insert(0, str(_LICENSE_PATH))

try:
    from corvin_license import verifier as _verifier
    from corvin_license import audit as _license_audit
    from corvin_license import grace as _grace
except ImportError:
    _verifier = None  # type: ignore[assignment]
    _license_audit = None  # type: ignore[assignment]
    _grace = None  # type: ignore[assignment]


router = APIRouter(prefix="/license")

_MAX_LICENSE_FILE_SIZE = 10 * 1024  # 10 KB for JWT

# ADR-0007: tenant_id charset rule — mirrors forge.forge.tenants._TENANT_ID_RE
_TENANT_ID_RE = re.compile(r"^[a-z0-9_][a-z0-9_-]{0,62}$")

_AUDIT_TAIL_DETAILS_ALLOWLIST = frozenset({
    "tier", "jti", "feature", "limit_value", "requested_value", "reason",
})


# ── Schemas ────────────────────────────────────────────────────────────


class LicenseStatus(BaseModel):
    """Current license status response."""
    tier: str
    mode: str  # "free", "active", "grace", "expired", "invalid"
    expires_at: int | None = None
    grace_ends_at: int | None = None
    customer_fp: str | None = None
    feature_flags: list[str] = Field(default_factory=list)

    model_config = {"extra": "forbid"}


class LicenseUploadResponse(BaseModel):
    """Response after successful license upload."""
    ok: bool
    tier: str
    customer_fp: str
    expires_at: int

    model_config = {"extra": "forbid"}


class LicenseRevokeRequest(BaseModel):
    """Request to revoke current license."""
    reason: str = Field(
        default="operator-revoke",
        max_length=200,
        description="Reason for revocation"
    )

    model_config = {"extra": "forbid"}


class AuditEvent(BaseModel):
    """Single audit event from license.*"""
    timestamp: float
    event_type: str
    details: dict[str, Any]

    model_config = {"extra": "forbid"}


# ── Helper functions ────────────────────────────────────────────────────


def _check_license_plugin() -> None:
    """Verify corvin_license plugin is available."""
    if _verifier is None:
        raise HTTPException(
            status_code=http_status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="License plugin not installed",
        )


def _compute_license_status() -> LicenseStatus:
    """Compute current license status from disk."""
    _check_license_plugin()

    try:
        lic_file = _verifier.license_file_path()
        if not lic_file.exists():
            # No Enterprise (on-prem) license.jwt installed — the normal case
            # for a Paddle/consumer subscriber, licensed instead through the
            # separate operator/license system (license.key, checked above at
            # import time). Falling back to a hardcoded "free" here shadowed
            # an active Member subscription on the Dashboard (GET
            # /license/status) even though /license/info correctly showed
            # "member" for the very same license — same tier, two different
            # pages disagreeing.
            _op_tier = _lic_active_tier()
            if _op_tier != "free":
                return LicenseStatus(
                    tier=_op_tier,
                    mode="active",
                    customer_fp=None,
                )
            return LicenseStatus(
                tier="free",
                mode="free",
                customer_fp=None,
            )

        # Read and verify JWT
        token = lic_file.read_text(encoding="utf-8").strip()
        pubkey = _verifier.load_pubkey()
        lic = _verifier.verify_token(token, pubkey_pem=pubkey)

        fp = _verifier.fingerprint_customer_id(lic.customer_id)

        # Check grace period — assess() returns GraceStatus (not GraceState)
        grace_status = _grace.assess(valid_until=lic.valid_until)
        if grace_status.in_grace:
            return LicenseStatus(
                tier=lic.tier,
                mode="grace",
                expires_at=lic.valid_until,
                grace_ends_at=grace_status.grace_ends_at,
                customer_fp=fp,
                feature_flags=list(lic.feature_flags),
            )

        # Check expiry
        if int(time.time()) >= lic.valid_until:
            return LicenseStatus(
                tier=lic.tier,
                mode="expired",
                expires_at=lic.valid_until,
                customer_fp=fp,
                feature_flags=list(lic.feature_flags),
            )

        # Active license
        return LicenseStatus(
            tier=lic.tier,
            mode="active",
            expires_at=lic.valid_until,
            customer_fp=fp,
            feature_flags=list(lic.feature_flags),
        )

    except _verifier.LicenseFileMissing:
        return LicenseStatus(tier="free", mode="free", customer_fp=None)
    except _verifier.LicenseExpired as e:
        fp = e.customer_fingerprint
        tier = e.tier or "unknown"
        expired_at = e.expired_at
        grace_status = _grace.assess(valid_until=expired_at)
        if grace_status.in_grace:
            return LicenseStatus(
                tier=tier,
                mode="grace",
                expires_at=expired_at,
                grace_ends_at=grace_status.grace_ends_at,
                customer_fp=fp,
            )
        return LicenseStatus(
            tier=tier,
            mode="expired",
            expires_at=expired_at,
            customer_fp=fp,
        )
    except _verifier.LicenseError as e:
        return LicenseStatus(
            tier="unknown",
            mode="invalid",
            customer_fp=None,
        )
    except Exception as e:
        return LicenseStatus(
            tier="unknown",
            mode="invalid",
            customer_fp=None,
        )


# ── Routes ─────────────────────────────────────────────────────────────


@router.get("/status", response_model=LicenseStatus)
async def get_license_status(
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
) -> LicenseStatus:
    """Get current license status and tier."""
    if rec.tier != "owner":
        raise HTTPException(
            status_code=http_status.HTTP_403_FORBIDDEN,
            detail="Owner access required",
        )
    _check_license_plugin()
    return _compute_license_status()


@router.post("/upload", response_model=LicenseUploadResponse)
async def upload_license(
    rec: Annotated[session_auth.SessionRecord, Depends(require_csrf)],
    file: Annotated[UploadFile, File(...)],
) -> LicenseUploadResponse:
    """Upload and install a new license.jwt file.

    Validates RS256 signature before persisting.
    """
    if rec.tier != "owner":
        try:
            console_audit.action_denied(
                tenant_id=rec.tenant_id,
                sid_fingerprint=rec.sid_fingerprint,
                action="license.upload",
                target_kind="license",
                target_id="pending",
                reason="insufficient_tier",
            )
        except Exception:
            pass
        raise HTTPException(
            status_code=http_status.HTTP_403_FORBIDDEN,
            detail="Owner access required",
        )

    _check_license_plugin()

    # Validate file
    if not file.filename or not file.filename.endswith(".jwt"):
        try:
            console_audit.action_failed(
                tenant_id=rec.tenant_id,
                sid_fingerprint=rec.sid_fingerprint,
                action="license.upload",
                target_kind="license",
                target_id="pending",
                reason="invalid_filename",
            )
        except Exception:
            pass
        raise HTTPException(
            status_code=http_status.HTTP_400_BAD_REQUEST,
            detail="File must be a .jwt file",
        )

    content = await file.read()
    if len(content) > _MAX_LICENSE_FILE_SIZE:
        try:
            console_audit.action_failed(
                tenant_id=rec.tenant_id,
                sid_fingerprint=rec.sid_fingerprint,
                action="license.upload",
                target_kind="license",
                target_id="pending",
                reason="file_too_large",
            )
        except Exception:
            pass
        raise HTTPException(
            status_code=http_status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"License file must be < {_MAX_LICENSE_FILE_SIZE} bytes",
        )

    try:
        token = content.decode("utf-8").strip()
        if not token:
            raise ValueError("Empty token")
    except Exception:
        try:
            console_audit.action_failed(
                tenant_id=rec.tenant_id,
                sid_fingerprint=rec.sid_fingerprint,
                action="license.upload",
                target_kind="license",
                target_id="pending",
                reason="invalid_encoding",
            )
        except Exception:
            pass
        raise HTTPException(
            status_code=http_status.HTTP_400_BAD_REQUEST,
            detail="License file must be UTF-8 encoded",
        )

    # Verify JWT before installing
    try:
        pubkey = _verifier.load_pubkey()
        lic = _verifier.verify_token(token, pubkey_pem=pubkey)
    except _verifier.LicenseError as e:
        try:
            console_audit.action_failed(
                tenant_id=rec.tenant_id,
                sid_fingerprint=rec.sid_fingerprint,
                action="license.upload",
                target_kind="license",
                target_id="pending",
                reason="signature_invalid",
            )
        except Exception:
            pass
        import logging as _logging
        _logging.getLogger(__name__).warning("License validation failed: %s", e)
        raise HTTPException(
            status_code=http_status.HTTP_400_BAD_REQUEST,
            detail="License validation failed",
        )
    except Exception as e:
        try:
            console_audit.action_failed(
                tenant_id=rec.tenant_id,
                sid_fingerprint=rec.sid_fingerprint,
                action="license.upload",
                target_kind="license",
                target_id="pending",
                reason="verification_error",
            )
        except Exception:
            pass
        raise HTTPException(
            status_code=http_status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="License verification failed",
        )

    # Audit FIRST — audit-first invariant (GDPR Art. 30, CLAUDE.md §L16)
    fp = _verifier.fingerprint_customer_id(lic.customer_id)
    try:
        _license_audit.license_activated(
            tier=lic.tier,
            customer_fp=fp,
            valid_until=lic.valid_until,
            issued_at=lic.issued_at,
            employee_count_max=lic.employee_count_max,
            seats=lic.seats,
            feature_flags=list(lic.feature_flags),
        )
    except Exception:
        pass

    # Write to canonical location
    try:
        dest = _verifier.license_file_path()
        dest.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
        # tempfile.mkstemp + os.replace, not os.open(dest, O_CREAT|O_TRUNC):
        # the O_TRUNC form only applies its mode argument when the file is
        # newly CREATED — if dest already exists (e.g. left permissive by a
        # historical bug, bad umask, or backup restore), O_TRUNC opens and
        # truncates the EXISTING file in place without ever correcting its
        # mode, and (unlike mkstemp+replace) follows a symlink planted at
        # dest instead of atomically swapping it out (adversarial review
        # finding — this reintroduced the exact bug already fixed for the
        # sibling apply_license_key endpoint below).
        _fd, _tmp = tempfile.mkstemp(dir=dest.parent, prefix=".license.", suffix=".tmp")
        try:
            with os.fdopen(_fd, "w", encoding="utf-8") as _fh:
                _fh.write(token)
            os.chmod(_tmp, 0o600)
            os.replace(_tmp, dest)
        except Exception:
            try:
                os.unlink(_tmp)
            except OSError:
                pass
            raise

        # Update grace state with new expiry
        _grace.remember_valid_license(
            valid_until=lic.valid_until,
            customer_fingerprint=fp,
        )
    except Exception as e:
        try:
            console_audit.action_failed(
                tenant_id=rec.tenant_id,
                sid_fingerprint=rec.sid_fingerprint,
                action="license.upload",
                target_kind="license",
                target_id="pending",
                reason="write_failed",
            )
        except Exception:
            pass
        raise HTTPException(
            status_code=http_status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to save license file",
        )

    # Console audit
    try:
        console_audit.action_performed(
            tenant_id=rec.tenant_id,
            sid_fingerprint=rec.sid_fingerprint,
            action="license.upload",
            target_kind="license",
            target_id=fp,
        )
    except Exception:
        pass

    return LicenseUploadResponse(
        ok=True,
        tier=lic.tier,
        customer_fp=fp,
        expires_at=lic.valid_until,
    )


@router.post("/revoke")
async def revoke_license(
    req: LicenseRevokeRequest,
    rec: Annotated[session_auth.SessionRecord, Depends(require_csrf)],
) -> dict[str, bool]:
    """Revoke the currently installed license."""
    if rec.tier != "owner":
        try:
            console_audit.action_denied(
                tenant_id=rec.tenant_id,
                sid_fingerprint=rec.sid_fingerprint,
                action="license.revoke",
                target_kind="license",
                target_id="pending",
                reason="insufficient_tier",
            )
        except Exception:
            pass
        raise HTTPException(
            status_code=http_status.HTTP_403_FORBIDDEN,
            detail="Owner access required",
        )

    _check_license_plugin()

    fp = "unknown"
    dest = _verifier.license_file_path()

    # Read fingerprint before mutations — needed for audit trail
    if dest.exists():
        try:
            pubkey = _verifier.load_pubkey()
            lic = _verifier.verify_token(
                dest.read_text(encoding="utf-8").strip(),
                pubkey_pem=pubkey,
            )
            fp = _verifier.fingerprint_customer_id(lic.customer_id)
        except Exception:
            pass

    # Audit FIRST — audit-first invariant (GDPR Art. 30, CLAUDE.md §L16)
    try:
        _license_audit.license_revoked(
            customer_fp=fp,
            reason=req.reason,
        )
    except Exception:
        pass

    # Perform the mutations
    try:
        if dest.exists():
            dest.unlink()
        _grace.reset_state()
    except Exception as e:
        try:
            console_audit.action_failed(
                tenant_id=rec.tenant_id,
                sid_fingerprint=rec.sid_fingerprint,
                action="license.revoke",
                target_kind="license",
                target_id=fp,
                reason="revoke_failed",
            )
        except Exception:
            pass
        raise HTTPException(
            status_code=http_status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to revoke license",
        )

    try:
        console_audit.action_performed(
            tenant_id=rec.tenant_id,
            sid_fingerprint=rec.sid_fingerprint,
            action="license.revoke",
            target_kind="license",
            target_id=fp,
        )
    except Exception:
        pass

    return {"ok": True}


@router.get("/audit-tail", response_model=list[AuditEvent])
async def get_license_audit(
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
    limit: int = 100,
) -> list[AuditEvent]:
    """Get recent license audit events (metadata only)."""
    if rec.tier != "owner":
        raise HTTPException(
            status_code=http_status.HTTP_403_FORBIDDEN,
            detail="Owner access required",
        )

    _check_license_plugin()

    events: list[AuditEvent] = []

    try:
        # ADR-0007: validate tenant_id before constructing file paths
        if not _TENANT_ID_RE.match(rec.tenant_id):
            return events
        # Use the same path as audit.py:_audit_path() — forge/audit.jsonl under the tenant global dir
        audit_file = console_audit._audit_path(rec.tenant_id)
        if not audit_file.exists():
            return events

        with open(audit_file, "r", encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                try:
                    entry = json.loads(line)
                    if entry.get("event_type", "").startswith("license."):
                        raw_details = entry.get("details", {})
                        filtered = {k: v for k, v in raw_details.items() if k in _AUDIT_TAIL_DETAILS_ALLOWLIST}
                        events.append(AuditEvent(
                            timestamp=entry.get("timestamp", 0),
                            event_type=entry.get("event_type", ""),
                            details=filtered,
                        ))
                except (json.JSONDecodeError, ValueError):
                    pass

        # Return last N events
        return sorted(events, key=lambda e: e.timestamp, reverse=True)[:limit]

    except Exception:
        return []


# ── ADR-0092 info endpoint ─────────────────────────────────────────────

class LicenseInfo(BaseModel):
    """Full ADR-0092 license state — returned by /license/info."""
    tier: str
    loaded: bool
    issued_to: str | None = None
    expires_at: int | None = None
    subscription_active_until: int | None = None
    jti_prefix: str | None = None  # first 8 chars only — never full jti
    limits: dict[str, Any] = Field(default_factory=dict)
    features: dict[str, Any] = Field(default_factory=dict)
    custom: dict[str, Any] = Field(default_factory=dict)
    free_tier: dict[str, Any] = Field(default_factory=dict)

    model_config = {"extra": "forbid"}


@router.get("/info", response_model=LicenseInfo)
async def get_license_info(
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
) -> LicenseInfo:
    """ADR-0092 — full licence state from operator/license/.

    Returns the active SesT claims (limits, features, custom) plus the
    FREE_TIER defaults so the UI can show what each limit means without
    a key. Accessible to all authenticated users (not owner-only) so
    the UI can render feature gates on any page.
    """
    tier = _lic_active_tier()
    loaded = _lic_is_loaded()

    # Pull all known limit keys from FREE_TIER + active licence
    all_limit_keys = set(_FREE_TIER.keys())
    claims: dict = {}
    try:
        import license.validator as _lv  # type: ignore
        if _lv._ACTIVE_LICENSE:
            claims = _lv._ACTIVE_LICENSE
            all_limit_keys |= set(claims.get("limits", {}).keys())
    except Exception:
        pass

    limits = {k: _lic_get_limit(k) for k in sorted(all_limit_keys)}
    features = dict(claims.get("features", {}))
    custom = dict(claims.get("custom", {}))
    jti_raw = str(claims.get("jti", ""))
    jti_prefix = jti_raw[:8] if jti_raw else None

    try:
        console_audit.action_performed(
            tenant_id=rec.tenant_id,
            sid_fingerprint=rec.sid_fingerprint,
            action="license.info_viewed",
            target_kind="license",
            target_id="current",
        )
    except Exception:
        pass

    return LicenseInfo(
        tier=tier,
        loaded=loaded,
        issued_to=None,
        expires_at=claims.get("exp") or None,
        subscription_active_until=claims.get("subscription_active_until") or None,
        jti_prefix=jti_prefix,
        limits=limits,
        features=features,
        custom=custom,
        free_tier=dict(_FREE_TIER),
    )


# ── Key-paste endpoint ─────────────────────────────────────────────────


class LicenseKeyRequest(BaseModel):
    key: str = Field(..., min_length=10, max_length=8192)

    model_config = {"extra": "forbid"}


class LicenseKeyResponse(BaseModel):
    ok: bool
    tier: str
    loaded: bool
    issued_to: str | None = None
    expires_at: int | None = None

    model_config = {"extra": "forbid"}


@router.post("/key", response_model=LicenseKeyResponse)
async def apply_license_key(
    req: LicenseKeyRequest,
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
    csrf: Annotated[str, Depends(require_csrf)],
) -> LicenseKeyResponse:
    """Paste a CORVIN license key via the UI.

    Validates the Ed25519 signature, writes to
    <corvin_home>/global/license.key (mode 0600), and reloads the
    active license in-process.  Owner-only; CSRF required.
    """
    if rec.tier != "owner":
        try:
            console_audit.action_denied(
                tenant_id=rec.tenant_id,
                sid_fingerprint=rec.sid_fingerprint,
                action="license.key_apply",
                target_kind="license",
                target_id="pending",
                reason="insufficient_tier",
            )
        except Exception:
            pass
        raise HTTPException(
            status_code=http_status.HTTP_403_FORBIDDEN,
            detail="Owner access required",
        )

    token = req.key.strip()

    # Validate signature via operator/license/validator
    if not _ADR0092_OK:
        raise HTTPException(
            status_code=http_status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="License validator not available",
        )

    try:
        import license.validator as _lv  # type: ignore
        raw_claims = _lv._verify_ed25519(token)
        # Also run semantic validation (issuer, type, expiry, subscription) to match
        # load_license_from_env() — prevents expired or wrong-issuer tokens being
        # accepted by the UI while silently falling back to Free after in-process reload.
        claims = _lv._validate_claims(raw_claims) if raw_claims is not None else None
    except Exception:
        claims = None

    if claims is None:
        try:
            console_audit.action_failed(
                tenant_id=rec.tenant_id,
                sid_fingerprint=rec.sid_fingerprint,
                action="license.key_apply",
                target_kind="license",
                target_id="pending",
                reason="signature_invalid",
            )
        except Exception:
            pass
        raise HTTPException(
            status_code=http_status.HTTP_400_BAD_REQUEST,
            detail="Invalid license key — signature verification failed",
        )

    # Pre-check revocation (ADR-0102) before writing — a cancelled/revoked token
    # must be rejected here, not merely left to eventually degrade to Free tier
    # on some later reload. Fails open only if both the network AND local cache
    # are unavailable (see _is_token_fp_revoked docstring) — never blocks a
    # legitimate apply over a transient outage.
    try:
        if _lv._is_token_fp_revoked(token):
            try:
                console_audit.action_failed(
                    tenant_id=rec.tenant_id,
                    sid_fingerprint=rec.sid_fingerprint,
                    action="license.key_apply",
                    target_kind="license",
                    target_id=(str(claims.get("jti", ""))[:8] or "unknown"),
                    reason="revoked",
                )
            except Exception:
                pass
            raise HTTPException(
                status_code=http_status.HTTP_400_BAD_REQUEST,
                detail="License key has been revoked",
            )
    except HTTPException:
        raise
    except Exception:
        pass  # unexpected check failure — let load_license_from_env() handle it

    # Pre-check device_fp binding before writing — prevents ok=True/loaded=False confusion
    # when a member-tier key is issued for a different device (ADR-0098).
    try:
        if not _lv._check_device_fp(claims):
            try:
                console_audit.action_failed(
                    tenant_id=rec.tenant_id,
                    sid_fingerprint=rec.sid_fingerprint,
                    action="license.key_apply",
                    target_kind="license",
                    target_id=(str(claims.get("jti", ""))[:8] or "unknown"),
                    reason="device_fp_mismatch",
                )
            except Exception:
                pass
            raise HTTPException(
                status_code=http_status.HTTP_400_BAD_REQUEST,
                detail="License key is device-bound and does not match this machine",
            )
    except HTTPException:
        raise
    except Exception:
        pass  # unexpected check failure — let load_license_from_env() handle it

    # Write to corvin_home/global/license.key — validator._find_token() and
    # _find_token_disk_only() read from this exact path.
    try:
        # Use the canonical resolver (same as every other reader/writer in the
        # process, including the validator's own _CORVIN_HOME_SNAPSHOT) instead
        # of an ad-hoc Path.home()-only computation — a source-checkout run
        # with no CORVIN_HOME env var resolves to <repo>/.corvin here too,
        # rather than diverging to ~/.corvin and silently writing the key
        # where reload_from_disk() will never look for it.
        corvin_home = _forge_paths.corvin_home()
        key_path = corvin_home / "global" / "license.key"
        key_path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
        # tempfile.mkstemp + os.replace, not os.open(key_path, O_CREAT|O_TRUNC):
        # the O_TRUNC form only applies its mode argument when the file is
        # newly CREATED — if key_path already exists (e.g. left permissive by
        # a historical bug, bad umask, or backup restore), O_TRUNC opens and
        # truncates the EXISTING file in place without ever correcting its
        # mode, so a once-permissive license.key stays permissive on every
        # subsequent "Apply Key" forever. mkstemp always creates a fresh file
        # at 0600, and os.replace() atomically swaps it in — this also closes
        # the symlink-follow risk of writing directly to key_path (replace()
        # unlinks/replaces whatever is at that path rather than following it).
        _fd, _tmp = tempfile.mkstemp(dir=key_path.parent, prefix=".license.", suffix=".tmp")
        try:
            with os.fdopen(_fd, "w", encoding="utf-8") as _fh:
                _fh.write(token + "\n")
            os.chmod(_tmp, 0o600)
            os.replace(_tmp, key_path)
        except Exception:
            try:
                os.unlink(_tmp)
            except OSError:
                pass
            raise
    except Exception as exc:
        try:
            console_audit.action_failed(
                tenant_id=rec.tenant_id,
                sid_fingerprint=rec.sid_fingerprint,
                action="license.key_apply",
                target_kind="license",
                target_id="pending",
                reason="write_failed",
            )
        except Exception:
            pass
        raise HTTPException(
            status_code=http_status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to save license key",
        ) from exc

    # Reload in-process so the UI immediately reflects the new tier.
    # load_license_from_env() is idempotent after boot — use reload_from_disk()
    # which bypasses the idempotency guard while keeping frozen path snapshots
    # (ADR-0144 F-04, ADR-0138 C1/C2/C4).
    try:
        _lv.reload_from_disk()
    except Exception:
        pass

    tier = _lic_active_tier()
    loaded = _lic_is_loaded()
    issued_to: str | None = claims.get("issued_to") or None
    expires_at: int | None = claims.get("exp") or None

    # LIC-5: the signature/semantic pre-checks above can pass while enforcement
    # still correctly declines to activate — e.g. a token valid but bound to a
    # DIFFERENT installation (instance_id_bound), or one whose device_fp/binding
    # only fails inside the full load path. Returning ok=True in that state is
    # misleading (the UI shows success while the tier stays free), and the
    # rejected key sits on disk. Reflect the REAL post-reload state: if nothing
    # loaded, roll back the just-written key and report the failure honestly.
    if not loaded:
        try:
            key_path.unlink()
        except OSError:
            pass
        try:
            console_audit.action_failed(
                tenant_id=rec.tenant_id,
                sid_fingerprint=rec.sid_fingerprint,
                action="license.key_apply",
                target_kind="license",
                target_id=(str(claims.get("jti", ""))[:8] or "unknown"),
                reason="not_valid_for_installation",
            )
        except Exception:
            pass
        raise HTTPException(
            status_code=http_status.HTTP_400_BAD_REQUEST,
            detail="key not valid for this installation",
        )

    try:
        console_audit.action_performed(
            tenant_id=rec.tenant_id,
            sid_fingerprint=rec.sid_fingerprint,
            action="license.key_apply",
            target_kind="license",
            target_id=(str(claims.get("jti", ""))[:8] or "unknown"),
        )
    except Exception:
        pass

    return LicenseKeyResponse(
        ok=loaded,
        tier=tier,
        loaded=loaded,
        issued_to=issued_to,
        expires_at=expires_at,
    )
