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
import re

from .utils import (
    format_iso_timestamp,
    parse_iso_timestamp,
    format_time_remaining,
    parse_time_remaining_string,
)

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

        # Per-Skill criticality (inferred or explicit)
        self.skill_criticality: Dict[str, Criticality] = {}  # skill_id -> Criticality

        # Approval tracking: approval_id -> (apply_timestamp, hold_hours)
        # Changed from Dict[str, str] to include hold hours with each approval
        # This prevents multiple approvals for same skill from overwriting hold periods
        self.approval_apply_times: Dict[str, Tuple[str, int]] = {}

        # Track total approval count per skill for override rate calculation
        self.approval_count_by_skill: Dict[str, int] = {}

        # Override metrics for learning
        self.override_metrics: Dict[str, OverrideMetrics] = {}

        # Hold period configuration per Skill (updated via suggest_hold_adjustment)
        self.skill_hold_config: Dict[str, int] = {}

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
                        # Reconstruct approval apply times from persisted history
                        elif data.get("type") == "approval_registered":
                            approval_id = data.get("approval_id", "")
                            if approval_id:
                                apply_timestamp = data.get("apply_timestamp", "")
                                hold_hours = data.get("hold_hours", 12)
                                self.approval_apply_times[approval_id] = (apply_timestamp, hold_hours)
                                # Latest effective hold per skill (the attribute existed but was never written)
                                self.skill_hold_config[skill_id] = hold_hours
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
                    "timestamp": format_iso_timestamp(),
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
            # Store criticality
            self.skill_criticality[skill_id] = criticality

            # Determine hold period (custom or default)
            hold_hours = custom_hold_hours if custom_hold_hours is not None else DEFAULT_HOLD_HOURS[criticality]

            # Record approval with its hold period (prevents overwrites for multiple approvals)
            apply_timestamp = format_iso_timestamp()
            self.approval_apply_times[approval_id] = (apply_timestamp, hold_hours)

            # Track total approval count per skill (for override rate calculation)
            self.approval_count_by_skill[skill_id] = self.approval_count_by_skill.get(skill_id, 0) + 1

            # Persist approval registration to disk (fail-closed: log error but continue)
            try:
                self.history_file.parent.mkdir(parents=True, exist_ok=True)
                with open(self.history_file, "a") as f:
                    record = {
                        "type": "approval_registered",
                        "approval_id": approval_id,
                        "skill_id": skill_id,
                        "apply_timestamp": apply_timestamp,
                        "hold_hours": hold_hours,
                        "timestamp": apply_timestamp,
                    }
                    f.write(json.dumps(record) + "\n")
            except Exception as e:
                logger.error(f"[L5 Rollback] Failed to persist approval registration: {e}")

            logger.info(
                f"[L5 Rollback] Registered {approval_id} for {skill_id} "
                f"(hold={hold_hours}h)"
            )

    def can_revoke(
        self, approval_id: str, skill_id: str
    ) -> Tuple[bool, Optional[timedelta]]:
        """Check if an approval can be revoked (normal, non-forced).

        Returns:
            (allowed, time_remaining)
            - If allowed: (True, None)
            - If blocked by hold: (False, timedelta_until_allowed)
        """
        with self._lock:
            # Check if approval exists
            if approval_id not in self.approval_apply_times:
                return (False, None)  # Approval not found — cannot revoke

            # Get approval timestamp and hold hours
            apply_timestamp, hold_hours = self.approval_apply_times[approval_id]

            # Compute elapsed time (with error handling for malformed timestamps)
            try:
                apply_time = parse_iso_timestamp(apply_timestamp)
            except ValueError as e:
                logger.warning(f"[L5 Rollback] Malformed timestamp for {approval_id}: {e}; cannot revoke yet")
                # Graceful degradation: return False with large remaining time
                return (False, timedelta(days=365))

            now = datetime.utcnow()
            elapsed = now - apply_time

            hold_period = timedelta(hours=hold_hours)

            if elapsed < hold_period:
                # Still in hold period
                remaining = hold_period - elapsed
                return (False, remaining)

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
            is_allowed, time_remaining = self.can_revoke(approval_id, skill_id)

            if not is_allowed and not force:
                # Blocked by hold — advise but don't execute
                time_remaining_secs = None
                reason_msg = "Hold period not expired."
                if time_remaining:
                    reason_msg += f" Time remaining: {format_time_remaining(time_remaining)}"
                    time_remaining_secs = int(time_remaining.total_seconds())

                return RollbackDecision(
                    approval_id=approval_id,
                    allowed=False,
                    reason=reason_msg,
                    time_remaining_seconds=time_remaining_secs,
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
                # Guard: verify approval exists before accessing
                if approval_id not in self.approval_apply_times:
                    return RollbackDecision(
                        approval_id=approval_id,
                        allowed=False,
                        reason="Approval not found; cannot force-revoke",
                    )
                apply_timestamp, hold_hours = self.approval_apply_times[approval_id]
                apply_time = parse_iso_timestamp(apply_timestamp)
                now = datetime.utcnow()
                elapsed = (now - apply_time).total_seconds()
                hold_seconds = hold_hours * 3600

                metrics = OverrideMetrics(
                    skill_id=skill_id,
                    approval_id=approval_id,
                    time_into_hold_seconds=int(elapsed),
                    hold_period_configured_seconds=int(hold_seconds),
                    timestamp=format_iso_timestamp(),
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
                override_timestamp=format_iso_timestamp() if force else None,
            )

            self._persist_decision(decision)

            return decision

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

        Override rate = (count of overrides before hold expired) / total_approvals

        Returns:
            (override_rate [0.0, 1.0], sample_size)
        """
        metrics_list = self.get_override_metrics(skill_id)
        total_approvals = self.approval_count_by_skill.get(skill_id, 0)

        if total_approvals == 0:
            return 0.0, 0

        # Count overrides that happened before hold period expired
        early_overrides = 0
        for approval_id, metrics in metrics_list.items():
            # If time_into_hold_seconds < hold_period_configured_seconds,
            # then the override happened before hold expired
            if metrics.time_into_hold_seconds < metrics.hold_period_configured_seconds:
                early_overrides += 1

        override_rate = min(1.0, early_overrides / max(1, total_approvals))
        return override_rate, total_approvals

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

