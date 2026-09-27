"""SkillOptimizerLoop — Phase 3 Loop Closure (ADR-0314 + ADR-0722).

The learning loop's optimization engine. Consumes feedback signals and computes
parameter deltas for Skills (e.g., os.delegation_router, os.context_adapter).

Loop Closure Chain:
  FeedbackSignal (ingester aggregates)
    ↓
  OptimizerLoop reads signal_strength + outcomes
    ↓
  Computes parameter delta (gradient descent on confidence)
    ↓
  Validates: convergence check + bounds enforcement + PII safety
    ↓
  Updates Skill config (manifest file, immutable, audit-logged)
    ↓
  Next Skill run uses updated params
    ↓
  [Loop continues — feedback → optimize → improve]

Invariants (LOAD-BEARING):
- Stateless: same (signal, config, history) → same delta (reproducible)
- Convergence detection: stop when slope < 0.01 or confidence > 0.95
- Bounds enforcement: reject deltas >1σ (prevent oscillation)
- PII safety: fail-closed on any credential/secret pattern
- Audit-first: every decision logged before config write
- Content-free: no prompts, transcripts, user data — only metrics
- Tenant-scoped: GDPR Art. 32 isolation

Algorithm:
  1. Extract success_rate from recent outcomes (window of 50 tasks)
  2. Compute slope (linear regression, last 20 samples)
  3. Detect convergence (slope < 0.01 OR confidence > 0.95)
  4. If NOT converged:
     - Compute parameter delta (proportional to gap from target 0.9)
     - Check bounds (reject >1σ)
     - Validate PII (fail-closed)
     - Emit config_updated event
     - Write new manifest
  5. Return OptimizationDecision for audit logging
"""

from __future__ import annotations

import json
import logging
import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional, Dict, Any, List, Tuple
from pathlib import Path

from core.learning.learning_events import LearningEvent, EventType, FeedbackSignal
from core.learning.skill_feedback_ingester import FeedbackSignalType
from core.paths import tenant_home

logger = logging.getLogger(__name__)


@dataclass
class OptimizationDecision:
    """Result of one optimization epoch."""
    should_update: bool
    reason: str  # "convergence_reached", "bounds_enforced", "safe_delta", "pii_detected", etc.
    parameter_deltas: Dict[str, float] = field(default_factory=dict)
    old_config: Dict[str, Any] = field(default_factory=dict)
    new_config: Dict[str, Any] = field(default_factory=dict)
    confidence_score: float = 0.5
    slope: float = 0.0
    validation_passed: bool = False
    audit_event: Optional[LearningEvent] = None


class ConvergenceDetector:
    """Detects when learning has converged (slope-based + confidence)."""

    def __init__(
        self,
        window_size: int = 50,
        slope_threshold: float = 0.01,
        confidence_threshold: float = 0.95,
    ):
        """Initialize convergence detector.

        Args:
            window_size: Number of samples to track
            slope_threshold: Slope below this = convergence
            confidence_threshold: Confidence above this = stop learning
        """
        self.window_size = window_size
        self.slope_threshold = slope_threshold
        self.confidence_threshold = confidence_threshold
        self.history: List[float] = []  # Success rate history

    def add_sample(self, success_rate: float) -> None:
        """Add success rate to history."""
        if not 0.0 <= success_rate <= 1.0:
            logger.warning(f"Ignoring invalid success_rate: {success_rate}")
            return
        self.history.append(success_rate)
        # Keep window size bounded
        if len(self.history) > self.window_size:
            self.history = self.history[-self.window_size:]

    def compute_slope(self) -> float:
        """Compute linear regression slope over history."""
        if len(self.history) < 2:
            return 0.0

        # Linear regression: y = mx + b, solve for m (slope)
        n = len(self.history)
        mean_x = (n - 1) / 2.0
        mean_y = sum(self.history) / n

        numerator = sum((i - mean_x) * (y - mean_y) for i, y in enumerate(self.history))
        denominator = sum((i - mean_x) ** 2 for i in range(n))

        if denominator == 0:
            return 0.0

        return numerator / denominator

    def compute_confidence(self) -> float:
        """Compute confidence score [0.0, 1.0].

        Confidence = f(success_rate, sample_size, slope)
        - High success rate → high confidence
        - Large sample size → high confidence (sqrt curve, saturates at n=100)
        - Stable/declining slope → high confidence
        """
        if not self.history:
            return 0.0

        # Success rate component
        mean_success_rate = sum(self.history) / len(self.history)

        # Sample size component (sqrt to avoid overweighting large n)
        sample_size_confidence = math.sqrt(len(self.history) / 100.0)

        # Slope stability component (declining slope = improving)
        slope = self.compute_slope()
        if slope >= 0:
            # Flat or rising slope = stable or improving, high confidence
            slope_confidence = 1.0
        else:
            # Declining slope = still improving, moderate confidence
            slope_confidence = 0.8

        # Combine: geometric mean to weight all factors
        confidence = (mean_success_rate * sample_size_confidence * slope_confidence) ** (1.0 / 3.0)

        return min(1.0, max(0.0, confidence))

    def has_converged(self) -> Tuple[bool, str]:
        """Check if learning has converged.

        Returns:
            (has_converged, reason)
        """
        if len(self.history) < 5:
            return False, "insufficient_samples"

        slope = self.compute_slope()
        if abs(slope) < self.slope_threshold:
            return True, "slope_threshold_reached"

        confidence = self.compute_confidence()
        if confidence > self.confidence_threshold:
            return True, "confidence_threshold_reached"

        return False, "ongoing"


