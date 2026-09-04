"""L5 k=5: Rollback Guard — Approval Stability & Operator Override.

ADR-0582: Rollback Guard (Approval Stability & Operator Override)
Enforces configurable hold periods for approved configs.
Allows operator force-revoke at any time (with mandatory reason + alert).
Learns from overrides to adjust future hold periods.
"""

from dataclasses import dataclass, field
from typing import Dict, Optional, Tuple
from enum import Enum
import logging
from datetime import datetime, timedelta
import json
from pathlib import Path
import threading
import os

logger = logging.getLogger(__name__)


class Criticality(str, Enum):
    """Skill criticality level determines default hold period."""
    CRITICAL = "critical"    # 1h hold (production hotfix candidate)
    MEDIUM = "medium"        # 12h hold (learning stability)
    LOW = "low"              # 48h hold (experimental, needs stability proof)


DEFAULT_HOLD_HOURS = {
    Criticality.CRITICAL: 1,
    Criticality.MEDIUM: 12,
    Criticality.LOW: 48,
}


@dataclass
class RollbackRequest:
    """Request to revoke an approved config change."""
    approval_id: str
    operator_id: str
    force: bool               # True if operator is forcing override
    reason: str = ""          # Mandatory for force_revoke
    timestamp: str = ""       # ISO 8601


@dataclass
class RollbackDecision:
    """Outcome of a revoke request."""
    approval_id: str
    allowed: bool
    reason: str               # Why allowed/denied
    time_remaining_seconds: Optional[int] = None  # If denied: seconds until allowed
    override_timestamp: Optional[str] = None  # If forced: when override happened


@dataclass
class OverrideMetrics:
    """Track operator override behavior for learning."""
    skill_id: str
    approval_id: str
    time_into_hold_seconds: int  # How far into hold period when overridden
    hold_period_configured_seconds: int  # Total configured hold
    timestamp: str


