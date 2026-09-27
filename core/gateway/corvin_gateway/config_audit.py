"""Audit sink for the gateway's ``CentralizedConfigManager`` (ADR-2066).

``CentralizedConfigManager.create_with_audit(tenant_audit_chain())`` binds an
``AuditChainWriter`` -- a second, incompatible hash scheme -- to THE canonical
tenant chain; its first config event made ``security_events.verify_chain`` (and
so the ADR-0232 boot tripwire) read the chain as tampered. Config events go
through the one forge writer instead. Kept out of ``app.py`` so it is testable
without importing the whole console.
"""
from __future__ import annotations


class ForgeConfigAudit:
    """``audit_chain`` for ``CentralizedConfigManager`` that writes through the
    ONE forge writer onto the event's own tenant chain (``tenant_audit_chain``).

    Same ``write_event_dict`` surface as ``AuditChainWriter``, which the manager
    calls; the record shape, hash-chaining, field floor and tenant check are
    forge's, so the chain stays verifiable. Raises on failure like the writer
    it replaces (the manager already treats config audit as best-effort).
    """

    def write_event_dict(self, event_type: str, tenant_id: str,
                         user_id: str | None = None, details: dict | None = None,
                         severity: str | None = None) -> str:
        from forge import paths as _fp  # noqa: PLC0415
        from forge import security_events as _se  # noqa: PLC0415

        chain = _fp.tenant_audit_chain(tenant_id)
        chain.parent.mkdir(parents=True, exist_ok=True)
        _se.write_event(
            chain, str(event_type),
            severity=str(severity).upper() if severity else None,
            details={**(details or {}), "tenant_id": tenant_id},
            hash_chain=True,
        )
        return ""


def build_config_manager():
    """The gateway's ``CentralizedConfigManager`` (ADR-2066), audited through
    :class:`ForgeConfigAudit` — never through a private chain writer."""
    from core.config import CentralizedConfigManager  # noqa: PLC0415
    return CentralizedConfigManager(audit_chain=ForgeConfigAudit())
