"""Skill Optimizer — A/B testing & parameter tuning (ADR-0683, Phase 7).

Reads learning events from SkillLearningLoop and proposes parameter tuning.
Performs A/B testing on a subset of requests; promotes variant B if improvement >5%.

The optimizer is fail-closed: if tuning breaks the skill, rolls back to variant A.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, asdict
from datetime import datetime
from enum import Enum
from typing import Optional, Dict, Any
from uuid import uuid4

logger = logging.getLogger(__name__)


class ParameterType(str, Enum):
    """Tunable parameter types."""

    PROMPT_SYSTEM = "prompt.system"  # System message
    PROMPT_EXAMPLES = "prompt.examples"  # Few-shot examples
    TEMPERATURE = "temperature"  # LLM generation temperature (0.0-1.0)
    TOP_P = "top_p"  # LLM nucleus sampling (0.0-1.0)
    MAX_RETRIES = "max_retries"  # Retry count (1-5)
    TIMEOUT_MS = "timeout_ms"  # Timeout in milliseconds
    CONFIDENCE_THRESHOLD = "confidence_threshold"  # Router threshold


@dataclass
class SkillConfig:
    """Configuration snapshot for a skill."""

    skill_id: str
    version: str
    parameters: Dict[str, Any]
    created_at: datetime = None

    def __post_init__(self):
        if self.created_at is None:
            self.created_at = datetime.utcnow()

    def copy(self) -> SkillConfig:
        """Create a deep copy of this config."""
        return SkillConfig(
            skill_id=self.skill_id,
            version=self.version,
            parameters=self.parameters.copy(),
            created_at=self.created_at,
        )


@dataclass
class ABTestResult:
    """Result of an A/B test."""

    test_id: str
    skill_id: str
    variant_a: SkillConfig
    variant_b: SkillConfig

    # Results
    samples_a: int = 0
    samples_b: int = 0
    success_rate_a: float = 0.0
    success_rate_b: float = 0.0
    latency_a_ms: float = 0.0
    latency_b_ms: float = 0.0

    # Decision
    improvement_pct: float = 0.0  # (B - A) / A
    promoted: bool = False  # Was B promoted?
    reason: str = ""  # Why promoted or reverted

    # Metadata
    started_at: datetime = None
    completed_at: datetime = None

    def __post_init__(self):
        if self.started_at is None:
            self.started_at = datetime.utcnow()

    def to_dict(self) -> Dict[str, Any]:
        """Convert to JSON-serializable dict."""
        return {
            'test_id': self.test_id,
            'skill_id': self.skill_id,
            'variant_a': asdict(self.variant_a),
            'variant_b': asdict(self.variant_b),
            'samples_a': self.samples_a,
            'samples_b': self.samples_b,
            'success_rate_a': round(self.success_rate_a, 3),
            'success_rate_b': round(self.success_rate_b, 3),
            'latency_a_ms': round(self.latency_a_ms, 1),
            'latency_b_ms': round(self.latency_b_ms, 1),
            'improvement_pct': round(self.improvement_pct, 1),
            'promoted': self.promoted,
            'reason': self.reason,
            'started_at': self.started_at.isoformat() if self.started_at else None,
            'completed_at': self.completed_at.isoformat() if self.completed_at else None,
        }


class SkillOptimizer:
    """Optimize skill parameters based on learning signals.

    Workflow:
    1. Collect recent executions + feedback from SkillLearningLoop
    2. Propose parameter tuning (variant B)
    3. Run A/B test: route subset of requests to variant B, compare
    4. Decide: promote B if improvement >5%, revert otherwise
    5. Audit all decisions
    """

    # A/B testing parameters
    MIN_SAMPLES_PER_VARIANT = 10  # Minimum executions per variant
    IMPROVEMENT_THRESHOLD = 0.05  # Need 5%+ improvement to promote
    TEST_DURATION = 50  # Run A/B for 50 more executions

    def __init__(self, skill_id: str, learning_loop: object):
        """Initialize optimizer.

        Args:
            skill_id: Skill ID (e.g., "org/skill-name")
            learning_loop: SkillLearningLoop instance
        """
        self.skill_id = skill_id
        self.learning_loop = learning_loop

        # Current and candidate configs
        self.current_config = SkillConfig(
            skill_id=skill_id,
            version='1.0.0',
            parameters=self._get_default_parameters(),
        )

        # Active A/B test (if any)
        self.active_test: Optional[ABTestResult] = None
        self.test_sample_count = 0

        # Test history
        self.test_history: list[ABTestResult] = []

        # Tuning history
        self.parameter_history: list[Dict[str, Any]] = []

        logger.info(f"SkillOptimizer initialized: {skill_id}")

    # ─────────────────────────────────────────────────────────────────────────
    # Parameter Tuning Proposal
    # ─────────────────────────────────────────────────────────────────────────

    def propose_tuning(self) -> Optional[SkillConfig]:
        """Propose parameter tuning based on learning signals.

        Analyzes feedback and decides which parameters to adjust:
        - High error rate → increase max_retries
        - High latency → decrease timeout, reduce prompt size
        - Low quality ratings → improve prompt (via LLM)
        - High variance → increase temperature

        Returns:
            Variant B (tuned config), or None if tuning not recommended
        """
        stats = self.learning_loop.get_stats()

        # Not enough feedback yet
        if stats.feedback_count < 3:
            logger.debug(
                f"Optimizer: insufficient feedback ({stats.feedback_count} < 3), "
                f"skipping tuning proposal"
            )
            return None

        # Create variant B
        variant_b = self.current_config.copy()
        variant_b.version = f"{self.current_config.version}+tuned"

        # Analyze feedback and propose changes
        changed = False

        # High error rate → retry more aggressively
        if stats.error_count > 0:
            error_rate = stats.error_count / stats.execution_count
            if error_rate > 0.2:  # >20% errors
                current_retries = variant_b.parameters.get('max_retries', 1)
                variant_b.parameters['max_retries'] = min(5, current_retries + 1)
                changed = True
                logger.debug(
                    f"Optimizer: increasing retries to {variant_b.parameters['max_retries']} "
                    f"(error_rate={error_rate:.1%})"
                )

        # High latency → increase timeout or reduce scope
        if stats.avg_latency_ms > 2000:  # >2 seconds
            current_timeout = variant_b.parameters.get('timeout_ms', 5000)
            variant_b.parameters['timeout_ms'] = min(10000, current_timeout + 1000)
            changed = True
            logger.debug(
                f"Optimizer: increasing timeout to {variant_b.parameters['timeout_ms']}ms "
                f"(latency={stats.avg_latency_ms:.0f}ms)"
            )

        # Low quality ratings → adjust temperature (more conservative)
        # (This would trigger LLM prompt optimization in production)
        avg_rating = self._get_average_rating()
        if avg_rating is not None and avg_rating < 3.0:  # <3 stars
            current_temp = variant_b.parameters.get('temperature', 0.7)
            # Reduce temperature for more deterministic/consistent output
            variant_b.parameters['temperature'] = max(0.0, current_temp - 0.1)
            changed = True
            logger.debug(
                f"Optimizer: reducing temperature to {variant_b.parameters['temperature']:.1f} "
                f"(avg_rating={avg_rating:.1f})"
            )

        if not changed:
            logger.debug("Optimizer: no tuning recommended (all metrics healthy)")
            return None

        logger.info(
            f"Optimizer: proposed tuning for {self.skill_id} "
            f"(changes={len([k for k in variant_b.parameters if variant_b.parameters.get(k) != self.current_config.parameters.get(k)])})"
        )

        return variant_b

    def start_ab_test(self, variant_b: SkillConfig) -> str:
        """Start an A/B test between current config and proposed variant.

        Args:
            variant_b: Proposed configuration

        Returns:
            Test ID
        """
        test_id = str(uuid4())

        self.active_test = ABTestResult(
            test_id=test_id,
            skill_id=self.skill_id,
            variant_a=self.current_config.copy(),
            variant_b=variant_b,
        )
        self.test_sample_count = 0

        logger.info(
            f"A/B test started: {self.skill_id} "
            f"(test_id={test_id}, will run for {self.TEST_DURATION} executions)"
        )

        return test_id

    def record_ab_sample(self, execution_id: str, variant: str,
                        success: bool, latency_ms: float) -> None:
        """Record a sample from an A/B test.

        Args:
            execution_id: Execution ID
            variant: 'a' or 'b'
            success: Whether execution was successful
            latency_ms: Execution latency in milliseconds
        """
        if not self.active_test:
            return  # No active test

        self.test_sample_count += 1

        if variant.lower() == 'a':
            self.active_test.samples_a += 1
            self._update_running_stats_a(success, latency_ms)
        elif variant.lower() == 'b':
            self.active_test.samples_b += 1
            self._update_running_stats_b(success, latency_ms)

        # Check if test is complete
        total_samples = self.active_test.samples_a + self.active_test.samples_b
        if total_samples >= self.TEST_DURATION:
            self.finalize_ab_test()

    def _update_running_stats_a(self, success: bool, latency_ms: float) -> None:
        """Update running average for variant A."""
        if not self.active_test:
            return

        n = self.active_test.samples_a
        self.active_test.success_rate_a = (
            (self.active_test.success_rate_a * (n - 1) + (1 if success else 0)) / n
        )
        self.active_test.latency_a_ms = (
            (self.active_test.latency_a_ms * (n - 1) + latency_ms) / n
        )

    def _update_running_stats_b(self, success: bool, latency_ms: float) -> None:
        """Update running average for variant B."""
        if not self.active_test:
            return

        n = self.active_test.samples_b
        self.active_test.success_rate_b = (
            (self.active_test.success_rate_b * (n - 1) + (1 if success else 0)) / n
        )
        self.active_test.latency_b_ms = (
            (self.active_test.latency_b_ms * (n - 1) + latency_ms) / n
        )

    def finalize_ab_test(self) -> Optional[ABTestResult]:
        """Finalize A/B test and decide: promote B or revert to A.

        Promotion criteria:
        1. Variant B has sufficient samples (MIN_SAMPLES_PER_VARIANT)
        2. Improvement in success rate >IMPROVEMENT_THRESHOLD (5%)
        3. No regression in latency (max +10% degradation tolerated)

        Returns:
            ABTestResult with decision, or None if test not complete
        """
        if not self.active_test:
            return None

        test = self.active_test
        test.completed_at = datetime.utcnow()

        # Check if we have enough samples
        if test.samples_a < self.MIN_SAMPLES_PER_VARIANT or test.samples_b < self.MIN_SAMPLES_PER_VARIANT:
            test.promoted = False
            test.reason = f"Insufficient samples (A={test.samples_a}, B={test.samples_b})"
            logger.info(f"A/B test inconclusive: {test.reason}")
            self.active_test = None
            self.test_history.append(test)
            return test

        # Calculate improvement
        success_improvement = test.success_rate_b - test.success_rate_a
        latency_degradation = (test.latency_b_ms - test.latency_a_ms) / max(1, test.latency_a_ms)

        # Decision logic
        test.improvement_pct = (success_improvement / max(0.01, test.success_rate_a)) * 100

        # Promote if success improves AND latency doesn't degrade too much
        should_promote = (
            success_improvement >= self.IMPROVEMENT_THRESHOLD and
            latency_degradation <= 0.1  # Max 10% latency increase
        )

        if should_promote:
            # Promote variant B
            test.promoted = True
            test.reason = (
                f"Promoted: +{test.improvement_pct:.1f}% success rate "
                f"({test.success_rate_a:.1%} → {test.success_rate_b:.1%}), "
                f"latency {latency_degradation*100:+.0f}%"
            )
            self.current_config = test.variant_b.copy()

            # Record parameter change
            self._record_parameter_change(test.variant_a, test.variant_b, "promoted", test.test_id)

            logger.info(f"A/B test PROMOTED variant B: {test.reason}")
        else:
            # Revert to variant A
            test.promoted = False
            if success_improvement < self.IMPROVEMENT_THRESHOLD:
                test.reason = (
                    f"Reverted: insufficient improvement ({test.improvement_pct:.1f}% "
                    f"required {self.IMPROVEMENT_THRESHOLD * 100:.0f}%)"
                )
            else:
                test.reason = f"Reverted: latency degradation {latency_degradation*100:+.0f}%"

            logger.info(f"A/B test REVERTED to variant A: {test.reason}")

        self.active_test = None
        self.test_history.append(test)
        return test

    # ─────────────────────────────────────────────────────────────────────────
    # Helper Methods
    # ─────────────────────────────────────────────────────────────────────────

    def _get_default_parameters(self) -> Dict[str, Any]:
        """Return default parameter values."""
        return {
            'temperature': 0.7,
            'top_p': 0.9,
            'max_retries': 1,
            'timeout_ms': 5000,
            'confidence_threshold': 0.7,
        }

    def _get_average_rating(self) -> Optional[float]:
        """Get average feedback rating from learning loop."""
        # This would query the learning loop's feedback
        # For now, return None (no ratings available)
        return None

    def _record_parameter_change(self, variant_a: SkillConfig,
                                 variant_b: SkillConfig,
                                 action: str, test_id: str) -> None:
        """Record parameter change to history.

        Args:
            variant_a: Original config
            variant_b: Tuned config
            action: 'promoted' or 'reverted'
            test_id: A/B test ID
        """
        changes = {}
        for key in variant_b.parameters:
            if variant_a.parameters.get(key) != variant_b.parameters.get(key):
                changes[key] = {
                    'old': variant_a.parameters.get(key),
                    'new': variant_b.parameters.get(key),
                }

        record = {
            'timestamp': datetime.utcnow().isoformat(),
            'test_id': test_id,
            'action': action,
            'changes': changes,
        }

        self.parameter_history.append(record)
        logger.debug(f"Parameter change recorded: {action} ({len(changes)} changes)")

    # ─────────────────────────────────────────────────────────────────────────
    # Stats & Reporting
    # ─────────────────────────────────────────────────────────────────────────

    def get_config(self) -> SkillConfig:
        """Get current skill configuration.

        Returns:
            Current SkillConfig
        """
        return self.current_config

    def get_test_history(self, limit: int = 10) -> list[ABTestResult]:
        """Get recent A/B test results.

        Args:
            limit: Maximum number of tests to return

        Returns:
            List of ABTestResult (newest first)
        """
        return self.test_history[-limit:][::-1]

    def get_parameter_history(self, limit: int = 20) -> list[Dict[str, Any]]:
        """Get recent parameter changes.

        Args:
            limit: Maximum number of records to return

        Returns:
            List of parameter change records (newest first)
        """
        return self.parameter_history[-limit:][::-1]

    def rollback_to_version(self, version: str) -> bool:
        """Rollback to a previous configuration version.

        Args:
            version: Version string to rollback to

        Returns:
            True if rollback successful, False if version not found
        """
        # This would query configuration history
        # For now, just log a warning
        logger.warning(f"Rollback not yet implemented for version {version}")
        return False
