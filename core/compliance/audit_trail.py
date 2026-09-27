"""L16 audit record shape — ADR-0232 "Phase 1A" skeleton, DEFUSED.

NOT WIRED: no production caller as of 2026-09-27 (adversarial review).

This module used to ship its own ``AuditTrail``: a SECOND hash chain in a file
of the caller's choosing, in its own record format, whose record hash did not
cover ``prev_hash`` (so records could be reordered undetected) and which wrote
the raw ``user_id`` as ``actor`` into an append-only file. CorvinOS has exactly
ONE audit chain per tenant (ADR-0650): ``forge.paths.tenant_audit_chain()``,
written only through ``forge.security_events.write_event`` (hash-chained over
``prev_hash``, PII-floored, tenant-checked). A second writer can only split the
trail, so :class:`AuditTrail` now refuses to exist and names the canonical one.

:class:`AuditRecord` / :func:`new_audit_record` stay as an inert value type;
``new_audit_record`` pseudonymises the actor so a raw user id cannot even be
put into one.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any, Optional

#: Actors that name a system role, not a person — kept verbatim.
_SYSTEM_ACTORS = frozenset({"system", "operator", "scheduler", "tripwire"})

_CANONICAL = (
    "forge.security_events.write_event(forge.paths.tenant_audit_chain(tenant_id), "
    "event_type, details=...)"
)


def pseudonymise_actor(actor: str) -> str:
    """``sha256(actor)[:8]`` for a person, the role name for a system actor.

    Same transform the core writer applies to PII-shaped identifiers, so a
    value pseudonymised here collides into the same namespace."""
    a = str(actor or "")
    if a in _SYSTEM_ACTORS:
        return a
    return hashlib.sha256(a.encode("utf-8", "surrogatepass")).hexdigest()[:8]


@dataclass(frozen=True)
class AuditRecord:
    """Immutable audit record (value type only — it is written nowhere here)."""
    timestamp: str  # ISO-8601 UTC
    event_type: str
    tenant_id: str
    actor: str
    action: str
    resource: str
    result: str  # "allowed" | "denied" | "error"
    details: dict  # Metadata (no PII)

    def to_json(self) -> str:
        """Serialize to JSON (sorted for determinism)."""
        return json.dumps(asdict(self), sort_keys=True)

    def hash(self) -> str:
        """SHA256 of the record content (NOT a chain hash — see module doc)."""
        return hashlib.sha256(self.to_json().encode()).hexdigest()


class AuditTrail:
    """Refused: CorvinOS has one audit chain per tenant, not one per caller."""

    def __init__(self, *_args: Any, **_kwargs: Any):
        raise NotImplementedError(
            "core.compliance.audit_trail.AuditTrail would create a second audit chain "
            f"and is disabled; write through {_CANONICAL} instead"
        )


def new_audit_record(
    event_type: str,
    tenant_id: str,
    actor: str,
    action: str,
    resource: str,
    result: str,
    details: Optional[dict] = None,
) -> AuditRecord:
    """Create an audit record value. ``actor`` is pseudonymised (never raw)."""
    return AuditRecord(
        timestamp=datetime.now(timezone.utc).isoformat(),
        event_type=event_type,
        tenant_id=tenant_id,
        actor=pseudonymise_actor(actor),
        action=action,
        resource=resource,
        result=result,
        details=details or {},
    )


__all__ = ["AuditRecord", "AuditTrail", "new_audit_record", "pseudonymise_actor"]
