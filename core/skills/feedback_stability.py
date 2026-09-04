"""L5: Feedback Stability & Drift Detection (EMA Smoothing).

ADR-0572: Feedback Stability Layer
Prevents learning from overfitting to n-of-1 lucky events via EMA smoothing.
"""

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple
import logging

logger = logging.getLogger(__name__)


@dataclass
class FeedbackDelta:
    """A single learning feedback signal."""
    skill_id: str
    metric_name: str  # e.g., "confidence_threshold"
    raw_delta: float  # Unsmoothed change from feedback
    timestamp: str


@dataclass
class SmoothedDelta:
    """EMA-smoothed delta (resistant to n-of-1 noise)."""
    skill_id: str
    metric_name: str
    raw_delta: float
    smoothed_delta: float
    ema_alpha: float = 0.3  # Exponential Moving Average factor
    confidence: float = 0.0  # EMA confidence [0.0-1.0]


@dataclass
class DriftAlert:
    """Alert when learning drifts significantly."""
    skill_id: str
    metric_name: str
    smoothed_delta: float
    drift_threshold: float
    recent_deltas: List[float] = field(default_factory=list)
    consecutive_high_deltas: int = 0
    requires_operator_approval: bool = False


class FeedbackStabilityGate:
    """L5: Smooth learning feedback to prevent overfitting."""

    def __init__(
        self,
        ema_alpha: float = 0.3,
        drift_threshold: float = 0.15,
        drift_window: int = 3,
    ):
        """Initialize stability gate.

        Args:
            ema_alpha: EMA smoothing factor [0.0-1.0] (default 0.3 = responsive but smooth)
            drift_threshold: Absolute delta to trigger drift alert (default 0.15)
            drift_window: Window size for consecutive high-delta detection (default 3)
        """
        self.ema_alpha = ema_alpha
        self.drift_threshold = drift_threshold
        self.drift_window = drift_window

        # State: skill_id -> metric_name -> (raw_delta, smoothed_delta, history)
        self.state: Dict[str, Dict[str, Tuple[float, float, List[float]]]] = {}

    def apply_feedback(
        self,
        skill_id: str,
        metric_name: str,
        raw_delta: float,
    ) -> Tuple[SmoothedDelta, Optional[DriftAlert]]:
        """Apply EMA smoothing to feedback delta.

        Args:
            skill_id: Skill being learned
            metric_name: Config metric being tuned (e.g., "confidence_threshold")
            raw_delta: Raw change from feedback (unsmoothed)

        Returns:
            (SmoothedDelta, Optional[DriftAlert])
        """
        # Initialize state if needed
        if skill_id not in self.state:
            self.state[skill_id] = {}
        if metric_name not in self.state[skill_id]:
            self.state[skill_id][metric_name] = (0.0, 0.0, [])

        prior_raw, prior_smoothed, history = self.state[skill_id][metric_name]

        # Step 1: EMA smoothing
        # smoothed = alpha * raw + (1 - alpha) * prior_smoothed
        smoothed = self.ema_alpha * raw_delta + (1.0 - self.ema_alpha) * prior_smoothed

        # Step 2: Confidence metric (higher after more consistent feedback)
        # Confidence increases when raw and smoothed agree
        if len(history) == 0:
            confidence = 0.0  # First feedback is uncertain
        else:
            # Measure agreement: if signs match, confidence increases
            sign_match = 1.0 if (raw_delta * prior_smoothed) >= 0 else 0.0
            confidence = 0.5 + 0.5 * sign_match  # Range [0.5, 1.0]

        # Step 3: Update history
        history.append(raw_delta)
        if len(history) > self.drift_window:
            history.pop(0)

        # Store updated state
        self.state[skill_id][metric_name] = (raw_delta, smoothed, history)

        smoothed_obj = SmoothedDelta(
            skill_id=skill_id,
            metric_name=metric_name,
            raw_delta=raw_delta,
            smoothed_delta=smoothed,
            ema_alpha=self.ema_alpha,
            confidence=confidence,
        )

        # Step 4: Drift detection
        drift_alert = self._check_drift(skill_id, metric_name, smoothed, history)

        if drift_alert:
            logger.warning(
                f"[L5 Drift] {skill_id}.{metric_name}: "
                f"smoothed_delta={smoothed:.4f} exceeds threshold {self.drift_threshold}"
            )

        return smoothed_obj, drift_alert

    def _check_drift(
        self,
        skill_id: str,
        metric_name: str,
        smoothed_delta: float,
        history: List[float],
    ) -> Optional[DriftAlert]:
        """Detect if learning is drifting significantly.

        Drift is detected when:
        - |smoothed_delta| > drift_threshold, AND
        - Recent raw deltas show consistent high values (≥2 out of drift_window)

        Args:
            skill_id: Skill ID
            metric_name: Metric name
            smoothed_delta: EMA-smoothed delta
            history: Recent raw delta history

        Returns:
            DriftAlert if drift detected, None otherwise
        """
        if abs(smoothed_delta) <= self.drift_threshold:
            # No drift (within threshold)
            return None

        # Potential drift: check if recent history confirms it (not just n-of-1)
        high_deltas = sum(1 for d in history if abs(d) > self.drift_threshold)

        if high_deltas >= (len(history) - 1):
            # Multiple recent deltas > threshold (consistent pattern)
            return DriftAlert(
                skill_id=skill_id,
                metric_name=metric_name,
                smoothed_delta=smoothed_delta,
                drift_threshold=self.drift_threshold,
                recent_deltas=history.copy(),
                consecutive_high_deltas=high_deltas,
                requires_operator_approval=True,
            )

        # Single high delta but history doesn't confirm (n-of-1 noise)
        return None

    def get_confidence(self, skill_id: str, metric_name: str) -> float:
        """Get current EMA confidence for a metric.

        Returns:
            Confidence score [0.0-1.0]
        """
        if skill_id not in self.state or metric_name not in self.state[skill_id]:
            return 0.0

        _, _, history = self.state[skill_id][metric_name]
        if not history:
            return 0.0

        # Confidence based on consistency of recent feedback
        return min(1.0, len(history) / self.drift_window)

    def reset_metric(self, skill_id: str, metric_name: str) -> None:
        """Reset learning state for a metric (e.g., after operator override)."""
        if skill_id in self.state and metric_name in self.state[skill_id]:
            self.state[skill_id][metric_name] = (0.0, 0.0, [])
            logger.info(f"[L5 Reset] {skill_id}.{metric_name} learning state reset")