class SkillOptimizerLoop:
    """Optimizer loop: feedback → parameter delta → config update."""

    # Target success rate (optimizer tries to reach this)
    TARGET_SUCCESS_RATE = 0.90

    # Max allowed delta (±1σ safety bound)
    MAX_PARAMETER_DELTA = 0.10

    # PII patterns that disqualify a config update (fail-closed)
    PII_PATTERNS = [
        r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b",  # Email
        r"\bsk_live_|sk_test_|pk_live_|pk_test_",  # Stripe keys
        r"\b[A-Za-z0-9]{40}\b",  # Tokens
    ]

    def __init__(
        self,
        tenant_id: str,
        skill_id: str,
        emitter: Optional[Any] = None,
    ):
        """Initialize optimizer.

        Args:
            tenant_id: Tenant scope
            skill_id: Skill being optimized (e.g., "os.delegation_router")
            emitter: EventEmitter for audit logging
        """
        self.tenant_id = tenant_id
        self.skill_id = skill_id
        self.emitter = emitter
        self.convergence_detector = ConvergenceDetector()

    def optimize_from_signal(
        self,
        signal: FeedbackSignal,
        current_config: Dict[str, Any],
        outcomes: List[Dict[str, Any]],  # Recent task outcomes
    ) -> OptimizationDecision:
        """Compute parameter delta from feedback signal.

        Args:
            signal: Aggregated FeedbackSignal from ingester
            current_config: Current Skill config (manifest)
            outcomes: Recent task outcomes (success/failure)

        Returns:
            OptimizationDecision with deltas (if safe) or rejection reason
        """
        decision = OptimizationDecision(
            should_update=False,
            reason="unknown",
            old_config=current_config.copy(),
        )

        # Extract success rate from outcomes
        if not outcomes:
            decision.reason = "no_outcomes_available"
            return decision

        success_count = sum(1 for o in outcomes if o.get("success"))
        success_rate = success_count / len(outcomes) if outcomes else 0.5

        # Add to convergence detector
        self.convergence_detector.add_sample(success_rate)

        # Check convergence
        has_converged, convergence_reason = self.convergence_detector.has_converged()
        if has_converged:
            decision.reason = convergence_reason
            decision.confidence_score = self.convergence_detector.compute_confidence()
            decision.slope = self.convergence_detector.compute_slope()
            return decision

        # Compute parameter delta (proportional to gap from target)
        gap = self.TARGET_SUCCESS_RATE - success_rate
        delta_magnitude = gap * 0.5  # Dampening factor (conservative)

        # Clamp delta to max bounds (±1σ)
        if abs(delta_magnitude) > self.MAX_PARAMETER_DELTA:
            decision.reason = "bounds_enforced"
            decision.validation_passed = False
            return decision

        # Validate PII safety (fail-closed)
        config_json = json.dumps(current_config)
        import re
        for pattern in self.PII_PATTERNS:
            if re.search(pattern, config_json):
                logger.error(f"PII pattern detected in config: {pattern}")
                decision.reason = "pii_detected"
                decision.validation_passed = False
                return decision

        # Construct new config
        new_config = current_config.copy()

        # Update parameters (example: if optimizing a threshold)
        # This is skill-specific; here we demonstrate with a generic "threshold" param
        if "routing_threshold" in new_config:
            old_threshold = float(new_config["routing_threshold"])
            new_threshold = old_threshold + delta_magnitude
            new_threshold = max(0.0, min(1.0, new_threshold))  # Clamp [0, 1]
            new_config["routing_threshold"] = new_threshold

        if "context_weight" in new_config:
            old_weight = float(new_config["context_weight"])
            new_weight = old_weight + delta_magnitude
            new_weight = max(0.0, min(1.0, new_weight))  # Clamp [0, 1]
            new_config["context_weight"] = new_weight

        # Compute deltas for audit logging
        deltas = {}
        for key in new_config:
            if key in current_config:
                if isinstance(new_config[key], (int, float)):
                    deltas[key] = new_config[key] - current_config[key]

        decision.should_update = True
        decision.reason = "safe_delta"
        decision.parameter_deltas = deltas
        decision.old_config = current_config.copy()
        decision.new_config = new_config
        decision.confidence_score = self.convergence_detector.compute_confidence()
        decision.slope = self.convergence_detector.compute_slope()
        decision.validation_passed = True

        return decision

    async def execute_optimization_epoch(
        self,
        signal: FeedbackSignal,
        current_config: Dict[str, Any],
        outcomes: List[Dict[str, Any]],
    ) -> OptimizationDecision:
        """Execute one optimization epoch: compute delta, validate, update config.

        Args:
            signal: FeedbackSignal
            current_config: Current Skill config
            outcomes: Recent task outcomes

        Returns:
            OptimizationDecision (audit-logged)
        """
        decision = self.optimize_from_signal(signal, current_config, outcomes)

        # Emit audit event (audit-first, before any side effects)
        if self.emitter:
            try:
                audit_event = LearningEvent.create(
                    event_type=EventType.CONFIG_UPDATED if decision.should_update else EventType.METRIC,
                    skill_id=self.skill_id,
                    tenant_id=self.tenant_id,
                    signal={
                        "optimizer_epoch": True,
                        "decision": decision.reason,
                        "should_update": decision.should_update,
                        "parameter_deltas": decision.parameter_deltas,
                        "confidence": decision.confidence_score,
                        "slope": decision.slope,
                    },
                    lom="core/learning/skill_optimizer_loop.py:execute_optimization_epoch",
                )
                self.emitter.emit(audit_event)
                decision.audit_event = audit_event
            except Exception as e:
                logger.warning(f"Failed to emit optimizer audit event: {e}")

        logger.info(
            f"Optimization epoch for {self.skill_id}: {decision.reason} "
            f"(confidence={decision.confidence_score:.3f}, slope={decision.slope:.4f})"
        )

        return decision

    async def apply_config_update(
        self,
        decision: OptimizationDecision,
        manifest_path: Optional[Path] = None,
    ) -> bool:
        """Apply config update to Skill manifest.

        Args:
            decision: OptimizationDecision from execution
            manifest_path: Path to Skill manifest file (optional, for testing)

        Returns:
            True if update successful
        """
        if not decision.should_update:
            return False

        if not manifest_path:
            # Default: skill manifest in tenant home
            manifest_path = (
                tenant_home(self.tenant_id)
                / "global"
                / "skills"
                / f"{self.skill_id}.json"
            )

        try:
            # Read current manifest
            manifest = {}
            if manifest_path.exists():
                with open(manifest_path, "r") as f:
                    manifest = json.load(f)

            # Update config
            manifest["config"] = decision.new_config
            manifest["last_optimizer_update"] = datetime.now(timezone.utc).isoformat() + "Z"
            manifest["optimizer_epochs"] = manifest.get("optimizer_epochs", 0) + 1

            # Write manifest (atomic: write temp, then rename)
            temp_path = manifest_path.with_suffix(".json.tmp")
            manifest_path.parent.mkdir(parents=True, exist_ok=True)

            with open(temp_path, "w") as f:
                json.dump(manifest, f, indent=2)

            temp_path.replace(manifest_path)

            logger.info(
                f"Updated {self.skill_id} config: deltas={decision.parameter_deltas}, "
                f"path={manifest_path}"
            )
            return True

        except Exception as e:
            logger.error(f"Failed to apply config update to {manifest_path}: {e}")
            return False


__all__ = [
    "SkillOptimizerLoop",
    "OptimizationDecision",
    "ConvergenceDetector",
]
