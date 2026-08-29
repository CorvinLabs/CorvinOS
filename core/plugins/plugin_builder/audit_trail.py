"""Phase 4: Audit Trail — Compliance logging for all review decisions.

Hash-chained audit log (JSONL) recording:
- Submission events
- Review decisions (approve/reject/escalate)
- Rubber-stamp detection samples
- Comment updates

Integrates with CorvinOS compliance baseline (GDPR Art. 30, 32).

ADR-0262 Extended: Audit Trail (Phase 4.3)
"""
from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AuditEvent:
    """Immutable audit event record."""

    timestamp: str
    event_type: str  # "submission", "decision", "comment_update", "rubber_stamp"
    plugin_id: str
    pr_number: int
    actor: str  # username or "system"
    decision_status: Optional[str] = None  # "approved", "rejected", "escalated"
    reason: Optional[str] = None
    comment: str = ""
    test_count: Optional[int] = None
    coverage_percent: Optional[float] = None
    tier: Optional[str] = None
    metadata: dict[str, Any] = field(default_factory=dict)
    previous_hash: str = ""

    def compute_hash(self) -> str:
        """Compute SHA256 hash of this event (hash-chain)."""
        # Build payload with only non-None, non-empty values
        payload = {
            "timestamp": self.timestamp,
            "event_type": self.event_type,
            "plugin_id": self.plugin_id,
            "pr_number": self.pr_number,
            "actor": self.actor,
            "previous_hash": self.previous_hash,
        }
        # Add optional fields only if they have meaningful values
        if self.decision_status:
            payload["decision_status"] = self.decision_status
        if self.reason:
            payload["reason"] = self.reason
        if self.comment:
            payload["comment"] = self.comment
        if self.test_count is not None:
            payload["test_count"] = self.test_count
        if self.coverage_percent is not None:
            payload["coverage_percent"] = self.coverage_percent
        if self.tier:
            payload["tier"] = self.tier
        if self.metadata:
            payload["metadata"] = self.metadata

        data = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(data.encode()).hexdigest()

    def to_dict(self) -> dict:
        """Serialize to JSON dict with computed hash."""
        d = {
            "timestamp": self.timestamp,
            "event_type": self.event_type,
            "plugin_id": self.plugin_id,
            "pr_number": self.pr_number,
            "actor": self.actor,
            "decision_status": self.decision_status,
            "reason": self.reason,
            "comment": self.comment,
            "test_count": self.test_count,
            "coverage_percent": self.coverage_percent,
            "tier": self.tier,
            "metadata": self.metadata,
            "previous_hash": self.previous_hash,
            "hash": self.compute_hash(),
        }
        return {k: v for k, v in d.items() if v is not None and v != ""}