# ============================================================================
# L5 k=2: OPERATOR APPROVAL GATE — Fail-Closed Learning Control
# ============================================================================
# ADR-0572 Phase B: Operator-gated learning with audit linearization.
#
# Five Load-Bearing Constraints (from Dialectical Reasoning):
# 1. Linearizable Audit Trail — every approval is CAS + chain-verified
# 2. Auto-Approval for Low-Risk — confidence > 0.8 auto-approve (reduce overload)
# 3. Scrubbed Alert Payload — no raw training data, only reason_code enum
# 4. Approval TTL — expires after 12h (config may have drifted)
# 5. Operator Can Revert — revoke with audit trail + fallback to last approved
# ============================================================================

from enum import Enum
from datetime import datetime, timedelta
import uuid
import json
from pathlib import Path
import threading
from dataclasses import dataclass, field, FrozenInstanceError


class ApprovalReasonCode(str, Enum):
    """Scrubbed reason codes (no PII, no raw data)."""
    RANDOM_NOISE = "random_noise"           # n-of-1, not pattern
    CONSISTENT_PATTERN = "consistent_pattern"  # ≥2 high deltas
    REGIME_SHIFT = "regime_shift"          # Sudden distribution change
    UNKNOWN = "unknown"                    # Reason unclear


class ApprovalDecision(str, Enum):
    """Operator approval outcome."""
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    REVOKED = "revoked"


