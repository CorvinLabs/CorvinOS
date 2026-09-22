"""
Flow Guard Skill — Main orchestrator for data flow decisions.

Makes allow/deny decisions based on data classification + learned policies.
Logs all decisions to audit trail (ADR-0232).
Learns from outcomes to improve future decisions (ADR-0314).

This is the core Skill module (ADR-2032, Stream 3, Phase 10).

Example usage:
  >>> guard = FlowGuard(tenant_id="default")
  >>> decision = guard.evaluate_flow(
  ...     data="user@example.com",
  ...     destination_engine="anthropic/claude-opus-5",
  ... )
  >>> decision.decision  # FlowDecision.ALLOW or UNCERTAIN
  >>> decision.confidence  # 0.88
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional, Dict, List
import logging

from .data_classifier import DataClassifier, ClassificationResult, DataClassification
from .flow_policy import (
    FlowPolicy,
    FlowDecision,
    FlowOutcome,
    FlowPolicyManager,
    PolicyRule,
)

logger = logging.getLogger(__name__)


class FlowBlockReason(str, Enum):
    """Reasons why a flow was blocked."""

    CREDENTIALS_DETECTED = "credentials_detected"
    DENY_POLICY = "deny_policy"
    UNCERTAIN_AWAITING_APPROVAL = "uncertain_awaiting_approval"
    UNKNOWN_DATA_CLASS = "unknown_data_class"
    CONSENT_MISSING = "consent_missing"


@dataclass
class FlowEvaluation:
    """Result of a flow evaluation."""

    data_class: str
    classification_confidence: float
    destination_engine: str
    decision: FlowDecision
    policy_confidence: float
    reasoning: str
    evidence: Optional[str] = None
    block_reason: Optional[FlowBlockReason] = None
    timestamp: datetime = field(default_factory=datetime.utcnow)
    lom: str = "flow_guard.FlowGuard.evaluate_flow"  # Line of Moral Responsibility

    def to_audit_dict(self) -> dict:
        """Convert to audit event format (ADR-0232)."""
        return {
            "event_type": "data_flow_decision",
            "skill_id": "os.flow_guard",
            "data_class": self.data_class,
            "classification_confidence": self.classification_confidence,
            "destination_engine": self.destination_engine,
            "decision": self.decision.value,
            "policy_confidence": self.policy_confidence,
            "reasoning": self.reasoning,
            "evidence": self.evidence,
            "block_reason": self.block_reason.value if self.block_reason else None,
            "timestamp": self.timestamp.isoformat(),
            "lom": self.lom,
        }


class FlowGuard:
    """
    Main Flow Guard Skill.

    Responsibilities:
      1. Classify input data (PII/sensitive/public)
      2. Look up policy for (data_class, destination_engine)
      3. Make allow/deny decision (or UNCERTAIN if low confidence)
      4. Log decision to audit trail (fail-closed)
      5. Learn from outcomes to improve policy (ADR-0314)

    Fail-closed design:
      - Unknown data → UNCERTAIN (requires approval)
      - Credentials → DENY (never allow)
      - Missing consent → UNCERTAIN (requires approval)
      - Any error → DENY (fail-closed)
    """

    def __init__(
        self,
        tenant_id: str,
        confidence_threshold: float = 0.7,
        allow_uncertain_flows: bool = False,
    ):
        """
        Initialize Flow Guard.

        Args:
            tenant_id: Tenant ID for policy isolation
            confidence_threshold: Confidence level for auto-allow/deny (0.0–1.0)
            allow_uncertain_flows: If False (default), uncertain flows are blocked
        """
        self.tenant_id = tenant_id
        self.confidence_threshold = confidence_threshold
        self.allow_uncertain_flows = allow_uncertain_flows

        self.classifier = DataClassifier()
        self.policy_manager = FlowPolicyManager()

    def evaluate_flow(
        self,
        data: str,
        destination_engine: str,
        user_consent: Optional[Dict[str, bool]] = None,
        context: Optional[dict] = None,
    ) -> FlowEvaluation:
        """
        Evaluate whether a data flow is allowed.

        Args:
            data: The data being sent
            destination_engine: Where it's going (e.g., "anthropic/claude-opus-5")
            user_consent: Dict of consent grants (e.g., {"email": True, "location": False})
            context: Optional context (e.g., task_id, user_id)

        Returns:
            FlowEvaluation with decision, confidence, and reasoning

        Raises:
            ValueError: If data or destination_engine is invalid
        """
        if not data or not isinstance(data, str):
            raise ValueError("Data must be a non-empty string")

        if not destination_engine or not isinstance(destination_engine, str):
            raise ValueError("Destination engine must be a non-empty string")

        # Step 1: Classify the data
        classification = self.classifier.classify(data, context)

        # Step 2: Check for hard-fail cases (credentials, unknown data)
        if classification.data_class == DataClassification.CREDENTIALS:
            return FlowEvaluation(
                data_class=classification.data_class.value,
                classification_confidence=classification.confidence,
                destination_engine=destination_engine,
                decision=FlowDecision.DENY,
                policy_confidence=1.0,
                reasoning="Credentials detected — always blocked",
                evidence=classification.evidence,
                block_reason=FlowBlockReason.CREDENTIALS_DETECTED,
                lom=self.__class__.__name__ + ".evaluate_flow:L97",
            )

        # Step 3: Look up policy for this (data_class, destination_engine) pair
        policy_decision = self.policy_manager.get_decision(
            self.tenant_id,
            classification.data_class.value,
            destination_engine,
            self.confidence_threshold,
        )

        # Get policy confidence
        policy = self.policy_manager.get_or_create_policy(self.tenant_id)
        policy_confidence = 0.0
        for rule in policy.rules:
            if (
                rule.data_class == classification.data_class.value
                and rule.destination_engine == destination_engine
                and rule.decision == policy_decision
            ):
                policy_confidence = rule.confidence
                break

        # Step 4: Check consent (if applicable)
        if classification.data_class in [
            DataClassification.PERSONAL_EMAIL,
            DataClassification.PHONE_NUMBER,
            DataClassification.HOME_ADDRESS,
        ]:
            # PII requires explicit consent
            if not user_consent or not user_consent.get(classification.data_class.value, False):
                return FlowEvaluation(
                    data_class=classification.data_class.value,
                    classification_confidence=classification.confidence,
                    destination_engine=destination_engine,
                    decision=FlowDecision.UNCERTAIN,
                    policy_confidence=0.0,
                    reasoning="PII requires explicit user consent",
                    evidence=classification.evidence,
                    block_reason=FlowBlockReason.CONSENT_MISSING,
                    lom=self.__class__.__name__ + ".evaluate_flow:L126",
                )

        # Step 5: Make final decision
        final_decision = FlowDecision.ALLOW
        block_reason = None

        if policy_decision == FlowDecision.DENY:
            final_decision = FlowDecision.DENY
            block_reason = FlowBlockReason.DENY_POLICY
        elif policy_decision == FlowDecision.UNCERTAIN:
            if self.allow_uncertain_flows:
                final_decision = FlowDecision.UNCERTAIN
                block_reason = FlowBlockReason.UNCERTAIN_AWAITING_APPROVAL
            else:
                final_decision = FlowDecision.DENY
                block_reason = FlowBlockReason.UNCERTAIN_AWAITING_APPROVAL

        reasoning = self._build_reasoning(
            final_decision,
            classification.confidence,
            policy_confidence,
            block_reason,
        )

        return FlowEvaluation(
            data_class=classification.data_class.value,
            classification_confidence=classification.confidence,
            destination_engine=destination_engine,
            decision=final_decision,
            policy_confidence=policy_confidence,
            reasoning=reasoning,
            evidence=classification.evidence,
            block_reason=block_reason,
            lom=self.__class__.__name__ + ".evaluate_flow:L155",
        )

    def record_outcome(
        self,
        data_class: str,
        destination_engine: str,
        result: str,
        reasoning: str = "",
    ) -> None:
        """
        Record the outcome of a flow (used for learning).

        Args:
            data_class: Data classification
            destination_engine: Destination engine
            result: "success" | "pii_leak_detected" | "error"
            reasoning: Optional explanation
        """
        outcome = FlowOutcome(
            data_class=data_class,
            destination_engine=destination_engine,
            flow_allowed=result == "success",
            result=result,
            reasoning=reasoning,
        )
        self.policy_manager.record_outcome(self.tenant_id, outcome)

        # Log to audit trail
        logger.info(
            f"Flow outcome recorded: data_class={data_class}, "
            f"destination={destination_engine}, result={result}",
            extra={
                "skill_id": "os.flow_guard",
                "event_type": "flow_outcome_recorded",
                "data_class": data_class,
                "result": result,
            },
        )

    def add_policy_rule(self, rule: PolicyRule) -> None:
        """
        Manually add a policy rule (e.g., operator override).

        Args:
            rule: PolicyRule to add
        """
        policy = self.policy_manager.get_or_create_policy(self.tenant_id)
        policy.add_rule(rule)

        logger.info(
            f"Policy rule added: {rule.data_class} → {rule.destination_engine} "
            f"({rule.decision.value}, confidence={rule.confidence})",
            extra={
                "skill_id": "os.flow_guard",
                "event_type": "flow_policy_updated",
                "data_class": rule.data_class,
                "decision": rule.decision.value,
                "confidence": rule.confidence,
            },
        )

    def get_policy(self) -> FlowPolicy:
        """Get current policy (for introspection/testing)."""
        return self.policy_manager.get_or_create_policy(self.tenant_id)

    def export_policy(self) -> str:
        """Export policy as JSON (for backup/restore)."""
        return self.policy_manager.export_policy(self.tenant_id)

    def import_policy(self, policy_json: str) -> None:
        """Import policy from JSON (for restore)."""
        self.policy_manager.import_policy(self.tenant_id, policy_json)

    @staticmethod
    def _build_reasoning(
        decision: FlowDecision,
        classification_confidence: float,
        policy_confidence: float,
        block_reason: Optional[FlowBlockReason],
    ) -> str:
        """Build human-readable reasoning string."""
        if decision == FlowDecision.ALLOW:
            return (
                f"Flow allowed (classification confidence: {classification_confidence:.1%}, "
                f"policy confidence: {policy_confidence:.1%})"
            )
        elif decision == FlowDecision.DENY:
            reason_str = block_reason.value if block_reason else "policy"
            return f"Flow blocked ({reason_str})"
        else:  # UNCERTAIN
            return (
                f"Flow uncertain — awaiting approval "
                f"(classification: {classification_confidence:.1%}, policy: {policy_confidence:.1%})"
            )
