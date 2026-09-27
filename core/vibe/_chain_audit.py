"""Default audit backend for core.vibe: THE tenant chain via the forge writer.

Replaces the ``_NoOpAudit`` default that returned a fabricated
``"test_hash_<type>"`` for every "audited" signal while writing nothing
(adversarial review 2026-09-27). Content-free: numbers, codes and ids only.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

#: Source event type (prefix match) → registered chain event type.
_CHAIN_TYPES = (
    ("loss_signal_", "vibe.loss_signal"),
    ("convergence_point_recorded", "vibe.convergence_point"),
    ("learning_loop_cycle_completed", "vibe.loop_cycle"),
)
_DROP = frozenset({"event_type", "timestamp", "prev_hash"})


class ForgeChainAudit:
    """``write_event(dict) -> chain hash``; raises when the record does not commit."""

    def write_event(self, event: Dict[str, Any]) -> Optional[str]:
        from forge import paths as forge_paths  # type: ignore[import-not-found]
        from forge import security_events  # type: ignore[import-not-found]

        source = str(event.get("event_type", ""))
        chain_type = next((t for p, t in _CHAIN_TYPES if source.startswith(p)), None)
        if chain_type is None:
            raise ValueError(f"unregistered vibe audit event {source!r}")
        tenant_id = str(event.get("tenant_id") or "")
        if not tenant_id:
            raise ValueError("vibe audit event without tenant_id")
        details = {k: v for k, v in event.items() if k not in _DROP}
        if "severity" in details:
            details["signal_severity"] = details.pop("severity")
        if chain_type == "vibe.loss_signal":
            details["signal_type"] = source[len("loss_signal_"):]
        rec = security_events.write_event(
            forge_paths.tenant_audit_chain(tenant_id), chain_type, details=details)
        return rec.get("hash")

    def last_hash(self) -> str:
        from forge import paths as forge_paths  # type: ignore[import-not-found]
        from forge import security_events  # type: ignore[import-not-found]

        chain = forge_paths.tenant_audit_chain()
        return (security_events.get_audit_chain_tail(chain) or "") if chain.exists() else ""
