"""Personal-KB audit emitters: the tenant's ONE hash chain, content-free (ADR-2326 item 11).

Mirrors ``core.forge_bundle.audit``: every record goes through
``core.paths.tenant_audit_chain(tenant_id)`` via ``forge.security_events.audit_write_or_die``.
Records carry ``owner_hash = sha256(owner_id)[:16]`` and never text, titles, handles, handle
hashes, bridge uids or passphrases. Mutations are audit-first: a record that does not commit
stops the operation (:class:`PersonalKbAuditError`).
"""
from __future__ import annotations

import hashlib
import sys
from pathlib import Path
from typing import Any, Callable

_T = "tenant_id"
_O = "owner_hash"

# Mirrored in corvin_operator/forge/forge/security_events.py (EVENT_SEVERITY + _EVENT_ALLOWLIST);
# tests/personal_kb/test_audit.py pins the two together.
ALLOWED_FIELDS: dict[str, frozenset[str]] = {
    "personal_kb.owner_created": frozenset({_O, "unlock_mode", "schema", _T}),
    "personal_kb.unlocked": frozenset({_O, "surface", "mode", _T}),
    "personal_kb.unlock_failed": frozenset({"surface", "attempt_bucket", _T}),
    "personal_kb.locked": frozenset({_O, "reason", _T}),
    "personal_kb.passphrase_changed": frozenset({_O, "key_epoch", _T}),
    "personal_kb.recovery_used": frozenset({_O, "key_epoch", _T}),
    "personal_kb.key_rotated": frozenset({_O, "key_epoch", _T}),
    "personal_kb.pair_issued": frozenset({_O, "channel", _T}),
    "personal_kb.pair_confirmed": frozenset({_O, "channel", _T}),
    "personal_kb.pair_failed": frozenset({"channel", "reason_code", _T}),
    "personal_kb.identity_unbound": frozenset({_O, "channel", _T}),
    "personal_kb.entity_written": frozenset({_O, "uid", "type", "origin", "version", _T}),
    "personal_kb.write_failed": frozenset({_O, "uid", "reason_code", _T}),
    "personal_kb.entity_forgotten": frozenset({_O, "uid", "type", _T}),
    "personal_kb.captured": frozenset({_O, "uid", "type", "confidence_bucket", "policy_version", _T}),
    "personal_kb.capture_dropped": frozenset({_O, "reason_code", _T}),
    "personal_kb.context_injected": frozenset({_O, "entry_count", "surface", _T}),
    "personal_kb.context_withheld": frozenset({_O, "entry_count", "surface", _T}),
    "personal_kb.expired": frozenset({_O, "count", _T}),
    "personal_kb.migrated": frozenset({_O, "count", _T}),
    "personal_kb.promoted": frozenset({_O, "uid", "project_uid", _T}),
    "personal_kb.exported": frozenset({_O, "entity_count", _T}),
    "personal_kb.policy_updated": frozenset({_O, "policy_version", "params", "deltas", "cause_code", _T}),
    "personal_kb.policy_rolled_back": frozenset({_O, "policy_version", "params", "deltas", "cause_code", _T}),
    "personal_kb.policy_refused": frozenset({_O, "policy_version", "params", "cause_code", _T}),
    "personal_kb.policy_confirmed": frozenset({_O, "policy_version", "params", "deltas", "cause_code", _T}),
    "personal_kb.policy_unfrozen": frozenset({_O, "policy_version", "cause_code", _T}),
    "personal_kb.learning_frozen": frozenset({_O, "reason_code", _T}),
    "personal_kb.decoder_stage": frozenset({_O, "uid", "stage", "gate", "loss_bucket", "iteration", _T}),
    "personal_kb.decoder_objection": frozenset({_O, "uid", "stage", "count", "resolved", _T}),
    "personal_kb.erasure_started": frozenset({_O, _T}),
    "erasure.personal_kb_deleted": frozenset({_O, "objects_deleted", "layer", _T}),
}

SEVERITY: dict[str, str] = {e: "INFO" for e in ALLOWED_FIELDS}
SEVERITY.update({
    "personal_kb.unlock_failed": "WARNING",
    "personal_kb.passphrase_changed": "WARNING",
    "personal_kb.recovery_used": "WARNING",
    "personal_kb.key_rotated": "WARNING",
    "personal_kb.pair_confirmed": "WARNING",
    "personal_kb.pair_failed": "WARNING",
    "personal_kb.identity_unbound": "WARNING",
    "personal_kb.write_failed": "WARNING",
    "personal_kb.exported": "WARNING",
    "personal_kb.policy_rolled_back": "WARNING",
    "personal_kb.policy_refused": "WARNING",
    "personal_kb.learning_frozen": "WARNING",
    "personal_kb.erasure_started": "WARNING",
    "erasure.personal_kb_deleted": "WARNING",
})


class PersonalKbAuditError(RuntimeError):
    """The chain write did not commit; the caller must treat the action as failed."""


def owner_hash(owner_id: str) -> str:
    return hashlib.sha256(owner_id.encode("utf-8")).hexdigest()[:16]


def _core_write_event() -> Callable[..., Any]:
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
    """Write one record; return its hash. Raises :class:`PersonalKbAuditError` on any failure."""
    allowed = ALLOWED_FIELDS.get(event)
    if allowed is None:
        raise PersonalKbAuditError(f"unregistered personal_kb event: {event}")
    payload = {k: v for k, v in details.items() if v is not None}
    payload[_T] = tenant_id
    extras = set(payload) - allowed
    if extras:
        raise PersonalKbAuditError(f"{event}: fields not allow-listed: {sorted(extras)}")
    try:
        from core.paths import tenant_audit_chain

        chain = tenant_audit_chain(tenant_id)
        chain.parent.mkdir(parents=True, exist_ok=True)
        record = _core_write_event()(chain, event, details=payload, severity=SEVERITY[event])
    except Exception as exc:  # noqa: BLE001 - any failure means "not recorded"
        raise PersonalKbAuditError(f"{event}: audit write failed ({type(exc).__name__})") from exc
    digest = record.get("hash") if isinstance(record, dict) else None
    if not digest:
        raise PersonalKbAuditError(f"{event}: audit write returned no chained record")
    return digest


def bucket(value: float) -> str:
    """Coarse bucket for a 0..1 number (no fine-grained signal in the chain)."""
    v = max(0.0, min(1.0, float(value)))
    return "low" if v < 0.34 else "mid" if v < 0.67 else "high"
