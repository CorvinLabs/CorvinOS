"""FastAPI router for the license-gate plugin.

Mounts under ``/v1/license/*`` on the gateway ASGI app. Routes:

  GET  /v1/license/healthz       — liveness probe (unauth)
  GET  /v1/license/version       — plugin version (unauth)
  GET  /v1/license/status        — current tier + grace status (unauth;
                                    deliberately public so the UI can
                                    show "Upgrade to Enterprise" hints
                                    without a session cookie)

The plugin emits audit events on state transitions only — a GET
that just READS state never writes to the chain. License activation
+ revocation are operator-side actions handled by ``cli.py``.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Header, HTTPException, status
from fastapi.responses import PlainTextResponse

from . import __version__
from . import audit as _audit
from . import grace as _grace
from . import portal as _portal
from . import sync as _sync
from . import trial as _trial
from . import verifier as _verifier


router = APIRouter()


@router.get("/version")
def version() -> dict[str, str]:
    return {"version": __version__, "plugin": "corvin-license"}


@router.get("/healthz")
def healthz() -> dict[str, Any]:
    """Liveness probe — does the plugin load and parse cleanly?
    Does NOT verify the license; that is /status's job."""
    return {"ok": True, "version": __version__, "plugin": "corvin-license"}


# ── In-memory cache ───────────────────────────────────────────────────
#
# /status is unauthenticated and could be polled aggressively (a
# license-gate hint in the UI). Cache the result for 60 s so a poll
# loop doesn't hammer disk + crypto.

_STATUS_CACHE: dict[str, Any] = {"ts": 0, "payload": None}
_CACHE_TTL_S = 60


def _read_raw_token() -> str | None:
    """Read the raw JWT string from disk without verifying it."""
    path = _verifier.license_file_path()
    if not path.exists():
        return None
    try:
        return path.read_text(encoding="utf-8").strip() or None
    except OSError:
        return None


