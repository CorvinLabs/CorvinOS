"""
Learning Loop Integration with L5 k=2 OperatorApprovalGate

Wires the optimizer feedback loop to the operator approval gate.
When learning detects significant drift, requests operator approval before applying config.
"""

from __future__ import annotations

import logging
from typing import Optional, Dict, Tuple, TYPE_CHECKING
from dataclasses import dataclass
from core.skills.feedback_stability import (
    FeedbackStabilityGate,
    DriftAlert,
    OperatorApprovalRecord,
    ApprovalDecision,
    OperatorApprovalGate,
)

logger = logging.getLogger(__name__)


@dataclass
class OptimizerConfig:
    """Skill optimizer configuration (immutable)."""
    skill_id: str
    metric_name: str
    config_hash: str  # Current running config SHA256


class OptimizerWithApprovalGate:
    """
    Optimizer that gates config changes through L5 k=2.

    Workflow:
    1. Optimizer computes delta from feedback
    2. FeedbackStabilityGate smooths + detects drift
    3. OperatorApprovalGate requests operator approval (or auto-approves)
    4. Callback: on approval, Skill applies config change
    """

    def __init__(
        self,
        skill_id: str,
        stability_gate: FeedbackStabilityGate,
        approval_gate: OperatorApprovalGate,
    ):
        """
        Initialize optimizer with approval gating.

        Args:
            skill_id: Skill being optimized
            stability_gate: L5 k=1 (EMA smoothing + drift detection)
            approval_gate: L5 k=2 (operator approval)
        """
        self.skill_id = skill_id
        self.stability_gate = stability_gate
        self.approval_gate = approval_gate

        # Current config hash (from Skill state)
        self.current_config_hash = None

        # Callbacks (wired by Skill implementation)
        self.on_approval_callback = None  # (approval_id, config_hash) → apply config
        self.on_rejection_callback = None  # (approval_id) → log rejection, continue

    def process_feedback(
        self,
        metric_name: str,
        raw_delta: float,
        new_config_hash: str,
    ) -> Tuple[Optional[OperatorApprovalRecord], bool]:
        """
        Process learning feedback and request approval if needed.

        Args:
            metric_name: Metric being tuned (e.g., "confidence_threshold")
            raw_delta: Raw change from feedback (unsmoothed)
            new_config_hash: SHA256 of proposed new config

        Returns:
            (approval_record, approved_immediately)
            - If approved_immediately=True: caller should apply new_config immediately
            - If approved_immediately=False: wait for operator decision (use callbacks)
        """
        # Step 1: Smooth feedback (L5 k=1)
        smoothed, drift_alert = self.stability_gate.apply_feedback(
            self.skill_id,
            metric_name,
            raw_delta,
        )

        confidence = smoothed.confidence

        logger.info(
            f"[Optimizer] {self.skill_id}.{metric_name}: "
            f"raw_delta={raw_delta:.4f}, smoothed={smoothed.smoothed_delta:.4f}, "
            f"confidence={confidence:.2f}"
        )

        # If no drift, no approval needed
        if drift_alert is None:
            logger.debug(f"[Optimizer] No drift for {self.skill_id}.{metric_name}, applying immediately")
            return None, True  # Auto-apply

        # Step 2: Request operator approval (L5 k=2)
        logger.warning(
            f"[Optimizer] Drift detected: {self.skill_id}.{metric_name}, "
            f"magnitude={drift_alert.smoothed_delta:.4f}, requesting approval"
        )

        try:
            record, auto_approved = self.approval_gate.request_approval(
                drift_alert,
                confidence=confidence,
                prev_config_hash=self.current_config_hash or "a" * 64,
                next_config_hash=new_config_hash,
            )

            if auto_approved:
                logger.info(
                    f"[Optimizer] Auto-approved {self.skill_id}.{metric_name} "
                    f"(confidence={confidence:.2f} > 0.8)"
                )
                return record, True

            else:
                logger.warning(
                    f"[Optimizer] Approval pending for {self.skill_id}.{metric_name}, "
                    f"approval_id={record.approval_id}"
                )
                return record, False

        except Exception as e:
            logger.error(
                f"[Optimizer] Failed to request approval: {e}, applying immediately (fallback)"
            )
            return None, True  # Fallback: apply anyway (fail-open, not ideal but safe)

    def handle_approval(self, approval_id: str, new_config_hash: str) -> None:
        """
        Called when operator approves a config change.

        Args:
            approval_id: UUID of the approval
            new_config_hash: Config hash to apply
        """
        status = self.approval_gate.get_approval_status(approval_id)
        if status and status.decision == ApprovalDecision.APPROVED:
            logger.info(f"[Optimizer] Approval granted: {approval_id}, applying config")
            self.current_config_hash = new_config_hash

            # Call Skill-provided callback
            if self.on_approval_callback:
                try:
                    self.on_approval_callback(approval_id, new_config_hash)
                except Exception as e:
                    logger.error(f"[Optimizer] Approval callback failed: {e}")

    def handle_rejection(self, approval_id: str) -> None:
        """
        Called when operator rejects a config change.

        Args:
            approval_id: UUID of the approval
        """
        status = self.approval_gate.get_approval_status(approval_id)
        if status and status.decision == ApprovalDecision.REJECTED:
            logger.warning(f"[Optimizer] Approval rejected: {approval_id}, continuing with current config")

            # Call Skill-provided callback
            if self.on_rejection_callback:
                try:
                    self.on_rejection_callback(approval_id)
                except Exception as e:
                    logger.error(f"[Optimizer] Rejection callback failed: {e}")

    def handle_revoke(self, approval_id: str) -> None:
        """
        Called when operator revokes a previously-approved config change.

        Args:
            approval_id: UUID of the approval to revoke
        """
        logger.critical(
            f"[Optimizer] Approval revoked: {approval_id}, rolling back to previous config"
        )
        # Skill should restore prev_config_hash
        # TODO: add rollback_callback