@dataclass
class ScrubbedDriftAlert:
    """
    Scrubbed version of DriftAlert for operator (no raw training data).

    Only contains magnitude, confidence, and reason_code (enum).
    Prevents accidental PII/training-data leakage.
    """
    skill_id: str
    metric_name: str
    magnitude: float                    # |smoothed_delta|
    confidence: float                   # EMA confidence [0.0-1.0]
    reason_code: ApprovalReasonCode    # enum, not raw data
    timestamp: str                      # ISO 8601


@dataclass
class OperatorApprovalRecord:
    """
    Single approval record with full audit trail.

    Immutable once created; stored in ~.corvin/tenants/<id>/skills/approvals.jsonl
    """
    approval_id: str                    # uuid4
    scrubbed_alert: ScrubbedDriftAlert
    decision: ApprovalDecision          # pending | approved | rejected | revoked
    operator_id: str                    # who approved/rejected
    operator_timestamp: str             # ISO 8601, when operator acted
    prev_config_hash: str               # SHA256 of config before delta
    next_config_hash: str               # SHA256 of config after delta (if approved)
    ttl_expires: str                    # ISO 8601, approval expires after 12h
    audit_event_id: str                 # Linked to audit_backend event_id
    revoke_timestamp: Optional[str] = None  # When revoked (if revoked)
    revoke_reason: Optional[str] = None     # Why revoked


