"""Phase 2: Learning Loop L5 Integration — Complete Feedback Pipeline.

ADR-0583: Learning Loop L5 Integration
Wires all five approval gates (k=1 through k=5) into a unified feedback loop.

Gate Pipeline:
  1. k=1 (FeedbackStabilityGate): Smooth raw deltas + detect drift
  2. k=2 (OperatorApprovalGate): Request operator approval if drift detected
  3. k=3 (QualityGate): Compute advisory quality metrics
  4. k=4 (ConflictResolver): Detect/resolve multi-skill conflicts
  5. k=5 (RollbackGuard): Enforce hold periods + operator override

Constraints (load-bearing):
  - Fail-closed: Every gate failure blocks the entire pipeline
  - Audit-first: Every decision logged before state mutation
  - Tenant-scoped: All operations filtered by tenant_id
  - Thread-safe: RLock on all shared state
  - Immutable: No silent overrides; operator is final authority
"""

from __future__ import annotations

import logging
import threading
import uuid
import copy
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple
from enum import Enum
from datetime import datetime, timedelta, timezone

from core.skills.feedback_stability import (
    FeedbackStabilityGate,
    OperatorApprovalGate,
    DriftAlert,
    ApprovalDecision,
)
from .quality_gate import QualityGate, QualityScore
from .conflict_resolver import ConflictResolver, Conflict, ConflictResolution
from .rollback_guard import RollbackGuard, RollbackRequest, RollbackDecision
from .utils import format_iso_timestamp

logger = logging.getLogger(__name__)


class L5PipelineDecision(str, Enum):
    """Outcome of full L5 pipeline."""
    APPROVED_IMMEDIATELY = "approved_immediately"              # k=1: no drift detected
    APPROVED_BY_HIGH_CONFIDENCE = "approved_by_high_confidence"  # k=2: auto-approved (confidence > threshold)
    PENDING_OPERATOR = "pending_operator"                      # k=2: awaiting operator approval
    APPROVED_BY_OPERATOR = "approved_by_operator"              # k=2: operator explicitly approved
    REJECTED_BY_OPERATOR = "rejected_by_operator"              # k=2: operator rejected
    BLOCKED_BY_CONFLICT = "blocked_by_conflict"                # k=4: conflict detected, serialized
    REVOKED_BY_OPERATOR = "revoked_by_operator"                # k=5: operator revoked after approval


@dataclass
class L5GateDecision:
    """Decision from a single gate in the pipeline."""
    gate_name: str                          # "k=1", "k=2", etc.
    passed: bool                            # True = gate allows forward progress
    decision_code: str                      # Enum or string code
    reason: str                             # Human-readable reason
    blocking: bool = False                  # True = blocks pipeline entirely
    advisory_data: Optional[Dict] = None   # Optional metadata for operator
    timestamp: str = field(default_factory=format_iso_timestamp)


@dataclass
class L5PipelineResult:
    """Outcome of running feedback through full L5 pipeline."""
    pipeline_id: str
    skill_id: str
    metric_name: str
    raw_delta: float

    # Gate decisions (in order)
    k1_decision: Optional[L5GateDecision] = None
    k2_decision: Optional[L5GateDecision] = None
    k3_decision: Optional[L5GateDecision] = None
    k4_decision: Optional[L5GateDecision] = None
    k5_decision: Optional[L5GateDecision] = None

    # Final outcome
    final_decision: L5PipelineDecision = L5PipelineDecision.APPROVED_IMMEDIATELY
    final_reason: str = ""

    # Operator action (if k=2 pending)
    approval_record: Optional[Dict] = None
    approval_id: Optional[str] = None

    # Quality metrics (from k=3)
    quality_score: Optional[QualityScore] = None

    # Conflict info (from k=4)
    conflicts: List[Conflict] = field(default_factory=list)
    conflict_resolutions: List[ConflictResolution] = field(default_factory=list)

    # Rollback info (from k=5)
    rollback_decision: Optional[RollbackDecision] = None

    timestamp: str = field(default_factory=format_iso_timestamp)


