"""L5 Workflow Optimizer Skill (Phase 10 Stream 1).

Learns task routing paths and dynamically optimizes routing decisions.
- Classifies task complexity (simple/medium/complex)
- Routes to best model (haiku/sonnet/opus)
- Collects operator feedback on routing quality
- Updates routing weights via learning optimizer

Inherits from BaseSkill (ADR-0535 + ADR-0232).

Compliance:
- Every routing decision audited with tenant_id (GDPR Art. 30, 32)
- Learned weights backed up daily (ADR-0314)
- Fail-closed: undefined complexity -> deny
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, Optional
from enum import Enum

# Local imports (will import BaseSkill from phase1)
try:
    from core.skills.os_skills.phase1.base_skill import (
        BaseSkill,
        SkillExecutedEvent,
        SkillConfigUpdatedEvent,
        SkillExecutionStatus,
        AuditTrail,
    )
except ImportError:
    # Fallback for testing
    BaseSkill = object
    SkillExecutedEvent = None
    SkillExecutionStatus = None

from l5_task_classifier import TaskClassifier, ComplexityTier, ClassificationResult
from l5_agent_selector import AgentSelector, RoutingDecision

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RoutingInput:
    """Immutable input to routing skill."""
    task_prompt: str
    tenant_id: str
    task_id: str
    model_pin: Optional[str] = None  # Operator override
    available_models: Optional[list] = None


@dataclass(frozen=True)
class RoutingOutput:
    """Immutable output from routing skill."""
    model_name: str
    complexity_tier: str
    classifier_confidence: float
    routing_confidence: float
    reasoning: str
    feature_hash: str


class WorkflowOptimizerSkill(BaseSkill[RoutingOutput]):
    """L5 Workflow Optimizer Skill for model routing with learning."""

    skill_id = "os.workflow_optimizer_l5"
    version = "1.0.0"
    required_dependencies = []  # No hard dependencies
    soft_dependencies = ["os.context_adapter"]  # Optional context enrichment

    def __init__(self, tenant_id: str, audit_trail: AuditTrail):
        """Initialize skill with classifier, selector, and audit trail.

        Args:
            tenant_id: Tenant scope for all operations
            audit_trail: Audit trail backend (from core/compliance)
        """
        super().__init__(tenant_id, audit_trail)
        self.classifier = TaskClassifier()
        self.selector = AgentSelector()

        # Learned config (will be persisted to disk in Week 3)
        self.routing_weights_version = "1.0.0"
        self.feedback_count = 0
        self.last_updated = datetime.utcnow().isoformat()

    def execute(self, input_data: RoutingInput) -> RoutingOutput:
        """Execute routing decision (audit-first).

        Contract (ADR-0232):
        1. Validate input (tenant_id, task_prompt)
        2. Classify task complexity
        3. Select best model
        4. Log to audit trail (MUST succeed before returning)
        5. Return routing decision

        Args:
            input_data: RoutingInput with task_prompt, tenant_id, etc.

        Returns:
            RoutingOutput with model_name, confidence, reasoning

        Raises:
            RuntimeError: If audit trail rejected the decision
            ValueError: If input validation fails
        """
        start_time = time.perf_counter()

        # Validate input
        if not input_data.task_prompt:
            raise ValueError("task_prompt required")
        if input_data.tenant_id != self.tenant_id:
            raise ValueError(f"Tenant mismatch: {input_data.tenant_id} != {self.tenant_id}")

        try:
            # Phase 1: Classify task complexity
            classification = self.classifier.classify(input_data.task_prompt)

            # Phase 2: Select model based on classification
            routing_decision = self.selector.select(
                complexity_tier=classification.tier.value,
                task_feature_hash=classification.feature_hash,
                model_pin=input_data.model_pin,
                available_models=input_data.available_models or self._default_models(),
            )

            # Build output
            output = RoutingOutput(
                model_name=routing_decision.model_name,
                complexity_tier=routing_decision.tier_input,
                classifier_confidence=classification.confidence,
                routing_confidence=routing_decision.confidence,
                reasoning=routing_decision.reasoning,
                feature_hash=routing_decision.feature_hash,
            )

            # Phase 3: Audit (MUST succeed before returning)
            latency_ms = int((time.perf_counter() - start_time) * 1000)
            success = self._audit_execution(
                input_data=input_data,
                output_data=output,
                status=SkillExecutionStatus.SUCCESS,
                latency_ms=latency_ms,
            )

            if not success:
                raise RuntimeError(f"Audit trail rejected routing decision for task {input_data.task_id}")

            return output

        except Exception as e:
            logger.error(f"Routing skill failed: {e}", extra={"tenant_id": self.tenant_id, "task_id": input_data.task_id})
            latency_ms = int((time.perf_counter() - start_time) * 1000)
            self._audit_execution(
                input_data=input_data,
                output_data=None,
                status=SkillExecutionStatus.ERROR,
                latency_ms=latency_ms,
                error_message=str(e),
            )
            raise

    def feedback(
        self,
        task_id: str,
        model_used: str,
        complexity_tier: str,
        outcome: str,  # "correct", "incorrect", "partial"
        operator_notes: Optional[str] = None,
    ) -> None:
        """Process operator feedback on a routing decision.

        Updates learned weights based on feedback.

        Args:
            task_id: Original task ID
            model_used: Model that was selected
            complexity_tier: Complexity tier of the task
            outcome: "correct", "incorrect", "partial"
            operator_notes: Optional operator commentary
        """
        start_time = time.perf_counter()

        try:
            # Convert outcome to learning signal
            if outcome == "correct":
                feedback_signal = 1.0
            elif outcome == "partial":
                feedback_signal = 0.0
            elif outcome == "incorrect":
                feedback_signal = -1.0
            else:
                logger.warning(f"Unknown outcome: {outcome}")
                feedback_signal = 0.0

            # Update weights
            self.selector.update_weights(
                tier=complexity_tier,
                model=model_used,
                feedback_signal=feedback_signal,
                learning_rate=0.1,
            )

            self.feedback_count += 1
            self.last_updated = datetime.utcnow().isoformat()

            logger.info(
                f"Feedback processed: task={task_id}, outcome={outcome}, tier={complexity_tier}",
                extra={"tenant_id": self.tenant_id}
            )

        except Exception as e:
            logger.error(f"Feedback processing failed: {e}")
            raise

    def get_config(self) -> Dict[str, Any]:
        """Export current routing configuration (for persistence)."""
        return {
            'skill_id': self.skill_id,
            'version': self.version,
            'routing_weights': self.selector.to_dict(),
            'routing_weights_version': self.routing_weights_version,
            'feedback_count': self.feedback_count,
            'last_updated': self.last_updated,
            'tenant_id': self.tenant_id,
        }

    def set_config(self, config: Dict[str, Any]) -> None:
        """Load routing configuration (for recovery/versioning)."""
        if config.get('skill_id') != self.skill_id:
            raise ValueError(f"Config mismatch: {config.get('skill_id')} != {self.skill_id}")

        self.selector = AgentSelector.from_dict(config.get('routing_weights', {}))
        self.routing_weights_version = config.get('routing_weights_version', self.version)
        self.feedback_count = config.get('feedback_count', 0)
        self.last_updated = config.get('last_updated', datetime.utcnow().isoformat())

    def _default_models(self) -> list[str]:
        """Default available models."""
        return [
            "claude-haiku-4-5-20251001",
            "claude-sonnet-5-20251001",
            "claude-opus-5-20251001",
        ]

    def _audit_execution(
        self,
        input_data: RoutingInput,
        output_data: Optional[RoutingOutput],
        status: SkillExecutionStatus,
        latency_ms: int,
        error_message: Optional[str] = None,
    ) -> bool:
        """Audit routing decision to immutable trail (ADR-0232).

        Args:
            input_data: Routing input
            output_data: Routing output (or None if error)
            status: Execution status
            latency_ms: Execution time
            error_message: Error details if failed

        Returns:
            True if audit succeeded, False if audit trail rejected
        """
        input_hash = hashlib.sha256(
            json.dumps({
                'task_id': input_data.task_id,
                'tenant_id': input_data.tenant_id,
                'prompt_length': len(input_data.task_prompt),
            }).encode()
        ).hexdigest()

        output_hash = hashlib.sha256(
            json.dumps({
                'model': output_data.model_name if output_data else None,
                'tier': output_data.complexity_tier if output_data else None,
            }).encode()
        ).hexdigest()

        event = SkillExecutedEvent(
            tenant_id=self.tenant_id,
            timestamp=datetime.utcnow().isoformat() + "Z",
            skill_id=self.skill_id,
            version=self.version,
            input_hash=input_hash,
            output_hash=output_hash,
            status=status,
            latency_ms=latency_ms,
            lom=self._get_lom(),
            lom_hash=self._compute_lom_hash(),
            error_message=error_message,
            hash="",
        )

        success = self.audit_trail.write_event(event)
        if not success:
            logger.error(
                f"Audit trail rejected execution: {input_hash} -> {output_hash}",
                extra={"tenant_id": self.tenant_id}
            )
        return success
