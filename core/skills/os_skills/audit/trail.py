"""DataHub Creator audit trail — a view onto the tenant's CORE audit chain.

NOT WIRED: no production caller as of 2026-09-27 (adversarial review).

Until 2026-09-27 this module kept its OWN hash-chain format in a caller-chosen
JSONL file, with free-form payloads (tests wrote e-mail addresses and phone
numbers into it): a second, private "audit" chain that no verifier, tripwire
or compliance report reads, holding personal data in an append-only file that
can never be redacted. It now delegates:

* every write goes through ``forge.security_events.write_event`` on
  ``tenant_audit_chain(tenant_id)`` — the one chain per tenant (CLAUDE.md
  § Multi-tenant Axis), under a fixed ``datahub.*`` event vocabulary that is
  registered (severity + positive allowlist) with the core writer at import;
* a payload key outside that vocabulary, a non-scalar value, or a value the
  metadata floor drops (PII/secret shape) is REFUSED (``ValueError``), never
  silently written short;
* any other ``chain_path`` raises ``NotImplementedError`` — a private chain
  file is not an audit trail;
* integrity is ``forge.security_events.verify_chain`` — the same verdict the
  ADR-0232 boot tripwire reaches.

Reads (``query_events``) are tenant-scoped by construction — they read only
this tenant's chain file — and return ``AuditEvent`` views of its
``datahub.*`` records.
"""
from __future__ import annotations

import json
import logging
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

#: Chain event-type prefix for every record this module writes.
EVENT_PREFIX = "datahub."

_COMMON_FIELDS = frozenset({"skill_id", "skill_version"})

#: The ONLY events and payload keys this trail writes. Content-free scalars
#: (ids, counts, measurements, closed labels); extend here, in one place, and
#: the registration below follows.
EVENT_PAYLOAD_FIELDS: dict[str, frozenset[str]] = {
    "skill_generated": frozenset({"skill_name", "loss_before", "loss_after",
                                  "improvement_pct", "phase_count", "source"}),
    "weight_updated": frozenset({"source_id", "weight_before", "weight_after",
                                 "change_pct", "reason"}),
    "feedback_received": frozenset({"signal", "impact_on_loss"}),
    "optimization_completed": frozenset({"loss_before", "loss_after",
                                         "improvement_pct", "phase_count"}),
}

_SCALAR = (str, int, float, bool, type(None))
_registered = False


def _security_events():
    """The core audit writer, with this module's events registered (idempotent)."""
    global _registered
    forge_dir = Path(__file__).resolve().parents[4] / "corvin_operator" / "forge"
    if forge_dir.is_dir() and str(forge_dir) not in sys.path:
        sys.path.insert(0, str(forge_dir))
    from forge import security_events as se  # type: ignore[import-not-found]

    if not _registered:
        for name, fields in EVENT_PAYLOAD_FIELDS.items():
            se.EVENT_SEVERITY.setdefault(EVENT_PREFIX + name, "INFO")
            se.register_event_allowlist(EVENT_PREFIX + name, fields | _COMMON_FIELDS)
        _registered = True
    return se


@dataclass(frozen=True)
class AuditEvent:
    """Read-only view of one ``datahub.*`` record of the core chain."""

    event_type: str  # "skill_generated", "weight_updated", "feedback_received", ...
    tenant_id: str
    timestamp: str  # ISO 8601 (UTC), from the record's ``ts``
    skill_id: Optional[str] = None
    skill_version: Optional[str] = None
    payload: dict = field(default_factory=dict)
    prev_hash: str = ""  # the core record's prev_hash
    hash: str = ""  # the core record's hash


def _event_from_record(record: dict, tenant_id: str) -> Optional[AuditEvent]:
    et = str(record.get("event_type", ""))
    if not et.startswith(EVENT_PREFIX):
        return None
    name = et[len(EVENT_PREFIX):]
    details = record.get("details") or {}
    allowed = EVENT_PAYLOAD_FIELDS.get(name, frozenset())
    ts = record.get("ts")
    try:
        stamp = datetime.fromtimestamp(float(ts), tz=timezone.utc).isoformat()
    except (TypeError, ValueError):
        stamp = ""
    return AuditEvent(
        event_type=name,
        tenant_id=tenant_id,  # the chain file is per tenant
        timestamp=stamp,
        skill_id=details.get("skill_id"),
        skill_version=details.get("skill_version"),
        payload={k: v for k, v in details.items() if k in allowed},
        prev_hash=str(record.get("prev_hash", "")),
        hash=str(record.get("hash", "")),
    )


