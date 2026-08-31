"""License-gate audit emitters — metadata only, never the token.

Five event types registered in ``forge.security_events.EVENT_SEVERITY``:

  license.activated     INFO     - tier, customer_fp, valid_until,
                                   employee_count_max, seats
  license.expired       WARNING  - customer_fp, expired_at
  license.grace_started WARNING  - customer_fp, grace_ends_at
  license.violated      WARNING  - reason (curated set)
  license.revoked       WARNING  - customer_fp, reason

Per-event allow-list enforced at emit boundary. The actual license
JWT, the customer's full UUID, and any signing material NEVER appear
in the chain. Mirror of Layer 23 / 24 / 25 metadata-only precedent.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

_THIS = Path(__file__).resolve().parent
_REPO = _THIS.parents[2]
_FORGE_PATH = _REPO / "operator" / "forge"
if str(_FORGE_PATH) not in sys.path:
    sys.path.insert(0, str(_FORGE_PATH))

from forge import paths as _forge_paths  # noqa: E402
from forge import security_events as _security_events  # noqa: E402


class LicenseAuditFieldNotAllowed(Exception):
    """A detail key is off the per-event allow-list."""


_FORBIDDEN_FIELDS = frozenset({
    "token", "jwt", "license_jwt", "raw_token",
    "customer_id",         # FULL customer_id forbidden; fingerprint only
    "signing_key", "privkey", "private_key",
    "pubkey_body",
    "secret",
})

_ALLOWED_FIELDS: dict[str, frozenset[str]] = {
    "license.activated": frozenset({
        "tier", "customer_fp", "valid_until", "issued_at",
        "employee_count_max", "seats", "feature_flags",
    }),
    "license.expired": frozenset({
        "tier", "customer_fp", "expired_at",
    }),
    "license.grace_started": frozenset({
        "tier", "customer_fp", "grace_ends_at",
    }),
    "license.violated": frozenset({
        "reason", "tier_attempted", "customer_fp",
    }),
    "license.revoked": frozenset({
        "customer_fp", "reason",
    }),
    # ADR-0019 — customer-self-service portal download.
    # Fires on every authenticated /v1/license/me read. Bearer-token
    # fingerprint is the only auth-side info that lands in the chain.
    "license.portal_served": frozenset({
        "tier", "customer_fp", "bearer_fp",
    }),
    "license.portal_denied": frozenset({
        "reason", "bearer_fp",
    }),
    # ADR-0093 M1.4 — sync-disable anomaly signal.
    # Fires on every sync call when CORVIN_LICENSE_SYNC_DISABLED=1 is set.
    # Operators aggregating audit logs see unexplained sync-disable events
    # as a red flag. source is always "env-var".
    "license.sync_disabled": frozenset({
        "source",
    }),
}

_VALID_VIOLATED_REASONS = frozenset({
    "signature-invalid",
    "algorithm-not-allowed",
    "claim-missing",
    "claim-invalid",
    "issuer-invalid",
    "tier-free-not-signable",
    "file-malformed",
    "grace-exhausted",
    "file-mode-permissive",
})


def _audit_path() -> Path:
    """Chain path. License events land in the _default tenant chain —
    the license is per-installation, not per-tenant."""
    return _forge_paths.tenant_global_dir("_default") / "forge" / "audit.jsonl"


def _validate_details(event_type: str, details: dict[str, Any]) -> None:
    if event_type not in _ALLOWED_FIELDS:
        raise LicenseAuditFieldNotAllowed(
            f"unknown-event-type: {event_type!r}"
        )
    allowed = _ALLOWED_FIELDS[event_type]
    for k in details.keys():
        if k in _FORBIDDEN_FIELDS:
            raise LicenseAuditFieldNotAllowed(
                f"forbidden-field: {k!r} in {event_type}"
            )
        if k not in allowed:
            raise LicenseAuditFieldNotAllowed(
                f"off-allowlist: {k!r} not allowed in {event_type} "
                f"(allowed: {sorted(allowed)})"
            )


def _emit(event_type: str, **details: Any) -> None:
    _validate_details(event_type, details)
    _security_events.write_event(
        event_type=event_type,
        details=details,
        path=_audit_path(),
    )


# ── Public event emitters ─────────────────────────────────────────────

def license_activated(
    *,
    tier: str,
    customer_fp: str,
    valid_until: int,
    issued_at: int,
    employee_count_max: int,
    seats: int,
    feature_flags: list[str] | tuple[str, ...],
) -> None:
    """Fired when an operator installs (or the gateway boot validates)
    a fresh license that is currently active."""
    _emit(
        "license.activated",
        tier=tier,
        customer_fp=customer_fp,
        valid_until=valid_until,
        issued_at=issued_at,
        employee_count_max=employee_count_max,
        seats=seats,
        feature_flags=list(feature_flags),
    )


def license_expired(*, tier: str, customer_fp: str, expired_at: int) -> None:
    """Fired the moment we first observe the token as past its exp claim."""
    _emit(
        "license.expired",
        tier=tier,
        customer_fp=customer_fp,
        expired_at=expired_at,
    )


def license_grace_started(
    *, tier: str, customer_fp: str, grace_ends_at: int,
) -> None:
    """Fired exactly once when grace mode begins. Subsequent reads
    don't re-fire this event."""
    _emit(
        "license.grace_started",
        tier=tier,
        customer_fp=customer_fp,
        grace_ends_at=grace_ends_at,
    )