class AuditTrail:
    """Hash-chained append-only audit log for Phase 4 decisions."""

    def __init__(self, log_path: Path | str = "./.corvin/audit/phase4.jsonl"):
        """Initialize audit trail with file path."""
        self.log_path = Path(log_path)
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self._last_hash = self._load_last_hash()

    def _load_last_hash(self) -> str:
        """Load the hash of the last event for chaining."""
        if not self.log_path.exists():
            return "0" * 64  # Initial hash

        try:
            with open(self.log_path, "r", encoding="utf-8") as f:
                lines = f.readlines()
            if not lines:
                return "0" * 64

            last_line = lines[-1].strip()
            data = json.loads(last_line)
            return data.get("hash", "0" * 64)
        except (json.JSONDecodeError, IOError) as e:
            logger.error(f"Failed to load last hash: {e}")
            return "0" * 64

    def append(self, event: AuditEvent) -> str:
        """Append event to log, return computed hash."""
        # Create new event with hash-chain link
        chained_event = AuditEvent(
            timestamp=event.timestamp,
            event_type=event.event_type,
            plugin_id=event.plugin_id,
            pr_number=event.pr_number,
            actor=event.actor,
            decision_status=event.decision_status,
            reason=event.reason,
            comment=event.comment,
            test_count=event.test_count,
            coverage_percent=event.coverage_percent,
            tier=event.tier,
            metadata=event.metadata,
            previous_hash=self._last_hash,
        )

        event_hash = chained_event.compute_hash()

        try:
            with open(self.log_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(chained_event.to_dict()) + "\n")
            self._last_hash = event_hash
            logger.debug(f"Audit event logged: {event.event_type} (hash: {event_hash[:8]}...)")
            return event_hash
        except IOError as e:
            logger.error(f"Failed to write audit event: {e}")
            raise

    def log_submission(
        self,
        plugin_id: str,
        pr_number: int,
        actor: str,
        test_count: int,
        coverage_percent: float,
        tier: str,
        comment: str = "",
    ) -> str:
        """Log a test submission event."""
        event = AuditEvent(
            timestamp=datetime.now(timezone.utc).isoformat(),
            event_type="submission",
            plugin_id=plugin_id,
            pr_number=pr_number,
            actor=actor,
            test_count=test_count,
            coverage_percent=coverage_percent,
            tier=tier,
            comment=comment,
        )
        return self.append(event)

    def log_decision(
        self,
        plugin_id: str,
        pr_number: int,
        actor: str,
        status: str,
        reason: str,
        tier: Optional[str] = None,
        comment: str = "",
        metadata: Optional[dict] = None,
    ) -> str:
        """Log an approval/rejection decision."""
        event = AuditEvent(
            timestamp=datetime.now(timezone.utc).isoformat(),
            event_type="decision",
            plugin_id=plugin_id,
            pr_number=pr_number,
            actor=actor,
            decision_status=status,
            reason=reason,
            tier=tier,
            comment=comment,
            metadata=metadata or {},
        )
        return self.append(event)

    def log_comment_update(
        self,
        plugin_id: str,
        pr_number: int,
        actor: str,
        comment_id: int,
        new_content: str,
    ) -> str:
        """Log a PR comment update."""
        event = AuditEvent(
            timestamp=datetime.now(timezone.utc).isoformat(),
            event_type="comment_update",
            plugin_id=plugin_id,
            pr_number=pr_number,
            actor=actor,
            comment=new_content,
            metadata={"comment_id": comment_id},
        )
        return self.append(event)

    def log_rubber_stamp_alert(
        self,
        plugin_id: str,
        pr_number: int,
        actor: str,
        confidence: float,
        decision_count: int,
        max_time_delta: float,
    ) -> str:
        """Log a rubber-stamp detection alert."""
        event = AuditEvent(
            timestamp=datetime.now(timezone.utc).isoformat(),
            event_type="rubber_stamp",
            plugin_id=plugin_id,
            pr_number=pr_number,
            actor=actor,
            comment="Suspicious approval pattern detected",
            metadata={
                "confidence": confidence,
                "decision_count": decision_count,
                "max_time_delta_seconds": max_time_delta,
            },
        )
        return self.append(event)

    def verify_chain(self) -> dict[str, Any]:
        """Verify hash-chain integrity, return summary."""
        if not self.log_path.exists():
            return {"valid": True, "event_count": 0}

        try:
            with open(self.log_path, "r", encoding="utf-8") as f:
                lines = f.readlines()
        except IOError as e:
            logger.error(f"Failed to read audit log: {e}")
            return {"valid": False, "error": str(e)}

        if not lines:
            return {"valid": True, "event_count": 0}

        prev_hash = "0" * 64
        broken_at = None
        event_count = 0

        for i, line in enumerate(lines):
            try:
                data = json.loads(line.strip())
                event_count += 1

                recorded_hash = data.get("hash")
                recorded_prev = data.get("previous_hash", "")

                # Check chain linking
                if recorded_prev != prev_hash:
                    broken_at = i
                    logger.error(f"Chain broken at line {i}: expected prev {prev_hash}, got {recorded_prev}")
                    break

                # Reconstruct the event without the hash field to verify
                # Use the same logic as compute_hash()
                payload = {
                    "timestamp": data.get("timestamp"),
                    "event_type": data.get("event_type"),
                    "plugin_id": data.get("plugin_id"),
                    "pr_number": data.get("pr_number"),
                    "actor": data.get("actor"),
                    "previous_hash": recorded_prev,
                }
                # Add optional fields only if they exist
                for key in ("decision_status", "reason", "comment", "tier", "metadata"):
                    if key in data and data[key]:
                        payload[key] = data[key]
                for key in ("test_count", "coverage_percent"):
                    if key in data and data[key] is not None:
                        payload[key] = data[key]

                reconstructed = json.dumps(payload, sort_keys=True, separators=(",", ":"))
                computed_hash = hashlib.sha256(reconstructed.encode()).hexdigest()

                if computed_hash != recorded_hash:
                    broken_at = i
                    logger.error(f"Hash mismatch at line {i}: expected {recorded_hash}, got {computed_hash}")
                    break

                prev_hash = recorded_hash
            except json.JSONDecodeError as e:
                broken_at = i
                logger.error(f"JSON decode error at line {i}: {e}")
                break

        return {
            "valid": broken_at is None,
            "event_count": event_count,
            "broken_at": broken_at,
        }

    def export_for_compliance(self, start_date: str = "", end_date: str = "") -> list[dict]:
        """Export audit events for compliance reporting (optional date filtering)."""
        if not self.log_path.exists():
            return []

        events = []
        try:
            with open(self.log_path, "r", encoding="utf-8") as f:
                for line in f:
                    try:
                        data = json.loads(line.strip())
                        if start_date and data.get("timestamp", "") < start_date:
                            continue
                        if end_date and data.get("timestamp", "") > end_date:
                            continue
                        events.append(data)
                    except json.JSONDecodeError:
                        pass
        except IOError as e:
            logger.error(f"Failed to export audit trail: {e}")

        return events