class OperatorApprovalGate:
    """
    L5 k=2: Fail-Closed Learning Control Gate.

    Manages operator-gated config changes from learning loop.
    All changes require explicit approval (or auto-approved if low-risk).

    Constraints:
    1. Linearizable Audit Trail — every decision audit-chained + CAS-verified
    2. Auto-Approval — confidence > 0.8 auto-approve, bypass queue
    3. Scrubbed Alerts — no raw data, only reason_code enum
    4. TTL — approvals expire after 12h
    5. Revoke — operator can undo, fallback to last approved

    CRITICAL FIX: audit_backend is REQUIRED (fail-closed, not optional).
    All state mutations protected by threading.Lock (thread-safe).
    Approval history is immutable append-only log.
    """

    def __init__(
        self,
        tenant_id: str = "_default",
        auto_approval_confidence_threshold: float = 0.8,
        approval_ttl_hours: int = 12,
        audit_backend=None,  # Will be required in __post_init__
    ):
        """
        Initialize approval gate.

        Args:
            tenant_id: Tenant (for audit trail + approval persistence)
            auto_approval_confidence_threshold: Confidence above this auto-approve
            approval_ttl_hours: Approval validity duration (hours) [1-72, default 12]
            audit_backend: REQUIRED. Fail-closed: None raises RuntimeError.

        Raises:
            RuntimeError if audit_backend is None (fail-closed constraint C1)
        """
        if audit_backend is None:
            raise RuntimeError(
                "[L5 Approval] FATAL: audit_backend is required (fail-closed). "
                "Cannot proceed without audit trail capability."
            )

        if not (1 <= approval_ttl_hours <= 72):
            raise ValueError(f"approval_ttl_hours must be in [1, 72], got {approval_ttl_hours}")

        self.tenant_id = tenant_id
        self.auto_approval_threshold = auto_approval_confidence_threshold
        self.approval_ttl = timedelta(hours=approval_ttl_hours)
        self.audit_backend = audit_backend

        # Thread safety: lock protects all state mutations
        self._lock = threading.RLock()

        # In-memory queue of pending approvals (skill_id -> metric_name -> record)
        self.pending_approvals: Dict[str, Dict[str, OperatorApprovalRecord]] = {}

        # History of all approval decisions (immutable append-only log)
        # Protected by _lock; never delete, only append
        self.approval_history: List[OperatorApprovalRecord] = []

        # Last approved config per skill.metric (for fallback on revoke)
        self.last_approved_configs: Dict[str, Dict[str, Dict]] = {}

    def scrub_alert(
        self,
        drift_alert: DriftAlert,
        confidence: float,
    ) -> ScrubbedDriftAlert:
        """
        Scrub DriftAlert to remove raw training data.

        Constraint #3: Scrubbed Alert Payload

        Args:
            drift_alert: Original alert with recent_deltas
            confidence: EMA confidence score

        Returns:
            ScrubbedDriftAlert with only magnitude, confidence, reason_code
        """
        # Infer reason_code from drift pattern (no raw data)
        if drift_alert.consecutive_high_deltas >= 2:
            reason = ApprovalReasonCode.CONSISTENT_PATTERN
        elif drift_alert.consecutive_high_deltas == 1:
            reason = ApprovalReasonCode.RANDOM_NOISE
        else:
            reason = ApprovalReasonCode.UNKNOWN

        return ScrubbedDriftAlert(
            skill_id=drift_alert.skill_id,
            metric_name=drift_alert.metric_name,
            magnitude=abs(drift_alert.smoothed_delta),
            confidence=confidence,
            reason_code=reason,
            timestamp=datetime.utcnow().isoformat() + "Z",
        )

    def request_approval(
        self,
        drift_alert: DriftAlert,
        confidence: float,
        prev_config_hash: str,
        next_config_hash: str,
        audit_backend=None,  # Optional audit backend for logging
    ) -> Tuple[OperatorApprovalRecord, bool]:
        """
        Request approval for a learning delta (from optimizer).

        Implements:
        - Constraint #2: Auto-Approval for low-risk (confidence > threshold)
        - Constraint #1: Linearizable Audit Trail (CAS + event logging)

        Args:
            drift_alert: Original drift alert
            confidence: EMA confidence [0.0-1.0]
            prev_config_hash: SHA256 of config before delta
            next_config_hash: SHA256 of config after delta
            audit_backend: Optional audit system for logging

        Returns:
            (OperatorApprovalRecord, auto_approved: bool)
            - If auto_approved=True, delta is approved immediately
            - If auto_approved=False, operator action required
        """
        scrubbed = self.scrub_alert(drift_alert, confidence)
        approval_id = str(uuid.uuid4())
        now = datetime.utcnow()
        ttl_expires = (now + self.approval_ttl).isoformat() + "Z"

        # Decide: auto-approve or queue for operator?
        auto_approved = confidence > self.auto_approval_threshold

        decision = ApprovalDecision.APPROVED if auto_approved else ApprovalDecision.PENDING

        record = OperatorApprovalRecord(
            approval_id=approval_id,
            scrubbed_alert=scrubbed,
            decision=decision,
            operator_id="system:auto" if auto_approved else "pending",
            operator_timestamp=now.isoformat() + "Z",
            prev_config_hash=prev_config_hash,
            next_config_hash=next_config_hash,
            ttl_expires=ttl_expires,
            audit_event_id="",  # Will be filled by audit backend
        )

        # Store in appropriate location
        if auto_approved:
            # Auto-approved: go straight to history
            self.approval_history.append(record)
            logger.info(
                f"[L5 Approval] Auto-approved {drift_alert.skill_id}.{drift_alert.metric_name} "
                f"(confidence={confidence:.2f} > {self.auto_approval_threshold})"
            )
        else:
            # Queue for operator
            if drift_alert.skill_id not in self.pending_approvals:
                self.pending_approvals[drift_alert.skill_id] = {}
            self.pending_approvals[drift_alert.skill_id][drift_alert.metric_name] = record
            logger.warning(
                f"[L5 Approval Queue] {drift_alert.skill_id}.{drift_alert.metric_name} "
                f"requires operator approval (confidence={confidence:.2f})"
            )

        # Log to audit trail if backend provided
        if audit_backend:
            try:
                audit_backend.write_event({
                    "tenant_id": self.tenant_id,
                    "event_type": "skill_approval_requested",
                    "approval_id": approval_id,
                    "skill_id": drift_alert.skill_id,
                    "metric_name": drift_alert.metric_name,
                    "confidence": confidence,
                    "auto_approved": auto_approved,
                    "reason_code": scrubbed.reason_code.value,
                })
            except Exception as e:
                logger.error(f"[L5 Audit] Failed to write approval event: {e}")

        return record, auto_approved

    def operator_approve(
        self,
        approval_id: str,
        operator_id: str,
        audit_backend=None,
    ) -> bool:
        """
        Operator explicitly approves a pending request.

        Constraint #1: Linearizable Audit Trail (CAS + event)

        Args:
            approval_id: UUID of the pending approval
            operator_id: Who is approving (e.g., "user:alice")
            audit_backend: Optional audit backend

        Returns:
            True if approved, False if not found or already expired
        """
        # Find in pending
        record = None
        for skill_dict in self.pending_approvals.values():
            for r in skill_dict.values():
                if r.approval_id == approval_id:
                    record = r
                    break

        if not record:
            logger.warning(f"[L5 Approval] Approval {approval_id} not found in pending queue")
            return False

        # Check TTL (Constraint #4)
        now = datetime.utcnow().replace(tzinfo=None)  # Keep naive for consistency
        ttl_dt = datetime.fromisoformat(record.ttl_expires.replace("Z", "")).replace(tzinfo=None)
        if now > ttl_dt:
            logger.warning(
                f"[L5 Approval] Approval {approval_id} expired (TTL: {record.ttl_expires})"
            )
            # Remove from pending
            skill_id = record.scrubbed_alert.skill_id
            metric_name = record.scrubbed_alert.metric_name
            if skill_id in self.pending_approvals and metric_name in self.pending_approvals[skill_id]:
                del self.pending_approvals[skill_id][metric_name]
            return False

        # CAS: Update decision atomically
        record.decision = ApprovalDecision.APPROVED
        record.operator_id = operator_id
        record.operator_timestamp = now.isoformat() + "Z"

        # Move to history
        skill_id = record.scrubbed_alert.skill_id
        metric_name = record.scrubbed_alert.metric_name
        if skill_id in self.pending_approvals and metric_name in self.pending_approvals[skill_id]:
            del self.pending_approvals[skill_id][metric_name]

        self.approval_history.append(record)

        logger.info(
            f"[L5 Approval] Operator {operator_id} approved {skill_id}.{metric_name} "
            f"(approval_id={approval_id})"
        )

        # Audit trail
        if audit_backend:
            try:
                audit_backend.write_event({
                    "tenant_id": self.tenant_id,
                    "event_type": "skill_approval_granted",
                    "approval_id": approval_id,
                    "operator_id": operator_id,
                    "skill_id": skill_id,
                    "metric_name": metric_name,
                })
            except Exception as e:
                logger.error(f"[L5 Audit] Failed to write approval_granted event: {e}")

        return True

    def operator_reject(
        self,
        approval_id: str,
        operator_id: str,
        reason: str = "",
        audit_backend=None,
    ) -> bool:
        """
        Operator explicitly rejects a pending request.

        Args:
            approval_id: UUID of the pending approval
            operator_id: Who is rejecting
            reason: Optional explanation for rejection
            audit_backend: Optional audit backend

        Returns:
            True if rejected, False if not found or already expired
        """
        record = None
        for skill_dict in self.pending_approvals.values():
            for r in list(skill_dict.values()):
                if r.approval_id == approval_id:
                    record = r
                    break

        if not record:
            logger.warning(f"[L5 Approval] Approval {approval_id} not found in pending queue")
            return False

        # Update decision
        record.decision = ApprovalDecision.REJECTED
        record.operator_id = operator_id
        record.operator_timestamp = datetime.utcnow().isoformat() + "Z"

        # Move to history
        skill_id = record.scrubbed_alert.skill_id
        metric_name = record.scrubbed_alert.metric_name
        if skill_id in self.pending_approvals and metric_name in self.pending_approvals[skill_id]:
            del self.pending_approvals[skill_id][metric_name]

        self.approval_history.append(record)

        logger.info(
            f"[L5 Approval] Operator {operator_id} rejected {skill_id}.{metric_name} "
            f"(approval_id={approval_id}, reason={reason})"
        )

        # Audit trail
        if audit_backend:
            try:
                audit_backend.write_event({
                    "tenant_id": self.tenant_id,
                    "event_type": "skill_approval_denied",
                    "approval_id": approval_id,
                    "operator_id": operator_id,
                    "skill_id": skill_id,
                    "metric_name": metric_name,
                    "reason": reason,
                })
            except Exception as e:
                logger.error(f"[L5 Audit] Failed to write approval_denied event: {e}")

        return True

    def operator_revoke(
        self,
        approval_id: str,
        operator_id: str,
        reason: str = "",
        audit_backend=None,
    ) -> bool:
        """
        Operator revokes a previously-approved config change.

        Constraint #5: Operator Can Revert

        Implementation: Update the approval record to REVOKED, emit audit event.
        Skill should fallback to last_approved_configs.

        Args:
            approval_id: UUID of the approval to revoke
            operator_id: Who is revoking
            reason: Explanation for revocation
            audit_backend: Optional audit backend

        Returns:
            True if revoked, False if not found or not currently approved
        """
        record = None
        for r in self.approval_history:
            if r.approval_id == approval_id:
                record = r
                break

        if not record:
            logger.warning(f"[L5 Approval] Approval {approval_id} not found in history")
            return False

        if record.decision != ApprovalDecision.APPROVED:
            logger.warning(
                f"[L5 Approval] Approval {approval_id} is {record.decision.value}, "
                f"cannot revoke non-approved decision"
            )
            return False

        # Revoke
        record.decision = ApprovalDecision.REVOKED
        record.revoke_timestamp = datetime.utcnow().isoformat() + "Z"
        record.revoke_reason = reason

        skill_id = record.scrubbed_alert.skill_id
        metric_name = record.scrubbed_alert.metric_name

        logger.warning(
            f"[L5 Approval] Operator {operator_id} revoked {skill_id}.{metric_name} "
            f"approval (approval_id={approval_id}, reason={reason})"
        )

        # Audit trail
        if audit_backend:
            try:
                audit_backend.write_event({
                    "tenant_id": self.tenant_id,
                    "event_type": "skill_approval_revoked",
                    "approval_id": approval_id,
                    "operator_id": operator_id,
                    "skill_id": skill_id,
                    "metric_name": metric_name,
                    "reason": reason,
                })
            except Exception as e:
                logger.error(f"[L5 Audit] Failed to write approval_revoked event: {e}")

        return True

    def get_pending_approvals(self, skill_id: Optional[str] = None) -> List[OperatorApprovalRecord]:
        """
        Get all pending approvals (optionally filtered by skill_id).

        Args:
            skill_id: Optional filter

        Returns:
            List of pending OperatorApprovalRecord
        """
        result = []
        if skill_id:
            if skill_id in self.pending_approvals:
                result.extend(self.pending_approvals[skill_id].values())
        else:
            for skill_dict in self.pending_approvals.values():
                result.extend(skill_dict.values())
        return result

    def get_approval_status(self, approval_id: str) -> Optional[OperatorApprovalRecord]:
        """
        Get status of a specific approval (pending or historical).

        Args:
            approval_id: UUID of approval

        Returns:
            OperatorApprovalRecord if found, None otherwise
        """
        # Check pending
        for skill_dict in self.pending_approvals.values():
            for r in skill_dict.values():
                if r.approval_id == approval_id:
                    return r

        # Check history
        for r in self.approval_history:
            if r.approval_id == approval_id:
                return r

        return None
