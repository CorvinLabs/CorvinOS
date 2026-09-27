"""AuditChainWriter — facade over THE forge audit writer (ADR-0232/0650).

Until 2026-09-27 this class wrote its OWN record format (``sha256(prev_hash +
event_json)``, no forge canonical hash, no MAC, no flock, no PII floor, raw
``user_id``) straight into the canonical tenant chain it is handed by
``audit_chain_provider``. One intent classification or control-plane override
from the console then made ``forge.security_events.verify_chain`` report the
chain as tampered, which the ADR-0232 boot tripwire (``audit_chain_intact``)
refuses on the next boot (adversarial review round 2).

Every record now goes through ``forge.security_events.write_event`` — the one
writer: canonical hash over ``prev_hash``, keyed MAC where configured, file
lock, the metadata-only floor, the tenant check. The user id rides in the
reserved ``user`` key, which the writer pseudonymises when it has a PII shape.
"""

from __future__ import annotations

import json
import sys
import threading
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional
from uuid import uuid4


def _forge():
    """``(security_events, paths)`` of the one forge writer."""
    try:
        from forge import paths as fp  # type: ignore[import-not-found]
        from forge import security_events as se  # type: ignore[import-not-found]
    except ImportError:
        forge_root = Path(__file__).resolve().parents[2] / "corvin_operator" / "forge"
        if str(forge_root) not in sys.path:
            sys.path.insert(0, str(forge_root))
        cached = sys.modules.get("forge")
        if cached is not None and getattr(cached, "__file__", None) is None:
            del sys.modules["forge"]
        from forge import paths as fp  # type: ignore[import-not-found]
        from forge import security_events as se  # type: ignore[import-not-found]
    return se, fp


@dataclass(frozen=True)
class AuditEvent:
    """Immutable audit event (input to :meth:`AuditChainWriter.write_event`)."""

    event_id: str
    event_type: str
    tenant_id: str
    user_id: Optional[str]
    timestamp: str  # ISO 8601
    details: dict[str, Any] = field(default_factory=dict)
    severity: Optional[str] = None  # "info", "warning", "error", "critical"

    def to_json(self) -> str:
        return json.dumps(asdict(self), sort_keys=True, separators=(",", ":"))


class AuditChainWriter:
    """Writes through ``forge.security_events.write_event`` onto ``log_path``.

    ``log_path`` is the tenant chain (``audit_chain_provider`` passes
    ``tenant_audit_chain(tenant)``); tests may pass a scratch file. Fail-closed:
    a record that does not commit raises.
    """

    GENESIS_HASH = ""  # forge chains start from an empty prev_hash
    VERSION = "2.0"

    def __init__(self, log_path: str | Path):
        self.log_path = Path(log_path)
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()

    def write_event(self, event: AuditEvent) -> str:
        """Append ``event`` via the forge writer; return the record's chain hash.

        Raises:
            IOError: the record did not commit (fail-closed).
        """
        se, _ = _forge()
        details = dict(event.details or {})
        details["tenant_id"] = event.tenant_id
        details["event_id"] = event.event_id
        if event.user_id:
            details["user"] = str(event.user_id)
        sev = str(event.severity).upper() if event.severity else None
        with self._lock:
            try:
                rec = se.write_event(self.log_path, event.event_type, severity=sev,
                                     details=details)
            except Exception as e:  # noqa: BLE001 - fail-closed
                raise IOError(f"Failed to write audit event: {type(e).__name__}: {e}") from e
        h = rec.get("hash") if isinstance(rec, dict) else None
        if not h:
            raise IOError("Failed to write audit event: writer returned no chain hash")
        return h

    def write_event_dict(
        self,
        event_type: str,
        tenant_id: str,
        user_id: Optional[str] = None,
        details: Optional[dict] = None,
        severity: Optional[str] = None,
    ) -> str:
        """Convenience method to write event from dict."""
        return self.write_event(AuditEvent(
            event_id=str(uuid4()),
            event_type=event_type,
            tenant_id=tenant_id,
            user_id=user_id,
            timestamp=datetime.now(timezone.utc).isoformat(),
            details=details or {},
            severity=severity,
        ))

    # ``enforce_retention`` (delete-and-rehash of old records) was REMOVED on
    # 2026-09-07 (F-A13): an audit chain is append-only; retention is the
    # sealed-segment rotation in ``corvin_operator/bridges/shared/audit_sealer.py``.

    def verify_chain(self) -> bool:
        """Verify the file with the forge verifier (the one the tripwire uses)."""
        if not self.log_path.exists():
            return True
        se, _ = _forge()
        try:
            ok, _problems = se.verify_chain(self.log_path)
        except Exception:  # noqa: BLE001
            return False
        return bool(ok)

    def get_last_hash(self) -> str:
        """Hash of the most recent record (``""`` for an empty chain)."""
        if not self.log_path.exists():
            return self.GENESIS_HASH
        se, _ = _forge()
        return se.get_audit_chain_tail(self.log_path) or self.GENESIS_HASH

    def _records(self):
        if not self.log_path.exists():
            return
        with open(self.log_path, "r") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(rec, dict):
                    yield rec

    def get_event_count(self) -> int:
        with self._lock:
            return sum(1 for _ in self._records())

    def read_events(self, tenant_id: Optional[str] = None, limit: int = 1000) -> list[AuditEvent]:
        """Read records back as :class:`AuditEvent` (optionally one tenant)."""
        events: list[AuditEvent] = []
        with self._lock:
            for rec in self._records():
                d = rec.get("details") if isinstance(rec.get("details"), dict) else {}
                tid = d.get("tenant_id", "")
                if tenant_id and tid != tenant_id:
                    continue
                ts = rec.get("ts")
                events.append(AuditEvent(
                    event_id=str(d.get("event_id", "")),
                    event_type=str(rec.get("event_type", "")),
                    tenant_id=str(tid),
                    user_id=d.get("user"),
                    timestamp=(datetime.fromtimestamp(ts, timezone.utc).isoformat()
                               if isinstance(ts, (int, float)) else ""),
                    details={k: v for k, v in d.items()
                             if k not in ("tenant_id", "event_id", "user")},
                    severity=str(rec.get("severity", "")).lower() or None,
                ))
                if len(events) >= limit:
                    break
        return events

    def get_stats(self) -> dict[str, Any]:
        """Audit chain statistics."""
        by_type: dict[str, int] = {}
        by_tenant: dict[str, int] = {}
        total = 0
        with self._lock:
            for rec in self._records():
                total += 1
                et = rec.get("event_type")
                d = rec.get("details") if isinstance(rec.get("details"), dict) else {}
                tid = d.get("tenant_id")
                by_type[et] = by_type.get(et, 0) + 1
                by_tenant[tid] = by_tenant.get(tid, 0) + 1
        return {
            "total_events": total,
            "events_by_type": by_type,
            "events_by_tenant": by_tenant,
            "last_hash": self.get_last_hash(),
            "log_path": str(self.log_path),
            "chain_verified": self.verify_chain(),
        }