def _compute_status() -> dict[str, Any]:
    """Read license.jwt + grace state + sync cache, project as JSON.

    No exceptions surface — every branch lands as a documented state.
    """
    # Read raw token first so we can pass it to background sync.
    raw_token = _read_raw_token()

    # Try to load + verify the license.
    try:
        lic = _verifier.load_license_from_disk()

        # Fire background sync (non-blocking — returns immediately).
        if raw_token:
            try:
                _sync.maybe_sync_in_background(
                    raw_jwt=raw_token,
                    iat=lic.issued_at,
                    exp=lic.valid_until,
                    trial_id=lic.trial_id,
                    trial_type=lic.trial_type,
                )
            except Exception:
                pass

        # Check sync cache for revocation — takes precedence over local JWT.
        sync_cache = _sync.load_sync_cache()
        if sync_cache.is_revoked:
            return {
                "tier": "revoked",
                "mode": "license-revoked",
                "feature_flags": [],
                "expired": True,
                "grace": _grace.assess(valid_until=None).to_dict(),
            }

        # Trial token: check expiry via activation anchor.
        if lic.is_trial and lic.trial_id and lic.trial_expires_at:
            activation = _trial.record_activation(lic.trial_id)
            # Propagate server-anchored activation time if available.
            if sync_cache.trial_activated_at:
                try:
                    _trial.update_server_anchor(lic.trial_id, sync_cache.trial_activated_at)
                    activation = _trial.load_trial_activation(lic.trial_id) or activation
                except Exception:
                    pass
            active, reason = _trial.is_trial_active(
                trial_expires_at=lic.trial_expires_at,
                issued_at=lic.issued_at,
                trial_id=lic.trial_id,
                activation=activation,
            )
            if not active:
                return {
                    "tier": "trial-expired",
                    "mode": f"trial-expired:{reason}",
                    "feature_flags": [],
                    "expired": True,
                    "grace": _grace.assess(valid_until=None).to_dict(),
                    "trial_type": lic.trial_type,
                }
            # Active trial — build payload, mark mode.
            payload = lic.to_public_dict()
            payload["mode"] = f"trial-active:{lic.trial_type}"
            days_left = max(0, (lic.trial_expires_at - int(__import__("time").time())) // 86400)
            payload["trial_days_remaining"] = days_left
            payload["grace"] = _grace.assess(valid_until=lic.valid_until).to_dict()
            payload["sync_fresh"] = sync_cache.is_fresh()
            return payload

        # Active license observed — refresh grace anchor.
        try:
            _grace.remember_valid_license(
                valid_until=lic.valid_until,
                customer_fingerprint=_verifier.fingerprint_customer_id(
                    lic.customer_id
                ),
            )
        except Exception:
            # Best-effort: a failing grace write must not fail the read.
            pass
        status = _grace.assess(valid_until=lic.valid_until)
        payload = lic.to_public_dict()
        payload["grace"] = status.to_dict()
        payload["mode"] = "licensed-active" if not status.in_grace else "licensed-grace"
        payload["sync_fresh"] = sync_cache.is_fresh()
        return payload

    except _verifier.LicenseFileMissing:
        # Free tier — no license installed. Documented baseline.
        status = _grace.assess(valid_until=None)
        return {
            "tier": "free",
            "customer_id_fingerprint": None,
            "employee_count_max": None,
            "seats": None,
            "valid_until": None,
            "issued_at": None,
            "feature_flags": [],
            "expired": False,
            "grace": status.to_dict(),
            "mode": "free-tier" if status.state == "no-license" else f"free-tier-{status.state}",
        }

    except _verifier.LicenseExpired as exc:
        # Token verifies but exp has passed — check grace.
        return _expired_payload(
            reason="exp-passed",
            expired_at=exc.expired_at,
            customer_fp=exc.customer_fingerprint,
            tier=exc.tier,
        )

    except _verifier.LicenseFileMalformed as exc:
        _safe_audit_violated("file-malformed")
        import logging as _logging
        _logging.getLogger(__name__).error("license file malformed: %s", exc)
        return {
            "tier": "unknown",
            "mode": "license-malformed",
            "error": "license-file-malformed",
            "grace": _grace.assess(valid_until=None).to_dict(),
            "feature_flags": [],
        }

    except _verifier.LicenseSignatureError as exc:
        _safe_audit_violated("signature-invalid")
        import logging as _logging
        _logging.getLogger(__name__).error("license signature invalid: %s", exc)
        return {
            "tier": "unknown",
            "mode": "license-signature-invalid",
            "error": "signature-invalid",
            "grace": _grace.assess(valid_until=None).to_dict(),
            "feature_flags": [],
        }

    except _verifier.LicenseClaimError as exc:
        _safe_audit_violated("claim-invalid")
        import logging as _logging
        _logging.getLogger(__name__).error("license claim invalid: %s", exc)
        return {
            "tier": "unknown",
            "mode": "license-claim-invalid",
            "error": "claim-invalid",
            "grace": _grace.assess(valid_until=None).to_dict(),
            "feature_flags": [],
        }

    except FileNotFoundError:
        # Pubkey file missing — operator hasn't installed the pinned
        # key. Plugin can't validate anything; degrade to free.
        return {
            "tier": "free",
            "mode": "no-pubkey",
            "feature_flags": [],
            "grace": _grace.assess(valid_until=None).to_dict(),
        }

    except RuntimeError as exc:
        # pubkey.pem sha256 mismatch (ADR-0093 M1.2 integrity check).
        # File has been modified since the build — treat as tampered.
        _safe_audit_violated("pubkey-integrity-failed")
        import logging as _logging
        _logging.getLogger(__name__).critical("pubkey integrity check failed: %s", exc)
        return {
            "tier": "unknown",
            "mode": "pubkey-integrity-failed",
            "error": "pubkey-integrity-failed",
            "feature_flags": [],
            "grace": _grace.assess(valid_until=None).to_dict(),
        }


def _expired_payload(
    *,
    reason: str,
    expired_at: int | None = None,
    customer_fp: str | None = None,
    tier: str | None = None,
) -> dict[str, Any]:
    """Handle the expired path: assess grace, emit one-shot audit."""
    status = _grace.assess(valid_until=expired_at)
    if status.state == "in-grace":
        # Emit grace-started exactly once on the first observation.
        was_first = _grace.mark_observed_expired(
            customer_fingerprint=customer_fp,
        )
        if was_first:
            try:
                _audit.license_grace_started(
                    tier=tier or "unknown",
                    customer_fp=customer_fp or "unknown",
                    grace_ends_at=status.grace_ends_at or 0,
                )
            except Exception:
                pass
    return {
        "tier": "expired",
        "mode": "license-expired-in-grace" if status.in_grace else "license-expired",
        "feature_flags": [],
        "expired": True,
        "grace": status.to_dict(),
        "reason": reason,
    }


def _safe_audit_violated(reason: str) -> None:
    try:
        _audit.license_violated(reason=reason)
    except Exception:
        pass


@router.get("/status")
def get_status() -> dict[str, Any]:
    """Public license-status endpoint.

    NEVER returns the raw license body; only the projected public
    dict (`License.to_public_dict()`) plus the grace assessment.
    """
    import time
    now = int(time.time())
    if _STATUS_CACHE["payload"] is not None and now - _STATUS_CACHE["ts"] < _CACHE_TTL_S:
        return _STATUS_CACHE["payload"]
    payload = _compute_status()
    _STATUS_CACHE["payload"] = payload
    _STATUS_CACHE["ts"] = now
    return payload


def _flush_cache() -> None:
    """Test-only cache flush. Production code should rely on TTL."""
    _STATUS_CACHE["payload"] = None
    _STATUS_CACHE["ts"] = 0


# ── ADR-0019 — customer-self-service download portal ───────────────────
#
# GET /v1/license/me returns the installed license.jwt to a caller
# who presents the configured CORVIN_LICENSE_PORTAL_BEARER. This is
# the Phase 1 single-bearer variant; Phase 2+ multi-customer flow
# lives in the corvin-cloud repo with Stripe-customer bearer tokens.

def _extract_bearer(authorization: str | None) -> str | None:
    if not authorization or not authorization.lower().startswith("bearer "):
        return None
    return authorization[7:].strip() or None


@router.get(
    "/me",
    response_class=PlainTextResponse,
    responses={
        200: {"content": {"text/plain": {}}, "description": "Signed license JWT"},
        401: {"description": "Missing or invalid bearer"},
        403: {"description": "Portal disabled (no bearer configured)"},
        404: {"description": "No license installed"},
    },
)
def get_my_license(
    authorization: str | None = Header(default=None),
) -> PlainTextResponse:
    """Return the installed license.jwt verbatim to an authenticated caller.

    Auth: ``Authorization: Bearer <CORVIN_LICENSE_PORTAL_BEARER>``.
    Without the env var configured the route refuses every request
    with 403 — the operator hasn't opted in to the portal.
    """
    bearer = _extract_bearer(authorization)

    # Portal must be enabled (bearer configured server-side).
    if not _portal.portal_enabled():
        try:
            _audit.license_portal_denied(reason="portal-disabled")
        except Exception:
            pass
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"reason": "portal-disabled"},
        )

    if not bearer:
        try:
            _audit.license_portal_denied(reason="missing-bearer")
        except Exception:
            pass
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"reason": "missing-bearer"},
            headers={"WWW-Authenticate": 'Bearer realm="corvin-license-portal"'},
        )

    if not _portal.check_bearer(bearer):
        try:
            _audit.license_portal_denied(
                reason="invalid-bearer",
                bearer_fp=_portal.bearer_fingerprint(bearer),
            )
        except Exception:
            pass
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"reason": "invalid-bearer"},
            headers={"WWW-Authenticate": 'Bearer realm="corvin-license-portal"'},
        )

    # Bearer matched — try to read the installed license.
    license_path = _verifier.license_file_path()
    if not license_path.exists():
        try:
            _audit.license_portal_denied(
                reason="no-license",
                bearer_fp=_portal.bearer_fingerprint(bearer),
            )
        except Exception:
            pass
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"reason": "no-license"},
        )

    # Verify the license so we can carry tier + customer_fp into audit.
    try:
        lic = _verifier.load_license_from_disk()
        token_bytes = _portal.read_installed_license_bytes(license_path)
        try:
            _audit.license_portal_served(
                tier=lic.tier,
                customer_fp=_verifier.fingerprint_customer_id(lic.customer_id),
                bearer_fp=_portal.bearer_fingerprint(bearer),
            )
        except Exception:
            pass
        return PlainTextResponse(
            content=token_bytes,
            media_type="text/plain",
            headers={
                "Content-Disposition": 'attachment; filename="license.jwt"',
                "Cache-Control": "no-store, private",
            },
        )
    except _verifier.LicenseError:
        # Installed license fails verification — don't expose the
        # broken token to the customer. The /status endpoint surfaces
        # the diagnostic separately for the operator.
        try:
            _audit.license_portal_denied(
                reason="no-license",
                bearer_fp=_portal.bearer_fingerprint(bearer),
            )
        except Exception:
            pass
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"reason": "no-license"},
        )


# ── Helper for downstream gating ──────────────────────────────────────

_BLOCKED_MODES = frozenset({
    "free-tier", "free-tier-no-license",
    "license-expired", "license-malformed",
    "license-signature-invalid", "license-claim-invalid",
    "no-pubkey", "license-revoked", "pubkey-integrity-failed",
    # trial-expired:* modes are prefix-checked below
})


def has_feature(flag: str) -> bool:
    """Used by Enterprise-plugin mounts: is `flag` enabled?

    Free tier returns False for every flag. Active licenses (including
    active trials) respect their `feature_flags` list. In-grace licenses
    behave as active. Expired, revoked, or expired-trial → False.
    """
    status = get_status()
    mode = status.get("mode", "")
    if mode in _BLOCKED_MODES or mode.startswith("trial-expired"):
        return False
    return flag in status.get("feature_flags", [])


__all__ = ["router", "has_feature"]
