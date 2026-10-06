"""Layer Forge audit emitters — the tenant's ONE hash chain (ADR-2222 D6).

Every Layer Forge decision lands in ``core.paths.tenant_audit_chain(tenant_id)``
via ``forge.security_events.audit_write_or_die``. Audit-first and fail-closed: a state
change (create, status transition) is written only after its record committed;
if the chain write fails, ``LayerForgeAuditError`` propagates and nothing changes.

Metadata only — ids, versions, counts, statuses, exception class names. Never
manifest bodies, gate output, file contents or exception messages.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Callable

# Mirrored in corvin_operator/forge/forge/security_events.py (EVENT_SEVERITY + _EVENT_ALLOWLIST).
ALLOWED_FIELDS: dict[str, frozenset[str]] = {
    "layer_forge.definition_proposed": frozenset({
        "entry_id", "version", "target_layers", "gate_count", "rule_count",
        "gates_skipped", "actor", "tenant_id",
    }),
    "layer_forge.definition_rejected": frozenset({
        "entry_id", "version", "phase", "error_class", "failing_gates", "actor", "tenant_id",
    }),
    "layer_forge.quality_gate_evaluated": frozenset({
        "entry_id", "version", "gate_id", "status", "tenant_id",
    }),
    "layer_forge.enforcement_evaluated": frozenset({
        "entry_id", "version", "rule_id", "status", "tenant_id",
    }),
    "layer_forge.definition_transitioned": frozenset({
        "entry_id", "version", "from_status", "to_status", "actor", "tenant_id",
    }),
    "layer_forge.review_evaluated": frozenset({
        "entry_id", "version", "verdict", "flags", "tenant_id", "prompt_version",
    }),
    "layer_forge.review_override_applied": frozenset({
        "entry_id", "version", "override_reason", "overridden_flags", "actor", "tenant_id",
    }),
    "layer_forge.definition_outcome_feedback": frozenset({
        "entry_id", "version", "outcome", "phase", "actor", "tenant_id",
    }),
    "layer_forge.gate_threshold_suggested": frozenset({
        "gate_id", "old_threshold", "new_threshold", "reason", "signal",
        "override_success_rate", "total_overrides", "actor", "tenant_id",
    }),
    "layer_forge.gate_threshold_applied": frozenset({
        "gate_id", "old_threshold", "new_threshold", "reason", "actor", "tenant_id",
    }),
    "layer_forge.canary_rollout_assigned": frozenset({
        "entry_id", "version", "prompt_version", "rollout_percentage", "tenant_id",
    }),
    "layer_forge.canary_rollback": frozenset({
        "canary_version", "parent_version", "reason", "canary_success_rate",
        "parent_success_rate", "tenant_id",
    }),
}

SEVERITY: dict[str, str] = {
    "layer_forge.definition_proposed": "INFO",
    "layer_forge.definition_rejected": "WARNING",
    "layer_forge.quality_gate_evaluated": "INFO",
    "layer_forge.enforcement_evaluated": "INFO",
    "layer_forge.definition_transitioned": "INFO",
    "layer_forge.review_evaluated": "INFO",
    "layer_forge.review_override_applied": "WARNING",
    "layer_forge.definition_outcome_feedback": "INFO",
    "layer_forge.gate_threshold_suggested": "INFO",
    "layer_forge.gate_threshold_applied": "INFO",
    "layer_forge.canary_rollout_assigned": "INFO",
    "layer_forge.canary_rollback": "WARNING",
}


class LayerForgeAuditError(RuntimeError):
    """The chain write did not commit — the operation must not proceed."""


def _core_write_event() -> Callable[..., Any]:
    """``forge.security_events.audit_write_or_die`` (disk-headroom check, raises on failure)."""
    try:
        from forge.security_events import audit_write_or_die as _we  # type: ignore[import]
        return _we
    except ImportError:
        pass
    forge_root = Path(__file__).resolve().parents[3] / "corvin_operator" / "forge"
    if str(forge_root) not in sys.path:
        sys.path.insert(0, str(forge_root))
    cached = sys.modules.get("forge")
    if cached is not None and getattr(cached, "__file__", None) is None:
        for name in [m for m in sys.modules if m == "forge" or m.startswith("forge.")]:
            sys.modules.pop(name, None)
    from forge.security_events import audit_write_or_die as _we  # type: ignore[import]
    return _we


def emit(event: str, *, tenant_id: str, **details: Any) -> str:
    """Write one record; return its hash. Raises ``LayerForgeAuditError`` on any failure."""
    allowed = ALLOWED_FIELDS.get(event)
    if allowed is None:
        raise LayerForgeAuditError(f"unregistered layer_forge event: {event}")
    payload = {k: v for k, v in details.items() if v is not None}
    payload["tenant_id"] = tenant_id
    extras = set(payload) - allowed
    if extras:
        raise LayerForgeAuditError(f"{event}: fields not allow-listed: {sorted(extras)}")
    try:
        from core.paths import tenant_audit_chain

        chain = tenant_audit_chain(tenant_id)
        chain.parent.mkdir(parents=True, exist_ok=True)
        record = _core_write_event()(chain, event, details=payload, severity=SEVERITY[event])
    except Exception as exc:  # noqa: BLE001 — any failure means "not recorded"
        raise LayerForgeAuditError(f"{event}: audit write failed ({type(exc).__name__})") from exc
    digest = record.get("hash") if isinstance(record, dict) else None
    if not digest:
        raise LayerForgeAuditError(f"{event}: audit write returned no chained record")
    return digest