class AuditTrail:
    """Tenant-scoped DataHub view onto the core audit chain."""

    def __init__(self, tenant_id: str, chain_path: Optional[Path] = None):
        """
        Args:
            tenant_id: Tenant identifier (validated; fail-closed if missing)
            chain_path: Must be omitted or equal ``tenant_audit_chain(tenant_id)``.

        Raises:
            ValueError: invalid tenant, or the tenant's chain does not verify
            NotImplementedError: ``chain_path`` names any other file
        """
        if not tenant_id:
            raise ValueError("tenant_id must not be None (fail-closed)")
        from core.paths.tenant import tenant_audit_chain
        from core.tenants import validate_tenant_id

        validate_tenant_id(tenant_id)
        canonical = tenant_audit_chain(tenant_id)
        if chain_path is not None and (
            Path(chain_path).expanduser().resolve() != canonical.resolve()
        ):
            raise NotImplementedError(
                "AuditTrail writes only to the tenant's core audit chain "
                "(tenant_audit_chain); a private chain file is not an audit trail"
            )
        self.tenant_id = tenant_id
        self.chain_path = canonical
        self._se = _security_events()

        # Verify chain integrity on construction (fail-closed)
        self._verify_chain()

    def _verify_chain(self) -> None:
        """Raise ValueError unless the tenant's core chain verifies."""
        if not self.chain_path.exists():
            return  # empty chain is valid
        ok, problems = self._se.verify_chain(self.chain_path)
        if not ok:
            raise ValueError(f"Audit chain broken: {problems[:3]}")

    def write_event(
        self,
        event_type: str,
        skill_id: Optional[str] = None,
        skill_version: Optional[str] = None,
        payload: Optional[dict] = None,
    ) -> AuditEvent:
        """Append one ``datahub.<event_type>`` record to the core chain.

        Raises:
            ValueError: unknown event type, payload key outside the vocabulary,
                non-scalar value, or a value the metadata floor dropped
                (PII/secret shape) — the caller learns the record was refused
                or sanitised instead of it silently losing fields.
        """
        allowed = EVENT_PAYLOAD_FIELDS.get(event_type)
        if allowed is None:
            raise ValueError(f"unknown DataHub audit event type: {event_type!r}")
        payload = dict(payload or {})
        extra = sorted(set(payload) - allowed)
        if extra:
            raise ValueError(
                f"payload keys {extra} are not in the {event_type!r} vocabulary "
                f"({sorted(allowed)})"
            )
        bad = sorted(k for k, v in payload.items() if not isinstance(v, _SCALAR))
        if bad:
            raise ValueError(f"payload values must be scalars: {bad}")
        for name, value in (("skill_id", skill_id), ("skill_version", skill_version)):
            if value is not None and not re.fullmatch(r"[A-Za-z0-9._:-]{1,128}", str(value)):
                raise ValueError(f"invalid {name}: {value!r}")

        # No ``tenant_id`` in details: the chain FILE is this tenant's
        # (tenant_audit_chain), and the core writer refuses a details.tenant_id
        # that differs from the PROCESS tenant (AuditTenantMismatch).
        details: dict[str, Any] = dict(payload)
        if skill_id is not None:
            details["skill_id"] = skill_id
        if skill_version is not None:
            details["skill_version"] = skill_version

        record = self._se.write_event(
            self.chain_path,
            EVENT_PREFIX + event_type,
            severity="INFO",
            tool="os_skills.audit",
            details=details,
        )
        dropped = (record.get("details") or {}).get("_dropped_fields")
        if dropped:
            raise ValueError(
                f"audit floor dropped {dropped} from {event_type!r} "
                "(PII/secret shape) — the record was written without them"
            )
        event = _event_from_record(record, self.tenant_id)
        assert event is not None
        return event

    def query_events(
        self,
        event_type: Optional[str] = None,
        skill_id: Optional[str] = None,
        since: Optional[datetime] = None,
        limit: int = 100,
    ) -> list[AuditEvent]:
        """Tenant-scoped ``datahub.*`` events, oldest first (up to ``limit``)."""
        results: list[AuditEvent] = []
        if not self.chain_path.exists():
            return results
        since_iso = None
        if since is not None:
            since_iso = since.isoformat() if isinstance(since, datetime) else str(since)

        with open(self.chain_path, "r", encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    logger.warning("Skipping malformed line in audit chain")
                    continue
                event = _event_from_record(record, self.tenant_id)
                if event is None:
                    continue
                if event_type and event.event_type != event_type:
                    continue
                if skill_id and event.skill_id != skill_id:
                    continue
                if since_iso and event.timestamp < since_iso:
                    continue
                results.append(event)
                if len(results) >= limit:
                    break
        return results

    def export_jsonl(self, since: Optional[datetime] = None) -> str:
        """Export this tenant's DataHub events as JSONL (compliance reports)."""
        from dataclasses import asdict

        events = self.query_events(since=since, limit=999999)
        return "\n".join(json.dumps(asdict(e), default=str) for e in events)

    def verify_integrity(self) -> tuple[bool, str]:
        """``(is_valid, message)`` — the core writer's own chain verdict."""
        try:
            self._verify_chain()
            return True, "Audit chain intact"
        except ValueError as e:
            return False, str(e)
