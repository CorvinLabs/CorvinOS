"""Promotion audit trail integration (ADR-0423 Phase 4).

Hash-chained audit events for all feature tier transitions.
Complies with GDPR Art. 30, 32 (data integrity).
"""
from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PromotionAuditEvent:
    """Immutable audit event for feature tier transition."""

    event_type: str  # "feature.promotion_triggered", "feature.promotion_approved", etc.
    timestamp: str  # ISO 8601 UTC
    feature_id: str
    old_tier: str
    new_tier: str
    reason: str
    actor: str  # "daemon", "maintainer", "system"
    actor_id: str | None = None  # For maintainer: user ID
    triggered_by: str | None = None  # "age_requirement", "error_rate", "manual_override"
    metrics_snapshot: dict | None = None  # Metrics at time of transition

    def to_dict(self) -> dict:
        """Serialize to dict."""
        return asdict(self)

    def to_json(self) -> str:
        """Serialize to JSON."""
        return json.dumps(self.to_dict(), default=str)


class PromotionAuditTrail:
    """Hash-chained audit trail for feature promotions."""

    def __init__(
        self,
        storage_path: Path | None = None,
        enabled: bool = True,
    ):
        """Initialize audit trail.

        Args:
            storage_path: Path to audit log JSONL file
            enabled: Whether auditing is enabled
        """
        self.storage_path = storage_path or (
            Path.home() / ".corvin" / "features" / "promotion_audit.jsonl"
        )
        self.enabled = enabled
        self._last_hash: str | None = None

        if enabled:
            self.storage_path.parent.mkdir(parents=True, exist_ok=True)

    def record_event(self, event: PromotionAuditEvent) -> bool:
        """Record one audit event (hash-chained).

        Returns: True if recorded, False if error.
        """
        if not self.enabled:
            return False

        try:
            # Create hash chain
            event_json = event.to_json()
            event_hash = self._compute_hash(event_json, self._last_hash)

            # Append to log
            with open(self.storage_path, "a") as f:
                log_entry = {
                    **event.to_dict(),
                    "hash": event_hash,
                    "previous_hash": self._last_hash,
                }
                f.write(json.dumps(log_entry) + "\n")

            # Update for next event
            self._last_hash = event_hash
            logger.info(f"Audit event recorded: {event.event_type} for {event.feature_id}")
            return True

        except Exception as e:
            logger.error(f"Error recording audit event: {e}", exc_info=True)
            return False

    def record_promotion_triggered(
        self,
        feature_id: str,
        old_tier: str,
        new_tier: str,
        reason: str,
        triggered_by: str | None = None,
        metrics_snapshot: dict | None = None,
    ) -> bool:
        """Record a promotion trigger event."""
        event = PromotionAuditEvent(
            event_type="feature.promotion_triggered",
            timestamp=datetime.utcnow().isoformat() + "Z",
            feature_id=feature_id,
            old_tier=old_tier,
            new_tier=new_tier,
            reason=reason,
            actor="daemon",
            triggered_by=triggered_by,
            metrics_snapshot=metrics_snapshot,
        )
        return self.record_event(event)

    def record_promotion_approved(
        self,
        feature_id: str,
        old_tier: str,
        new_tier: str,
        reason: str,
        maintainer_id: str | None = None,
    ) -> bool:
        """Record a manual promotion approval event."""
        event = PromotionAuditEvent(
            event_type="feature.promotion_approved",
            timestamp=datetime.utcnow().isoformat() + "Z",
            feature_id=feature_id,
            old_tier=old_tier,
            new_tier=new_tier,
            reason=reason,
            actor="maintainer",
            actor_id=maintainer_id,
        )
        return self.record_event(event)

    def record_demotion(
        self,
        feature_id: str,
        old_tier: str,
        new_tier: str,
        reason: str,
        metrics_snapshot: dict | None = None,
    ) -> bool:
        """Record a demotion event."""
        event = PromotionAuditEvent(
            event_type="feature.demoted",
            timestamp=datetime.utcnow().isoformat() + "Z",
            feature_id=feature_id,
            old_tier=old_tier,
            new_tier=new_tier,
            reason=reason,
            actor="daemon",
            metrics_snapshot=metrics_snapshot,
        )
        return self.record_event(event)

    def record_feedback(
        self,
        feature_id: str,
        feedback_score: float,
        user_note: str | None = None,
    ) -> bool:
        """Record user feedback for a feature."""
        event = PromotionAuditEvent(
            event_type="feature.feedback_recorded",
            timestamp=datetime.utcnow().isoformat() + "Z",
            feature_id=feature_id,
            old_tier="",
            new_tier="",
            reason=user_note or f"Feedback score: {feedback_score:.1f}",
            actor="user",
        )
        return self.record_event(event)

    def verify_chain(self) -> tuple[bool, str]:
        """Verify the entire hash chain integrity.

        Returns: (is_valid, message)
        """
        if not self.storage_path.exists():
            return True, "No audit log yet"

        try:
            prev_hash = None
            line_num = 0

            with open(self.storage_path, "r") as f:
                for line in f:
                    line_num += 1
                    if not line.strip():
                        continue

                    log_entry = json.loads(line)
                    recorded_hash = log_entry.get("hash")
                    recorded_prev_hash = log_entry.get("previous_hash")

                    # Verify chain continuity
                    if prev_hash != recorded_prev_hash:
                        return False, f"Chain broken at line {line_num}: expected prev_hash {prev_hash}, got {recorded_prev_hash}"

                    # Verify hash by recomputing
                    event_data = {k: v for k, v in log_entry.items() if k not in ["hash", "previous_hash"]}
                    recomputed_hash = self._compute_hash(json.dumps(event_data), recorded_prev_hash)

                    if recomputed_hash != recorded_hash:
                        return False, f"Hash mismatch at line {line_num}: expected {recomputed_hash}, got {recorded_hash}"

                    prev_hash = recorded_hash

            return True, f"Chain verified ({line_num} events)"

        except Exception as e:
            return False, f"Verification error: {e}"

    @staticmethod
    def _compute_hash(event_json: str, previous_hash: str | None) -> str:
        """Compute hash for one event (SHA256).

        Args:
            event_json: Event serialized as JSON string
            previous_hash: Previous event's hash (or None for first event)

        Returns:
            Hex-encoded SHA256 hash
        """
        combined = event_json
        if previous_hash:
            combined = f"{previous_hash}:{event_json}"

        return hashlib.sha256(combined.encode()).hexdigest()

    def get_feature_history(self, feature_id: str) -> list[PromotionAuditEvent]:
        """Get all audit events for a feature."""
        if not self.storage_path.exists():
            return []

        events = []
        try:
            with open(self.storage_path, "r") as f:
                for line in f:
                    if not line.strip():
                        continue
                    try:
                        log_entry = json.loads(line)
                        if log_entry.get("feature_id") == feature_id:
                            # Reconstruct event (without hash fields)
                            event_data = {
                                k: v
                                for k, v in log_entry.items()
                                if k not in ["hash", "previous_hash"]
                            }
                            event = PromotionAuditEvent(**event_data)
                            events.append(event)
                    except (json.JSONDecodeError, TypeError):
                        pass
        except Exception as e:
            logger.error(f"Error reading audit history: {e}")

        return sorted(events, key=lambda e: e.timestamp)

    def reset(self) -> None:
        """Clear audit trail (for testing only)."""
        if self.storage_path.exists():
            self.storage_path.unlink()
        self._last_hash = None


# Singleton instance
_AUDIT_TRAIL: PromotionAuditTrail | None = None


def initialize_audit_trail(
    storage_path: Path | None = None,
    enabled: bool = True,
) -> PromotionAuditTrail:
    """Initialize the global promotion audit trail."""
    global _AUDIT_TRAIL
    _AUDIT_TRAIL = PromotionAuditTrail(storage_path=storage_path, enabled=enabled)
    return _AUDIT_TRAIL


def get_audit_trail() -> PromotionAuditTrail | None:
    """Get the global promotion audit trail."""
    return _AUDIT_TRAIL
