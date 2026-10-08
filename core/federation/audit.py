"""Federation audit emitters — CONCEPT-0097 Phase 1 (local agent registry).

Mirrors ``core.forge_bundle.audit`` exactly: every record goes through
``core.paths.tenant_audit_chain(tenant_id)`` via
``forge.security_events.audit_write_or_die``. Metadata only — ids,
capabilities, counts. Never prompts, model outputs, or free-text fields
beyond what's declared in ALLOWED_FIELDS below.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Callable

# Mirrored in corvin_operator/forge/forge/security_events.py (EVENT_SEVERITY +
# _EVENT_ALLOWLIST); keep both in sync by hand until a lint pins them together
# (see test_federation_audit_allowlist_matches_central_registry).
ALLOWED_FIELDS: dict[str, frozenset[str]] = {
    "federation.local_agent_registered": frozenset({
        "agent_id", "engine_type", "capabilities", "model", "federable", "tenant_id",
    }),
    "federation.local_agent_deregistered": frozenset({
        "agent_id", "tenant_id",
    }),
    "federation.local_agent_registration_rejected": frozenset({
        "reason", "tenant_id",
    }),
    # Origin side of a cross-peer exchange (ADR-2232). The receiver side
    # (catalog_served, task_received, ...) is written by the A2A receiver's
    # own writer — see corvin_operator/bridges/shared/a2a_federation.py.
    "federation.catalog_fetched": frozenset({
        "endpoint_id", "peer_instance_id", "agent_count", "status", "tenant_id",
    }),
    "federation.task_delegated": frozenset({
        "task_id", "endpoint_id", "peer_instance_id", "agent_id", "capability",
        "hop", "parent_task_id", "tenant_id",
    }),
    "federation.task_result_received": frozenset({
        "task_id", "endpoint_id", "peer_instance_id", "agent_id", "status",
        "duration_ms", "our_chain_tail", "peer_chain_tail", "tenant_id",
    }),
    # Agent-to-agent conversation (ADR-2234). Metadata only: the turn TEXT
    # lives in the tenant-local transcript, never in the chain.
    "federation.conversation_started": frozenset({
        "conversation_id", "local_agent_id", "endpoint_id", "peer_instance_id",
        "peer_agent_id", "max_turns", "tenant_id",
    }),
    "federation.conversation_turn": frozenset({
        "conversation_id", "seq", "speaker", "agent_id", "task_id", "status",
        "duration_ms", "text_chars", "tenant_id",
    }),
    "federation.conversation_ended": frozenset({
        "conversation_id", "status", "reason", "turns", "tenant_id",
    }),
    # `/ask @mine` in a peer thread (ADR-2235 Phase 2) — one-shot local turn,
    # answer text stored in the conversation transcript (kind=ask), never here.
    "federation.local_ask": frozenset({
        "conversation_id", "agent_id", "endpoint_id", "status", "duration_ms",
        "task_id", "tenant_id",
    }),
}

SEVERITY: dict[str, str] = {
    "federation.local_agent_registered": "INFO",
    "federation.local_agent_deregistered": "INFO",
    "federation.local_agent_registration_rejected": "WARNING",
    "federation.catalog_fetched": "INFO",
    "federation.task_delegated": "INFO",
    "federation.task_result_received": "INFO",
    "federation.conversation_started": "INFO",
    "federation.conversation_turn": "INFO",
    "federation.conversation_ended": "INFO",
    "federation.local_ask": "INFO",
}


class FederationAuditError(RuntimeError):
    """The chain write did not commit — the caller must treat the action as failed."""


def _core_write_event() -> Callable[..., Any]:
    """``forge.security_events.audit_write_or_die`` (disk-headroom check, raises on failure)."""
    try:
        from forge.security_events import audit_write_or_die as _we  # type: ignore[import]
        return _we
    except ImportError:
        pass
    forge_root = Path(__file__).resolve().parents[2] / "corvin_operator" / "forge"
    if str(forge_root) not in sys.path:
        sys.path.insert(0, str(forge_root))
    cached = sys.modules.get("forge")
    if cached is not None and hasattr(cached, "security_events"):
        return cached.security_events.audit_write_or_die  # type: ignore[attr-defined]
    from forge.security_events import audit_write_or_die as _we  # type: ignore[import]
    return _we


def emit(event: str, *, tenant_id: str, **details: Any) -> str:
    """Write one record; return its hash. Raises :class:`FederationAuditError` on any failure."""
    allowed = ALLOWED_FIELDS.get(event)
    if allowed is None:
        raise FederationAuditError(f"unregistered federation event: {event}")
    payload = {k: v for k, v in details.items() if v is not None}
    payload["tenant_id"] = tenant_id
    extras = set(payload) - allowed
    if extras:
        raise FederationAuditError(f"{event}: fields not allow-listed: {sorted(extras)}")
    try:
        from core.paths import tenant_audit_chain

        chain = tenant_audit_chain(tenant_id)
        chain.parent.mkdir(parents=True, exist_ok=True)
        record = _core_write_event()(chain, event, details=payload, severity=SEVERITY[event])
    except Exception as exc:  # noqa: BLE001 — any failure means "not recorded"
        raise FederationAuditError(f"{event}: audit write failed ({type(exc).__name__})") from exc
    digest = record.get("hash") if isinstance(record, dict) else None
    if not digest:
        raise FederationAuditError(f"{event}: audit write returned no chained record")
    return digest