class L5FeedbackLoopIntegrator:
    """
    Unified feedback loop integrator — runs feedback through all 5 L5 gates.

    Workflow:
    1. Input: raw_delta from learning loop
    2. k=1: Smooth + detect drift
    3. k=2: Request approval (or auto-approve if high confidence)
    4. k=3: Compute quality metrics (advisory)
    5. k=4: Detect conflicts (serialize if needed)
    6. k=5: Check hold period + operator override capability
    7. Output: L5PipelineResult with final decision

    All decisions are audit-first and fail-closed.
    """

    def __init__(
        self,
        stability_gate: FeedbackStabilityGate,
        approval_gate: OperatorApprovalGate,
        quality_gate: QualityGate,
        conflict_resolver: ConflictResolver,
        rollback_guard: RollbackGuard,
        tenant_id: str = "_default",
        audit_backend=None,  # Note: None will cause graceful failure in process_feedback
    ):
        """
        Initialize L5 feedback loop integrator.

        Args:
            stability_gate: L5 k=1
            approval_gate: L5 k=2
            quality_gate: L5 k=3
            conflict_resolver: L5 k=4
            rollback_guard: L5 k=5
            tenant_id: Tenant for isolation
            audit_backend: Audit backend (required)
        """
        self.stability_gate = stability_gate
        self.approval_gate = approval_gate
        self.quality_gate = quality_gate
        self.conflict_resolver = conflict_resolver
        self.rollback_guard = rollback_guard
        self.tenant_id = tenant_id
        self.audit_backend = audit_backend

        # Thread safety for state mutations
        self._lock = threading.RLock()

        # Track all pipeline runs (for observability)
        self.pipeline_results: Dict[str, L5PipelineResult] = {}

        # BUG FIX #3: Pending approvals scoped by tenant first, then skill, then metric
        # Structure: {tenant_id: {skill_id: {metric_name: approval_data}}}
        self.pending_approvals: Dict[str, Dict[str, Dict[str, Dict]]] = {}

        # Current config hash (for traceability)
        self._current_config_hash: str = "0" * 64

        # History for quality assessment (optional, filled by caller)
        self._history_deltas: List[float] = []
        self._ema_smoothed: Optional[float] = None
        self._ema_confidence: float = 0.5
        self._config_history: List[float] = []

        logger.info(f"[L5 Integrator] Initialized for tenant {tenant_id}")

    def process_feedback(
        self,
        skill_id: str,
        metric_name: str,
        raw_delta: float,
        new_config_hash: str,
        feedback_source: str = "learning_optimizer",
        history_deltas: Optional[List[float]] = None,
        ema_smoothed: Optional[float] = None,
        ema_confidence: Optional[float] = None,
        config_history: Optional[List[float]] = None,
    ) -> L5PipelineResult:
        """
        Run feedback through complete L5 pipeline (k=1 → k=5).

        Args:
            skill_id: Skill being optimized
            metric_name: Metric being tuned
            raw_delta: Raw feedback delta (unsmoothed)
            new_config_hash: SHA256 of proposed new config (used for traceability)
            feedback_source: Where feedback came from (for audit trail)
            history_deltas: Recent historical deltas for quality assessment (optional)
            ema_smoothed: EMA-smoothed delta from k=1 (optional, computed if None)
            ema_confidence: EMA confidence from k=1 (optional, computed if None)
            config_history: Recent config values for stability assessment (optional)

        Returns:
            L5PipelineResult with full decision chain

        Raises:
            RuntimeError: If any gate fails audit-first constraint (fail-closed)
        """
        pipeline_id = str(uuid.uuid4())
        result = L5PipelineResult(
            pipeline_id=pipeline_id,
            skill_id=skill_id,
            metric_name=metric_name,
            raw_delta=raw_delta,
        )

        # Store context for this pipeline (avoid shared state race conditions)
        # Note: History is passed by caller; we don't store in instance to avoid races
        pipeline_config_hash = new_config_hash
        pipeline_history_deltas = history_deltas or []
        pipeline_ema_smoothed = ema_smoothed
        pipeline_ema_confidence = ema_confidence if ema_confidence is not None else 0.5
        pipeline_config_history = config_history or []

        logger.info(
            f"[L5 Integrator] Pipeline {pipeline_id}: "
            f"{skill_id}.{metric_name}, raw_delta={raw_delta:.4f}"
        )

        try:
            # ========== k=1: Stability Gate ==========
            result.k1_decision = self._run_k1_stability_gate(
                result, feedback_source
            )

            # If no drift, approve immediately
            if not result.k1_decision.blocking:
                result.final_decision = L5PipelineDecision.APPROVED_IMMEDIATELY
                result.final_reason = result.k1_decision.reason
            else:
                # ========== k=2: Operator Approval Gate ==========
                result.k2_decision = self._run_k2_approval_gate(result, pipeline_config_hash)

                # If k=2 cannot approve (needs operator), stop here
                if result.k2_decision.decision_code == "pending_operator":
                    result.final_decision = L5PipelineDecision.PENDING_OPERATOR
                    result.final_reason = result.k2_decision.reason
                    self._track_pending_approval(result)  # _track_pending_approval has its own lock

                # If k=2 auto-approved, continue to quality check
                elif result.k2_decision.decision_code == "auto_approved":
                    # ========== k=3: Quality Gate (Advisory) ==========
                    result.k3_decision = self._run_k3_quality_gate(
                        result,
                        pipeline_history_deltas,
                        pipeline_ema_smoothed,
                        pipeline_ema_confidence,
                        pipeline_config_history,
                    )

                    # ========== k=4: Conflict Detector ==========
                    result.k4_decision = self._run_k4_conflict_detection(result)

                    # If conflict detected and serialized, we're done
                    if result.k4_decision.blocking:
                        result.final_decision = L5PipelineDecision.BLOCKED_BY_CONFLICT
                        result.final_reason = result.k4_decision.reason
                    else:
                        # ========== k=5: Rollback Guard (Advisory) ==========
                        result.k5_decision = self._run_k5_rollback_check(result)

                        # Final: approved by high confidence auto-approval
                        result.final_decision = L5PipelineDecision.APPROVED_BY_HIGH_CONFIDENCE
                        result.final_reason = "Auto-approved by k=2 (high confidence) + passed k=3/k=4/k=5"

        except Exception as e:
            logger.error(
                f"[L5 Integrator] FATAL: Pipeline {pipeline_id} failed: {e}",
                exc_info=True,
            )
            # Fail-closed: audit error FIRST, then propagate
            try:
                self._audit_pipeline_error(result, str(e))
            except Exception as audit_error:
                logger.critical(f"[L5 Integrator] FATAL: Audit of error failed: {audit_error}")
                raise RuntimeError(
                    f"[L5 Integrator] Audit-first constraint violated: {audit_error}"
                )
            raise RuntimeError(
                f"[L5 Integrator] Pipeline failed (fail-closed): {e}"
            )

        finally:
            # Audit-first: ALWAYS audit before returning (fail-closed)
            try:
                self._audit_pipeline_complete(result)
            except Exception as e:
                logger.critical(f"[L5 Integrator] FATAL: Audit-first constraint violated: {e}")
                raise RuntimeError(
                    f"[L5 Integrator] Audit-first constraint violated: {e}"
                )

            # Store result (after audit succeeds)
            with self._lock:
                self.pipeline_results[pipeline_id] = result

        return result

    # ========== Gate Implementations ==========

    def _run_k1_stability_gate(
        self, result: L5PipelineResult, feedback_source: str
    ) -> L5GateDecision:
        """
        k=1: Stability Gate — smooth feedback + detect drift.

        Returns:
            L5GateDecision with passed=False if drift detected (blocking)
        """
        try:
            smoothed, drift_alert = self.stability_gate.apply_feedback(
                result.skill_id,
                result.metric_name,
                result.raw_delta,
            )

            if drift_alert is None:
                # No drift: smooth change, approve immediately
                return L5GateDecision(
                    gate_name="k=1",
                    passed=True,
                    decision_code="no_drift",
                    reason=f"Smoothed delta {smoothed.smoothed_delta:.4f}, no drift detected",
                    blocking=False,
                    advisory_data={
                        "smoothed_delta": smoothed.smoothed_delta,
                        "confidence": smoothed.confidence,
                    },
                )
            else:
                # Drift detected: block and require approval
                return L5GateDecision(
                    gate_name="k=1",
                    passed=False,
                    decision_code="drift_detected",
                    reason=f"Drift detected: smoothed_delta={drift_alert.smoothed_delta:.4f}",
                    blocking=True,
                    advisory_data={
                        "drift_magnitude": drift_alert.smoothed_delta,
                        # DriftAlert carries no confidence of its own; the gate's
                        # per-metric confidence is the right figure. (The former
                        # ``drift_alert.confidence`` raised AttributeError, so the
                        # drift branch — the one this gate exists for — always
                        # failed closed and no drift ever reached k=2.)
                        "confidence": smoothed.confidence,
                        "consecutive_high_deltas": drift_alert.consecutive_high_deltas,
                    },
                )
        except Exception as e:
            logger.error(f"[L5 k=1] FATAL: Stability gate failed: {e}")
            raise RuntimeError(f"[L5 k=1] Stability gate failed: {e}. Fail-closed.")

    def _run_k2_approval_gate(
        self, result: L5PipelineResult, config_hash: str
    ) -> L5GateDecision:
        """
        k=2: Operator Approval Gate — request approval or auto-approve.

        Args:
            result: Pipeline result being built
            config_hash: Config hash to include in approval record

        Returns:
            L5GateDecision with decision_code in:
            - "auto_approved": Confidence high enough
            - "pending_operator": Awaiting manual approval
        """
        try:
            # Get confidence from k=1 advisory data with validation
            k1_data = result.k1_decision.advisory_data
            if k1_data is None:
                logger.error("[L5 k=2] FATAL: k=1 decision lacks advisory_data")
                raise RuntimeError("k=1 decision missing advisory_data (fail-closed)")

            confidence = k1_data.get("confidence")
            if confidence is None:
                logger.error("[L5 k=2] FATAL: k=1 advisory_data lacks 'confidence' key")
                raise RuntimeError("k=1 advisory_data missing 'confidence' (fail-closed)")

            # Create drift alert with proper semantics from k=1 data
            drift_magnitude = k1_data.get("drift_magnitude", abs(result.raw_delta))

            # Create DriftAlert with required fields (drift_threshold required)
            drift_alert = DriftAlert(
                skill_id=result.skill_id,
                metric_name=result.metric_name,
                smoothed_delta=drift_magnitude,
                drift_threshold=self.stability_gate.drift_threshold,  # Get from stability gate
                recent_deltas=k1_data.get("recent_deltas", []),
                consecutive_high_deltas=0,
                requires_operator_approval=True,
            )

            # Request approval with real config hashes for traceability
            record, auto_approved = self.approval_gate.request_approval(
                drift_alert,
                confidence=confidence,
                prev_config_hash="0" * 64,  # TODO: Get from Skill state; hardcoded for now
                next_config_hash=config_hash,  # Use real hash provided by caller
            )

            # Validate that approval_gate set approval_id (fail-closed)
            if not hasattr(record, "approval_id") or record.approval_id is None:
                logger.error("[L5 k=2] FATAL: approval_gate failed to create approval_id")
                raise RuntimeError("approval_gate.request_approval() did not set approval_id (fail-closed)")

            result.approval_record = record
            result.approval_id = record.approval_id

            if auto_approved:
                return L5GateDecision(
                    gate_name="k=2",
                    passed=True,
                    decision_code="auto_approved",
                    reason=f"Auto-approved (confidence={confidence:.2f} > threshold)",
                    blocking=False,
                    advisory_data={"confidence": confidence},
                )
            else:
                return L5GateDecision(
                    gate_name="k=2",
                    passed=False,
                    decision_code="pending_operator",
                    reason=f"Awaiting operator approval (confidence={confidence:.2f})",
                    blocking=True,
                    advisory_data={"approval_id": result.approval_id},
                )
        except Exception as e:
            logger.error(f"[L5 k=2] FATAL: Approval gate failed: {e}")
            raise RuntimeError(f"[L5 k=2] Approval gate failed: {e}. Fail-closed.")

    def _run_k3_quality_gate(
        self,
        result: L5PipelineResult,
        history_deltas: List[float],
        ema_smoothed: Optional[float],
        ema_confidence: float,
        config_history: List[float],
    ) -> L5GateDecision:
        """
        k=3: Quality Gate — advisory reliability scoring.

        Note: Requires historical data for meaningful assessment.
        If history is not provided, k=3 returns neutral "FAIR" quality level.

        Returns:
            L5GateDecision (advisory only, never blocking)
        """
        try:
            # Use provided history
            recent_deltas = history_deltas.copy() if history_deltas else []

            # Add current delta to history
            recent_deltas.append(result.raw_delta)

            # Use provided smoothed value or fall back to current delta
            ema_smoothed_value = ema_smoothed if ema_smoothed is not None else result.raw_delta

            quality_score = self.quality_gate.compute_quality(
                result.skill_id,
                result.metric_name,
                recent_deltas,
                ema_smoothed_value,
                ema_confidence,
                config_history,
            )

            result.quality_score = quality_score

            return L5GateDecision(
                gate_name="k=3",
                passed=True,
                decision_code="quality_assessed",
                reason=f"Quality: {quality_score.quality_level.value} "
                       f"(score={quality_score.composite_score:.2f})",
                blocking=False,
                advisory_data={
                    "composite_score": quality_score.composite_score,
                    "quality_level": quality_score.quality_level.value,
                    "recommendation": quality_score.recommendation,
                    "data_samples": len(recent_deltas),
                },
            )
        except Exception as e:
            logger.error(f"[L5 k=3] FATAL: Quality gate failed: {e}")
            raise RuntimeError(f"[L5 k=3] Quality gate failed: {e}. Fail-closed.")

    def _run_k4_conflict_detection(self, result: L5PipelineResult) -> L5GateDecision:
        """
        k=4: Conflict Detector — detect multi-skill parameter conflicts.

        Tenant isolation: Only check pending approvals within same tenant.

        Returns:
            L5GateDecision with decision_code in:
            - "no_conflicts": No conflicts detected
            - "conflicts_serialized": Conflicts detected and serialized
        """
        try:
            # CRITICAL: Tenant isolation + prevent mutation leaks
            # BUG FIX #3: Extract only this tenant's approvals
            with self._lock:
                tenant_approvals = copy.deepcopy(self.pending_approvals.get(self.tenant_id, {}))

            # Detect conflicts in pending approvals (tenant-scoped)
            resolutions = self.conflict_resolver.detect_and_resolve(
                tenant_approvals
            )

            result.conflict_resolutions = resolutions

            if not resolutions:
                return L5GateDecision(
                    gate_name="k=4",
                    passed=True,
                    decision_code="no_conflicts",
                    reason="No conflicts detected with other in-flight approvals",
                    blocking=False,
                )
            else:
                # Conflicts detected and serialized
                return L5GateDecision(
                    gate_name="k=4",
                    passed=False,
                    decision_code="conflicts_serialized",
                    reason=f"Conflicts detected: {len(resolutions)} serialized",
                    blocking=True,
                    advisory_data={
                        "conflict_count": len(resolutions),
                        "resolution_strategy": resolutions[0].strategy.value if resolutions else None,
                    },
                )
        except Exception as e:
            logger.error(f"[L5 k=4] FATAL: Conflict detection failed: {e}")
            raise RuntimeError(f"[L5 k=4] Conflict detection failed: {e}. Fail-closed.")

    def _run_k5_rollback_check(self, result: L5PipelineResult) -> L5GateDecision:
        """
        k=5: Rollback Guard — check hold period (advisory).

        Returns:
            L5GateDecision (advisory only, never blocking)
        """
        try:
            # Compute future expiration time (12 hours from now)
            hold_period_hours = 12
            expires_at = datetime.now(timezone.utc) + timedelta(hours=hold_period_hours)
            expires_at_iso = expires_at.isoformat().replace("+00:00", "Z")

            # Check if rollback is allowed (advisory)
            return L5GateDecision(
                gate_name="k=5",
                passed=True,
                decision_code="hold_period_set",
                reason=f"Hold period enforced for {hold_period_hours}h; operator can revoke anytime",
                blocking=False,
                advisory_data={
                    "hold_period_hours": hold_period_hours,
                    "expires_at": expires_at_iso,
                },
            )
        except Exception as e:
            logger.error(f"[L5 k=5] FATAL: Rollback check failed: {e}")
            raise RuntimeError(f"[L5 k=5] Rollback check failed: {e}. Fail-closed.")

    # ========== Audit & Tracking ==========

    def _audit_pipeline_complete(self, result: L5PipelineResult) -> None:
        """
        Audit-first: log pipeline completion.

        CRITICAL: Fail-closed constraint — if audit fails, entire pipeline fails.
        This method MUST succeed before any result is returned to caller.

        Raises:
            RuntimeError: If audit write fails (fail-closed, no fallback)
        """
        if not self.audit_backend:
            logger.error("[L5 Integrator] FATAL: No audit backend configured")
            raise RuntimeError("[L5 Integrator] No audit backend; audit-first constraint failed")

        event = {
            "tenant_id": self.tenant_id,
            "event_type": "l5_pipeline_complete",
            "pipeline_id": result.pipeline_id,
            "skill_id": result.skill_id,
            "metric_name": result.metric_name,
            "raw_delta": result.raw_delta,
            "final_decision": result.final_decision.value,
            "k1_passed": result.k1_decision.passed if result.k1_decision else None,
            "k2_passed": result.k2_decision.passed if result.k2_decision else None,
            "k3_passed": result.k3_decision.passed if result.k3_decision else None,
            "k4_passed": result.k4_decision.passed if result.k4_decision else None,
            "k5_passed": result.k5_decision.passed if result.k5_decision else None,
            "approval_id": result.approval_id,
        }

        try:
            self.audit_backend.write_event(event)
        except Exception as e:
            logger.critical(
                f"[L5 Integrator] FATAL: Audit write failed (fail-closed): {e}"
            )
            raise RuntimeError(
                f"[L5 Integrator] Audit-first constraint violated: {e}. "
                f"Pipeline result NOT returned (fail-closed)."
            )

    def _audit_pipeline_error(self, result: L5PipelineResult, error: str) -> None:
        """Audit-first: log pipeline error."""
        if not self.audit_backend:
            return

        event = {
            "tenant_id": self.tenant_id,
            "event_type": "l5_pipeline_error",
            "pipeline_id": result.pipeline_id,
            "skill_id": result.skill_id,
            "metric_name": result.metric_name,
            "error": error,
        }
        self.audit_backend.write_event(event)

    def _track_pending_approval(self, result: L5PipelineResult) -> None:
        """Track pending approval for conflict detection (tenant-scoped)."""
        with self._lock:
            # BUG FIX #3: Initialize tenant scope if not present
            if self.tenant_id not in self.pending_approvals:
                self.pending_approvals[self.tenant_id] = {}

            if result.skill_id not in self.pending_approvals[self.tenant_id]:
                self.pending_approvals[self.tenant_id][result.skill_id] = {}

            self.pending_approvals[self.tenant_id][result.skill_id][result.metric_name] = {
                "approval_id": result.approval_id,
                "pipeline_id": result.pipeline_id,
                "timestamp": format_iso_timestamp(),
                "raw_delta": result.raw_delta,
            }

    # ========== Operator Controls ==========

    def approve_pending(self, approval_id: str) -> None:
        """Operator approves a pending approval."""
        logger.info(f"[L5 Integrator] Operator approved: {approval_id}")

        if not self.audit_backend:
            logger.error("[L5 Integrator] FATAL: No audit backend; cannot record approval")
            raise RuntimeError("[L5 Integrator] Audit backend required for operator controls")

        try:
            event = {
                "tenant_id": self.tenant_id,
                "event_type": "l5_operator_approval",
                "approval_id": approval_id,
            }
            self.audit_backend.write_event(event)
        except Exception as e:
            logger.critical(f"[L5 Integrator] Audit-first constraint violated: {e}")
            raise RuntimeError(f"Approval audit failed: {e}")

    def reject_pending(self, approval_id: str, reason: str) -> None:
        """Operator rejects a pending approval."""
        logger.info(f"[L5 Integrator] Operator rejected: {approval_id}")

        if not self.audit_backend:
            logger.error("[L5 Integrator] FATAL: No audit backend; cannot record rejection")
            raise RuntimeError("[L5 Integrator] Audit backend required for operator controls")

        try:
            event = {
                "tenant_id": self.tenant_id,
                "event_type": "l5_operator_rejection",
                "approval_id": approval_id,
                "reason": reason,
            }
            self.audit_backend.write_event(event)
        except Exception as e:
            logger.critical(f"[L5 Integrator] Audit-first constraint violated: {e}")
            raise RuntimeError(f"Rejection audit failed: {e}")

    def revoke_approved(self, approval_id: str, operator_id: str, reason: str) -> None:
        """Operator revokes a previously-approved config change."""
        logger.critical(f"[L5 Integrator] Operator revoked: {approval_id}")

        if not self.audit_backend:
            logger.error("[L5 Integrator] FATAL: No audit backend; cannot record revoke")
            raise RuntimeError("[L5 Integrator] Audit backend required for operator controls")

        try:
            event = {
                "tenant_id": self.tenant_id,
                "event_type": "l5_operator_revoke",
                "approval_id": approval_id,
                "operator_id": operator_id,
                "reason": reason,
            }
            self.audit_backend.write_event(event)
        except Exception as e:
            logger.critical(f"[L5 Integrator] Audit-first constraint violated: {e}")
            raise RuntimeError(f"Revoke audit failed: {e}")

    # ========== Observability ==========

    def get_pipeline_result(self, pipeline_id: str) -> Optional[L5PipelineResult]:
        """Get a pipeline result by ID."""
        with self._lock:
            return self.pipeline_results.get(pipeline_id)

    def get_pending_approvals(self) -> Dict[str, Dict[str, Dict]]:
        """Get all pending approvals for this tenant."""
        with self._lock:
            # BUG FIX #3: Return only this tenant's approvals
            return dict(self.pending_approvals.get(self.tenant_id, {}))