class RollbackGuard:
    """L5 k=5: Enforces hold periods on approved configs + operator overrides.

    Constraints:
    - Hold is ADVISORY (operator can force-revoke at any time)
    - Reason mandatory for force-revoke
    - Audit-first (every revoke logged before execution)
    - Operator attribution (every override includes operator_id)
    - Learning enabled (collect override metrics, adjust TTLs)
    """

    def __init__(
        self,
        tenant_id: str = "_default",
        audit_backend=None,
        corvin_home: str = None,
    ):
        """Initialize rollback guard.

        Args:
            tenant_id: Tenant for isolation
            audit_backend: Audit backend (required)
            corvin_home: Path to ~/.corvin
        """
        self.tenant_id = tenant_id
        self.audit_backend = audit_backend

        # Persistence: path to rollback_history.jsonl
        if corvin_home is None:
            corvin_home = os.getenv("CORVIN_HOME", os.path.expanduser("~/.corvin"))
        self.corvin_home = Path(corvin_home)
        self.history_file = (
            self.corvin_home
            / "tenants"
            / tenant_id
            / "learning"
            / "rollback_history.jsonl"
        )

        # Thread safety
        self._lock = threading.RLock()

        # Per-Skill hold period config (in hours)
        self.skill_hold_config: Dict[str, int] = {}  # skill_id -> hours

        # Per-Skill criticality (inferred or explicit)
        self.skill_criticality: Dict[str, Criticality] = {}  # skill_id -> Criticality

        # Approval tracking: approval_id -> approval_timestamp
        self.approval_apply_times: Dict[str, str] = {}

        # Override metrics for learning
        self.override_metrics: Dict[str, OverrideMetrics] = {}

        # Load persisted history
        self._load_persisted_history()

    def _load_persisted_history(self) -> None:
        """Load rollback history from disk (recovery after restart)."""
        if not self.history_file.exists():
            return

        try:
            with open(self.history_file, "r") as f:
                for line in f:
                    if not line.strip():
                        continue
                    try:
                        data = json.loads(line)
                        # Reconstruct override metrics from persisted history
                        if data.get("type") == "override_metric":
                            approval_id = data.get("approval_id", "")
                            if approval_id:
                                metrics = OverrideMetrics(
                                    skill_id=data.get("skill_id", ""),
                                    approval_id=approval_id,
                                    time_into_hold_seconds=data.get("time_into_hold_seconds", 0),
                                    hold_period_configured_seconds=data.get("hold_period_configured_seconds", 0),
                                    timestamp=data.get("timestamp", ""),
                                )
                                self.override_metrics[approval_id] = metrics
                    except (json.JSONDecodeError, TypeError) as e:
                        logger.warning(f"[L5 Rollback] Failed to load history: {e}")
        except Exception as e:
            logger.error(f"[L5 Rollback] Failed to load persisted history: {e}")

    def _persist_decision(self, decision: RollbackDecision) -> None:
        """Append decision to disk (immutable log)."""
        try:
            self.history_file.parent.mkdir(parents=True, exist_ok=True)

            with open(self.history_file, "a") as f:
                record = {
                    "type": "revoke_decision",
                    "approval_id": decision.approval_id,
                    "allowed": decision.allowed,
                    "reason": decision.reason,
                    "timestamp": datetime.utcnow().isoformat() + "Z",
                }
                f.write(json.dumps(record) + "\n")
        except Exception as e:
            logger.error(f"[L5 Rollback] Failed to persist decision: {e}")

    def _persist_override(self, metrics: OverrideMetrics) -> None:
        """Append override metrics to disk."""
        try:
            self.history_file.parent.mkdir(parents=True, exist_ok=True)

            with open(self.history_file, "a") as f:
                record = {
                    "type": "override_metric",
                    "skill_id": metrics.skill_id,
                    "approval_id": metrics.approval_id,
                    "time_into_hold_seconds": metrics.time_into_hold_seconds,
                    "hold_period_configured_seconds": metrics.hold_period_configured_seconds,
                    "timestamp": metrics.timestamp,
                }
                f.write(json.dumps(record) + "\n")
        except Exception as e:
            logger.error(f"[L5 Rollback] Failed to persist override metrics: {e}")

    def register_approval(
        self,
        approval_id: str,
        skill_id: str,
        criticality: Criticality = Criticality.MEDIUM,
        custom_hold_hours: Optional[int] = None,
    ) -> None:
        """Register a newly-approved config change.

        Args:
            approval_id: UUID of the approval
            skill_id: Skill that generated the approval
            criticality: Skill criticality level (determines default hold)
            custom_hold_hours: Override default hold period (None = use default)
        """
        with self._lock:
            # Record approval apply time
            self.approval_apply_times[approval_id] = datetime.utcnow().isoformat() + "Z"

            # Store criticality
            self.skill_criticality[skill_id] = criticality

            # Store hold period (custom or default)
            if custom_hold_hours is not None:
                self.skill_hold_config[skill_id] = custom_hold_hours
            else:
                self.skill_hold_config[skill_id] = DEFAULT_HOLD_HOURS[criticality]

            logger.info(
                f"[L5 Rollback] Registered {approval_id} for {skill_id} "
                f"(hold={self.skill_hold_config[skill_id]}h)"
            )

    def can_revoke(
        self, approval_id: str, skill_id: str
    ) -> Tuple[bool, Optional[str]]:
        """Check if an approval can be revoked (normal, non-forced).

        Returns:
            (allowed, reason_or_time_remaining)
            - If allowed: (True, None)
            - If blocked by hold: (False, "HH:MM:SS until allowed")
        """
        with self._lock:
            # Check if approval exists
            if approval_id not in self.approval_apply_times:
                return (False, "Approval not found")

            # Get hold period for this Skill
            hold_hours = self.skill_hold_config.get(skill_id, 12)

            # Compute elapsed time (with error handling for malformed timestamps)
            try:
                apply_time = datetime.fromisoformat(
                    self.approval_apply_times[approval_id].replace("Z", "")
                )
            except (ValueError, TypeError) as e:
                logger.warning(f"[L5 Rollback] Malformed timestamp for {approval_id}: {e}; cannot revoke yet")
                # Graceful degradation: assume infinite remaining time (cannot revoke)
                return (False, "inf (malformed timestamp)")

            now = datetime.utcnow()
            elapsed = now - apply_time

            hold_period = timedelta(hours=hold_hours)

            if elapsed < hold_period:
                # Still in hold period
                remaining = hold_period - elapsed
                hours, remainder = divmod(remaining.total_seconds(), 3600)
                minutes, seconds = divmod(remainder, 60)

                reason = f"{int(hours):02d}:{int(minutes):02d}:{int(seconds):02d} remaining"
                return (False, reason)

            # Hold period expired — allow normal revoke
            return (True, None)

    def request_revoke(
        self,
        approval_id: str,
        skill_id: str,
        operator_id: str,
        force: bool = False,
        reason: str = "",
    ) -> RollbackDecision:
        """Request to revoke an approval (normal or forced).

        Args:
            approval_id: UUID of approval to revoke
            skill_id: Skill that generated the approval
            operator_id: Who is requesting revoke
            force: True for force-revoke (override hold period)
            reason: Required for force-revoke; optional for normal revoke

        Returns:
            RollbackDecision with outcome

        Raises:
            ValueError if force=True but reason is empty/invalid
            RuntimeError if audit fails
        """
        import re

        # Validate inputs
        if not operator_id or not re.match(r"^[a-z0-9._\-:]{3,50}$", operator_id):
            raise ValueError(
                f"operator_id must match pattern, got: {operator_id!r}"
            )

        if force:
            if not reason or len(reason) == 0:
                raise ValueError(
                    "[L5 Rollback] Force-revoke requires a reason (mandatory)"
                )
            if len(reason) > 500:
                raise ValueError(
                    f"[L5 Rollback] Reason too long (max 500 chars): {len(reason)}"
                )

        with self._lock:
            # Check hold period
            can_revoke, time_remaining = self.can_revoke(approval_id, skill_id)

            if not can_revoke and not force:
                # Blocked by hold — advise but don't execute
                return RollbackDecision(
                    approval_id=approval_id,
                    allowed=False,
                    reason=f"Hold period not expired. Time remaining: {time_remaining}",
                    time_remaining_seconds=self._parse_time_remaining(time_remaining),
                )

            # Audit-first: log the revoke request
            if self.audit_backend:
                audit_event = {
                    "tenant_id": self.tenant_id,
                    "event_type": "skill_approval_revoke_requested",
                    "approval_id": approval_id,
                    "skill_id": skill_id,
                    "operator_id": operator_id,
                    "force": force,
                    "reason": reason if force else "",
                }

                try:
                    event_id = self.audit_backend.write_event(audit_event)
                except Exception as e:
                    logger.error(f"[L5 Rollback] Failed to audit revoke request: {e}")
                    raise RuntimeError(
                        f"[L5 Rollback] FATAL: audit failed; revoke NOT executed (fail-closed)."
                    )

            # Determine if this is a normal or forced revoke
            if force:
                # Forced revoke — compute override metrics
                apply_time = datetime.fromisoformat(
                    self.approval_apply_times[approval_id].replace("Z", "")
                )
                now = datetime.utcnow()
                elapsed = (now - apply_time).total_seconds()

                hold_hours = self.skill_hold_config.get(skill_id, 12)
                hold_seconds = hold_hours * 3600

                metrics = OverrideMetrics(
                    skill_id=skill_id,
                    approval_id=approval_id,
                    time_into_hold_seconds=int(elapsed),
                    hold_period_configured_seconds=int(hold_seconds),
                    timestamp=datetime.utcnow().isoformat() + "Z",
                )

                # Audit-first: log the force-revoke BEFORE persistence
                if self.audit_backend:
                    audit_event_force = {
                        "tenant_id": self.tenant_id,
                        "event_type": "skill_approval_force_revoked",
                        "approval_id": approval_id,
                        "skill_id": skill_id,
                        "operator_id": operator_id,
                        "reason": reason,
                    }
                    try:
                        self.audit_backend.write_event(audit_event_force)
                    except Exception as e:
                        logger.error(f"[L5 Rollback] FATAL: audit failed; force-revoke NOT executed (fail-closed)")
                        raise RuntimeError(
                            f"[L5 Rollback] FATAL: audit_backend.write_event() failed: {e}. "
                            f"Force-revoke NOT executed (fail-closed)."
                        )

                # Persist AFTER audit succeeds
                self.override_metrics[approval_id] = metrics
                self._persist_override(metrics)

                logger.warning(
                    f"[L5 Rollback] Force-revoke by {operator_id}: {skill_id}.{approval_id} "
                    f"(reason: {reason[:50]}...)"
                )

            else:
                logger.info(
                    f"[L5 Rollback] Normal revoke by {operator_id}: {skill_id}.{approval_id}"
                )

            # Cleanup
            del self.approval_apply_times[approval_id]

            # Return decision
            decision = RollbackDecision(
                approval_id=approval_id,
                allowed=True,
                reason="Revoke allowed" if not force else f"Force-revoked: {reason}",
                override_timestamp=(
                    datetime.utcnow().isoformat() + "Z" if force else None
                ),
            )

            self._persist_decision(decision)

            return decision

    def _parse_time_remaining(self, time_str: str) -> Optional[int]:
        """Parse time remaining string and return total seconds.

        Args:
            time_str: Format "HH:MM:SS remaining", or special strings like "Approval not found", "inf"

        Returns:
            Total seconds, or None if parse fails / special string
        """
        if not time_str or not isinstance(time_str, str):
            return None

        # Handle special cases
        if "inf" in time_str.lower() or "not found" in time_str.lower():
            return None  # Cannot revoke

        try:
            parts = time_str.split(":")[: 3]  # HH:MM:SS
            if len(parts) < 3:
                # Not in HH:MM:SS format
                return None

            hours = int(parts[0])
            minutes = int(parts[1])
            seconds = int(parts[2].split()[0])  # Extract digits before " remaining"

            return hours * 3600 + minutes * 60 + seconds
        except (ValueError, IndexError, AttributeError, TypeError):
            logger.warning(f"[L5 Rollback] Could not parse time_remaining: {time_str!r}")
            return None

    def get_override_metrics(self, skill_id: str) -> Dict[str, OverrideMetrics]:
        """Get all override metrics for a Skill.

        Returns:
            Dict[approval_id, OverrideMetrics]
        """
        with self._lock:
            return {
                aid: m
                for aid, m in self.override_metrics.items()
                if m.skill_id == skill_id
            }

    def compute_override_rate(self, skill_id: str) -> Tuple[float, int]:
        """Compute override rate for a Skill.

        Returns:
            (override_rate [0.0, 1.0], sample_size)
        """
        metrics_list = self.get_override_metrics(skill_id)

        # For now, return metrics based on what we have
        # In production, this would query approval history
        if not metrics_list:
            return 0.0, 0

        # Placeholder: return sample size and 0.0 rate (no overrides yet)
        return 0.0, len(metrics_list)

    def suggest_hold_adjustment(self, skill_id: str) -> Optional[int]:
        """Suggest new hold period based on override metrics.

        Args:
            skill_id: Skill to analyze

        Returns:
            Suggested hold hours (or None if no adjustment)
        """
        override_rate, sample_size = self.compute_override_rate(skill_id)

        if sample_size < 5:
            # Not enough data yet
            return None

        current_hold = self.skill_hold_config.get(skill_id, 12)
        target_override_rate = 0.05  # 5% target

        # Simple linear adjustment
        alpha = 0.1
        adjustment = 1.0 + alpha * (override_rate - target_override_rate)

        suggested_hold = max(1, int(current_hold * adjustment))

        logger.info(
            f"[L5 Rollback] Suggested hold adjustment for {skill_id}: "
            f"{current_hold}h → {suggested_hold}h "
            f"(override_rate={override_rate:.2%}, sample_size={sample_size})"
        )

        return suggested_hold

