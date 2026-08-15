"""Compliance-report audit emitters — metadata only.

Two event types:

  compliance.report_generated  INFO    report_type, tenant_id,
                                       period_start_ts, period_end_ts,
                                       anchor_hash, total_events
  compliance.report_failed     WARNING reason, report_type, tenant_id

Per-event allow-list enforced at the boundary. Output paths are
relative-to-tenant by convention; full paths are NOT logged
(operator-side artefact placement is not the auditor's concern).
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

_THIS = Path(__file__).resolve().parent
_REPO = _THIS.parents[2]
_FORGE = _REPO / "operator" / "forge"
if str(_FORGE) not in sys.path:
    sys.path.insert(0, str(_FORGE))

from forge import paths as _forge_paths  # noqa: E402
from forge import security_events as _security_events  # noqa: E402


class ComplianceAuditFieldNotAllowed(Exception):
    """A detail key is off the per-event allow-list."""


_FORBIDDEN_FIELDS = frozenset({
    # Never write report body / chain hashes other than the documented anchor.
    "report_body", "pdf_bytes", "raw_events",
    "customer_id", "token",  # mirror license audit rules
})

_ALLOWED_FIELDS: dict[str, frozenset[str]] = {
    "compliance.report_generated": frozenset({
        "report_type", "tenant_id",
        "period_start_ts", "period_end_ts",
        "total_events", "chain_intact",
        "anchor_hash", "page_count_estimate",
    }),
    "compliance.report_failed": frozenset({
        "report_type", "tenant_id", "reason",
        "period_start_ts", "period_end_ts",
    }),
}

_VALID_REPORT_TYPES = frozenset({
    "ai_act_art_50",
    "gdpr_art_30_ropa",
    "audit_chain_attestation",
})

_VALID_FAILED_REASONS = frozenset({
    "chain-missing",
    "chain-malformed",
    "render-error",
    "permission-denied",
    "unknown-report-type",
})


def _audit_path(tenant_id: str) -> Path:
    return _forge_paths.tenant_global_dir(tenant_id) / "forge" / "audit.jsonl"


def _validate_details(event_type: str, details: dict[str, Any]) -> None:
    if event_type not in _ALLOWED_FIELDS:
        raise ComplianceAuditFieldNotAllowed(
            f"unknown-event-type: {event_type!r}"
        )
    allowed = _ALLOWED_FIELDS[event_type]
    for k in details:
        if k in _FORBIDDEN_FIELDS:
            raise ComplianceAuditFieldNotAllowed(
                f"forbidden-field: {k!r} in {event_type}"
            )
        if k not in allowed:
            raise ComplianceAuditFieldNotAllowed(
                f"off-allowlist: {k!r} not allowed in {event_type} "
                f"(allowed: {sorted(allowed)})"
            )


def report_generated(
    *,
    report_type: str,
    tenant_id: str,
    period_start_ts: int,
    period_end_ts: int,
    total_events: int,
    chain_intact: bool,
    anchor_hash: str | None,
    page_count_estimate: int | None = None,
) -> None:
    if report_type not in _VALID_REPORT_TYPES:
        raise ComplianceAuditFieldNotAllowed(
            f"unknown-report-type: {report_type!r}"
        )
    payload: dict[str, Any] = {
        "report_type": report_type,
        "tenant_id": tenant_id,
        "period_start_ts": period_start_ts,
        "period_end_ts": period_end_ts,
        "total_events": total_events,
        "chain_intact": chain_intact,
        "anchor_hash": anchor_hash or "",
    }
    if page_count_estimate is not None:
        payload["page_count_estimate"] = page_count_estimate
    _validate_details("compliance.report_generated", payload)
    _security_events.write_event(
        event_type="compliance.report_generated",
        details=payload,
        path=_audit_path(tenant_id),
    )


def report_failed(
    *,
    report_type: str,
    tenant_id: str,
    reason: str,
    period_start_ts: int,
    period_end_ts: int,
) -> None:
    if reason not in _VALID_FAILED_REASONS:
        raise ComplianceAuditFieldNotAllowed(
            f"reason-not-allowed: {reason!r}"
        )
    payload = {
        "report_type": report_type,
        "tenant_id": tenant_id,
        "reason": reason,
        "period_start_ts": period_start_ts,
        "period_end_ts": period_end_ts,
    }
    _validate_details("compliance.report_failed", payload)
    _security_events.write_event(
        event_type="compliance.report_failed",
        details=payload,
        path=_audit_path(tenant_id),
    )


def audit_log(
    *,
    action: str,
    reason: str = "",
    source: str = "",
    tenant_id: str = "unknown",
) -> None:
    """Log input validation and security events to audit trail.

    Generic audit log function for Phase 10 validators and other subsystems.
    Fail-closed: on error, silently skip (never propagate audit failures).

    Args:
        action: Action type (e.g., 'input_validation_failed', 'pii_detected')
        reason: Reason code (e.g., 'invalid_type', 'missing_tenant_id')
        source: Source identifier (e.g., 'route_json_name', 'async_task_foo')
        tenant_id: Tenant context (defaults to 'unknown' for system events)
    """
    try:
        payload: dict[str, Any] = {
            "action": action,
            "reason": reason,
            "source": source,
            "tenant_id": tenant_id,
        }
        # Write to audit trail via security events
        _security_events.write_event(
            event_type="validation.audit_log",
            details=payload,
            path=_audit_path(tenant_id),
        )
    except Exception:
        # Fail-closed: audit failures never propagate to the business logic
        pass


__all__ = [
    "ComplianceAuditFieldNotAllowed",
    "report_generated",
    "report_failed",
    "audit_log",
]
