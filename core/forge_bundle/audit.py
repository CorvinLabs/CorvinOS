"""Forge Bundle audit emitters — the tenant's ONE hash chain (ADR-2229).

Mirrors ``core.orchestration.layer_forge.audit`` exactly: every record goes
through ``core.paths.tenant_audit_chain(tenant_id)`` via
``forge.security_events.audit_write_or_die``. Metadata only — ids, versions,
counts. Never manifest bodies, file contents or exception messages.

Export is read-only (it writes a ZIP to the caller, not a registry), so this
is not audit-first the way Layer Forge's registry writes are — there is
nothing to roll back. If the chain write itself fails, the error still
propagates: an export that could not be recorded is reported as failed, not
silently handed back as if it had been.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Callable

# Mirrored in corvin_operator/forge/forge/security_events.py (EVENT_SEVERITY + _EVENT_ALLOWLIST);
# test_module_allowlist_matches_the_central_registry pins the two together.
ALLOWED_FIELDS: dict[str, frozenset[str]] = {
    "forge_bundle.exported": frozenset({
        "bundle_id", "bundle_version", "artifact_count", "total_bytes", "tenant_id",
    }),
}

SEVERITY: dict[str, str] = {
    "forge_bundle.exported": "INFO",
}


class ForgeBundleAuditError(RuntimeError):
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
    """Write one record; return its hash. Raises :class:`ForgeBundleAuditError` on any failure."""
    allowed = ALLOWED_FIELDS.get(event)
    if allowed is None:
        raise ForgeBundleAuditError(f"unregistered forge_bundle event: {event}")
    payload = {k: v for k, v in details.items() if v is not None}
    payload["tenant_id"] = tenant_id
    extras = set(payload) - allowed
    if extras:
        raise ForgeBundleAuditError(f"{event}: fields not allow-listed: {sorted(extras)}")
    try:
        from core.paths import tenant_audit_chain

        chain = tenant_audit_chain(tenant_id)
        chain.parent.mkdir(parents=True, exist_ok=True)
        record = _core_write_event()(chain, event, details=payload, severity=SEVERITY[event])
    except Exception as exc:  # noqa: BLE001 — any failure means "not recorded"
        raise ForgeBundleAuditError(f"{event}: audit write failed ({type(exc).__name__})") from exc
    digest = record.get("hash") if isinstance(record, dict) else None
    if not digest:
        raise ForgeBundleAuditError(f"{event}: audit write returned no chained record")
    return digest