def license_violated(
    *,
    reason: str,
    tier_attempted: str | None = None,
    customer_fp: str | None = None,
) -> None:
    """Fired on signature failure, malformed file, grace-exhausted access
    attempt to a gated route, etc."""
    if reason not in _VALID_VIOLATED_REASONS:
        raise LicenseAuditFieldNotAllowed(
            f"reason-not-allowed: {reason!r} "
            f"(allowed: {sorted(_VALID_VIOLATED_REASONS)})"
        )
    payload: dict[str, Any] = {"reason": reason}
    if tier_attempted is not None:
        payload["tier_attempted"] = tier_attempted
    if customer_fp is not None:
        payload["customer_fp"] = customer_fp
    _emit("license.violated", **payload)


_VALID_REVOKE_REASONS = frozenset({
    "operator-revoke",   # CLI default — explicit operator revoke
    "renewal",           # token replaced by a renewed/re-issued license
    "superseded",        # replaced by a different (e.g. upgraded) license
    "compromised",       # key/token believed leaked
    "expired-manual",    # operator pulls an expired token early
})


def license_revoked(*, customer_fp: str, reason: str) -> None:
    """Fired by the CLI revoke command. Operator-only action.

    The ``reason`` is a controlled reason code, NOT operator free-text — only
    codes on the allow-list reach the tamper-evident L16 chain. This mirrors
    ``license_violated()`` and keeps the audit chain metadata-only (no
    free-form strings, no PII) regardless of what an operator types at the CLI.
    """
    if reason not in _VALID_REVOKE_REASONS:
        raise LicenseAuditFieldNotAllowed(
            f"reason-not-allowed: {reason!r} "
            f"(allowed: {sorted(_VALID_REVOKE_REASONS)})"
        )
    _emit("license.revoked", customer_fp=customer_fp, reason=reason)


_VALID_PORTAL_DENIED_REASONS = frozenset({
    "missing-bearer",
    "invalid-bearer",
    "no-license",
    "portal-disabled",
})


def license_portal_served(
    *, tier: str, customer_fp: str, bearer_fp: str,
) -> None:
    """Fired when a customer downloads their license via /v1/license/me."""
    _emit(
        "license.portal_served",
        tier=tier,
        customer_fp=customer_fp,
        bearer_fp=bearer_fp,
    )


def license_portal_denied(*, reason: str, bearer_fp: str = "") -> None:
    """Fired when /v1/license/me refuses a request (bad bearer, no license)."""
    if reason not in _VALID_PORTAL_DENIED_REASONS:
        raise LicenseAuditFieldNotAllowed(
            f"reason-not-allowed: {reason!r} "
            f"(allowed: {sorted(_VALID_PORTAL_DENIED_REASONS)})"
        )
    payload: dict[str, Any] = {"reason": reason}
    if bearer_fp:
        payload["bearer_fp"] = bearer_fp
    _emit("license.portal_denied", **payload)


def license_sync_disabled() -> None:
    """ADR-0093 M1.4 — fired every time a sync call is skipped because
    CORVIN_LICENSE_SYNC_DISABLED=1 is set in the environment.

    Repeated events are intentional: each sync skip is an anomaly signal.
    Operators who aggregate audit logs will see the pattern and investigate.
    """
    _emit("license.sync_disabled", source="env-var")


__all__ = [
    "LicenseAuditFieldNotAllowed",
    "license_activated",
    "license_expired",
    "license_grace_started",
    "license_violated",
    "license_revoked",
    "license_portal_served",
    "license_portal_denied",
    "license_sync_disabled",
]
