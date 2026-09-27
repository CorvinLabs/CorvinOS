"""Remediation audit — ONE writer, the tenant's core hash chain.

Every remediation record goes through ``forge.security_events.write_event``
on ``forge.paths.tenant_audit_chain(tenant)`` — the one hash-chained writer,
on the chain of the tenant the record is ABOUT (never the process's context
tenant: a console serves several). The writer module is the one the core
audit bridge loaded (``core.learning.event_persistence._resolve_core_audit``),
so there is exactly one ``forge`` in the process. There is no second chain, no
in-memory "audit trail" and no fall-through: :func:`remediation_audit` RAISES
when the record was not written, so a caller that must be audit-first (an
approval decision) refuses instead of acting unrecorded.

Records are CONTENT-FREE: ids, enums, codes and booleans. An operator's free-
text decision reason is never written — only ``has_reason``.

The field allowlists below are folded into the core writer at first use (the
same runtime registration ``core.learning`` performs); without them the
writer's default-deny floor would drop every remediation-specific key.
"""
from __future__ import annotations

from typing import Any

_FIELDS = frozenset({
    "request_id", "drift_id", "drift_type", "instance_id", "state",
    "decided_by", "decision", "has_reason", "remediation_type", "status",
    "status_detail", "error_code", "plan_id", "event", "tenant_id", "audit_ref",
})

#: Remediation event types → allowlisted detail keys. EVENT_SEVERITY defaults
#: (for the owner of ``security_events.py``): requested/escalated/expired =
#: WARNING, decided = INFO, executed/lifecycle = INFO.
REMEDIATION_EVENT_ALLOWLISTS: dict[str, frozenset[str]] = {
    name: _FIELDS
    for name in (
        "remediation.approval_requested",
        "remediation.approval_decided",
        "remediation.approval_expired",
        "remediation.approval_escalated",
        "remediation.remediation_cancelled",
        "remediation.executed",
        "remediation.lifecycle",
    )
}

_registered_for: set[int] = set()


def _register(security_events: Any) -> None:
    register = getattr(security_events, "register_event_allowlist", None)
    if register is None or id(security_events) in _registered_for:
        return
    for event_type, fields in REMEDIATION_EVENT_ALLOWLISTS.items():
        register(event_type, fields)
    _registered_for.add(id(security_events))


def remediation_audit(event_type: str, *, tenant_id: str, **details: Any) -> str:
    """Write one remediation record to ``tenant_id``'s chain; return its hash.

    Raises:
        RuntimeError: writer unavailable or the record was not written.
        ValueError: unknown event type or an invalid tenant id.
    """
    if event_type not in REMEDIATION_EVENT_ALLOWLISTS:
        raise ValueError(f"unregistered remediation audit event: {event_type!r}")
    import importlib

    from core.learning.event_persistence import _resolve_core_audit
    from core.tenants.validation import validate_tenant_id

    validate_tenant_id(tenant_id)
    se = _resolve_core_audit()._se
    _register(se)
    paths = importlib.import_module(se.__name__.rsplit(".", 1)[0] + ".paths")
    body = {k: v for k, v in details.items() if v is not None}
    body["tenant_id"] = tenant_id
    chain = paths.tenant_audit_chain(tenant_id)
    chain.parent.mkdir(parents=True, exist_ok=True)
    try:
        record = se.write_event(chain, event_type, details=body)
    except Exception as exc:  # noqa: BLE001 — e.g. the writer refuses a tenant
        # that is not the process tenant; either way nothing was recorded.
        raise RuntimeError(f"remediation audit write refused for {event_type}: {type(exc).__name__}") from exc
    ref = (record or {}).get("hash") if isinstance(record, dict) else None
    if not ref:
        raise RuntimeError(f"remediation audit write did not commit for {event_type}")
    return ref
