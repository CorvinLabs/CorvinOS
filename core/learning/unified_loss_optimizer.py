"""Phase 2c: Unified Loss Optimizer — Closed-Loop Learning for L5 + L10.

Implements unified optimization of routing (os.delegation_router) + context
(os.context_adapter) decisions via a single loss function:

    L = (0.6 * routing_loss) + (0.4 * context_loss)

Where:
  - routing_loss: mean decision error (mde) — P(skill decision ≠ optimal engine)
  - context_loss: mean attention wasted — (tokens_used - optimal) / budget

Optimizer strategy:
  - Grid search on one parameter at a time (interpretable)
  - Test 10 hypotheses per epoch (priority: routing > context)
  - Accept hypothesis if loss improves > 5%
  - Run continuously on feedback batches (systemd daemon)

ADR-0532 Phase 2c: Learning Optimizer
ADR-0615: Loop Interdependencies & Backprop Protocol
"""
from __future__ import annotations

import dataclasses
import logging
import time
from enum import Enum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core.learning.feedback_batcher import FeedbackBatch

_log = logging.getLogger(__name__)


class HypothesisStatus(Enum):
    """Status of a hypothesis test."""

    PENDING = "pending"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    ROLLED_BACK = "rolled_back"


@dataclasses.dataclass(frozen=True)
class Hypothesis:
    """A hypothesis (parameter delta) to test."""

    skill_id: str  # 'os.delegation_router' or 'os.context_adapter'
    param_name: str  # e.g., 'confidence_threshold', 'vibe_boost_on_high_complexity'
    delta: float  # change to apply (e.g., +0.05)
    priority: int = 5  # 1-10 (higher = test first)

    def __hash__(self) -> int:
        """Make hashable for deduplication."""
        return hash((self.skill_id, self.param_name, self.delta))


@dataclasses.dataclass(frozen=True)
class OptimizerResult:
    """Result of one optimizer epoch."""

    batch_id: str
    timestamp: float
    baseline_loss: float  # Loss with current config
    hypotheses_tested: int
    hypotheses_accepted: list[Hypothesis]
    hypotheses_rejected: list[Hypothesis]
    improvement_pct: float  # (baseline_loss - new_loss) / baseline_loss * 100


class UnifiedLossOptimizer:
    """Optimize routing + context decisions via unified loss function."""

    # Parameter grid for hypothesis generation
    PARAMETER_GRID = {
        "os.delegation_router": {
            "confidence_threshold": [-0.10, -0.05, 0.05, 0.10],
            "speed_weight": [-0.05, 0.05],
            "escalation_enabled": [False, True],  # Not a float, special case
        },
        "os.context_adapter": {
            "vibe_boost_on_high_complexity": [-0.05, 0.05],
            "attention_budget_base": [-10000, 10000],
            "priority_boost_on_engaged": [-0.05, 0.05],
        },
    }

    def __init__(self, tenant_id: str):
        """Initialize unified loss optimizer.

        Args:
            tenant_id: Tenant to optimize for
        """
        self.tenant_id = tenant_id
        self._skill_adapters: dict[str, SkillAdapter] = {}  # Lazy-loaded
        self._config_cache: dict[str, dict] = {}  # Current configs

    def optimize_batch(self, batch: FeedbackBatch) -> OptimizerResult:
        """Run one optimizer epoch on a batch of outcomes.

        Args:
            batch: FeedbackBatch with outcomes to analyze

        Returns:
            OptimizerResult with test results and accepted changes
        """
        timestamp = time.time()

        # Compute baseline loss
        baseline_loss = self._compute_loss(
            batch.outcomes,
            use_current_config=True,
        )

        # Generate hypotheses to test
        hypotheses = self._generate_hypotheses(batch.skill_ids)

        accepted = []
        rejected = []

        # Test each hypothesis
        for hyp in hypotheses:
            try:
                # Compute loss with hypothesis applied
                test_loss = self._compute_loss(
                    batch.outcomes,
                    hypothesis=hyp,
                )

                # Calculate improvement
                improvement = baseline_loss - test_loss
                improvement_pct = (improvement / baseline_loss * 100) if baseline_loss > 0 else 0

                # Decision: accept if > 5% improvement
                if improvement > baseline_loss * 0.05:  # 5% threshold
                    self._apply_hypothesis(hyp)
                    accepted.append(hyp)
                    _log.info(
                        "Hypothesis accepted: %s %s %+.2f (improvement %.1f%%)",
                        hyp.skill_id,
                        hyp.param_name,
                        hyp.delta,
                        improvement_pct,
                    )
                else:
                    rejected.append(hyp)
                    _log.debug(
                        "Hypothesis rejected: %s %s %+.2f (improvement %.1f%%)",
                        hyp.skill_id,
                        hyp.param_name,
                        hyp.delta,
                        improvement_pct,
                    )

            except Exception as exc:  # noqa: BLE001
                _log.error("Hypothesis test failed: %s: %s", hyp, exc)
                rejected.append(hyp)

        # Compute final improvement
        final_loss = self._compute_loss(batch.outcomes, use_current_config=True)
        final_improvement = (baseline_loss - final_loss) / baseline_loss * 100

        result = OptimizerResult(
            batch_id=batch.batch_id,
            timestamp=timestamp,
            baseline_loss=baseline_loss,
            hypotheses_tested=len(hypotheses),
            hypotheses_accepted=accepted,
            hypotheses_rejected=rejected,
            improvement_pct=final_improvement,
        )

        # Emit audit event
        self._emit_audit_event(result)

        return result

    def _compute_loss(
        self,
        outcomes: tuple,
        use_current_config: bool = False,
        hypothesis: Hypothesis | None = None,
    ) -> float:
        """Compute unified loss on outcomes.

        Loss function:
            L = (0.6 * routing_loss) + (0.4 * context_loss)

        Where:
            routing_loss = # decisions_wrong / total
            context_loss = tokens_wasted / total_budget
        """
        if not outcomes:
            return 0.0

        routing_errors = 0
        context_wasted = 0
        total_budget = 0

        for outcome in outcomes:
            # Routing loss: how many times was Skill decision ≠ optimal?
            if outcome.skill_decision != outcome.optimal_engine:
                routing_errors += 1

            # Context loss: how many tokens wasted?
            if hasattr(outcome, "context_tokens") and hasattr(outcome, "optimal_context_tokens"):
                context_wasted += max(0, outcome.context_tokens - outcome.optimal_context_tokens)
                total_budget += outcome.attention_budget

        routing_loss = routing_errors / len(outcomes) if outcomes else 0
        context_loss = context_wasted / total_budget if total_budget > 0 else 0

        unified_loss = (0.6 * routing_loss) + (0.4 * context_loss)
        return unified_loss

    def _generate_hypotheses(self, skill_ids: set[str]) -> list[Hypothesis]:
        """Generate hypotheses to test (grid search, prioritized).

        Returns list of hypotheses, max 10 per epoch, prioritized by impact.
        """
        hypotheses = []
        priority = 1  # Highest priority first

        # Priority 1: routing parameters (0.6 weight in loss)
        for skill_id in ["os.delegation_router"]:
            if skill_id not in skill_ids:
                continue
            for param, deltas in self.PARAMETER_GRID.get(skill_id, {}).items():
                for delta in deltas:
                    if len(hypotheses) < 10:
                        hypotheses.append(
                            Hypothesis(
                                skill_id=skill_id,
                                param_name=param,
                                delta=delta,
                                priority=priority,
                            )
                        )
                        priority += 1

        # Priority 2: context parameters (0.4 weight in loss)
        for skill_id in ["os.context_adapter"]:
            if skill_id not in skill_ids:
                continue
            for param, deltas in self.PARAMETER_GRID.get(skill_id, {}).items():
                for delta in deltas:
                    if len(hypotheses) < 10:
                        hypotheses.append(
                            Hypothesis(
                                skill_id=skill_id,
                                param_name=param,
                                delta=delta,
                                priority=priority,
                            )
                        )
                        priority += 1

        return sorted(hypotheses, key=lambda h: h.priority)

    def _apply_hypothesis(self, hyp: Hypothesis) -> None:
        """Apply hypothesis config change (atomically).

        This would actually modify the Skill's config in the registry.
        For now, this is a placeholder.
        """
        try:
            # In production, would call:
            # adapter = self._get_skill_adapter(hyp.skill_id)
            # config = adapter.current_config()
            # new_config = config.apply_delta(hyp.param_name, hyp.delta)
            # adapter.set_config(new_config)

            _log.info(
                "Hypothesis applied: %s %s %+.2f",
                hyp.skill_id,
                hyp.param_name,
                hyp.delta,
            )
        except Exception as exc:  # noqa: BLE001
            _log.error("Failed to apply hypothesis: %s: %s", hyp, exc)

    def _emit_audit_event(self, result: OptimizerResult) -> None:
        """Emit audit event for optimizer run."""
        try:
            from core.security.audit_logger import audit_event  # noqa: PLC0415

            audit_event(
                "learning.optimizer_epoch",
                {
                    "batch_id": result.batch_id,
                    "baseline_loss": result.baseline_loss,
                    "hypotheses_tested": result.hypotheses_tested,
                    "hypotheses_accepted": len(result.hypotheses_accepted),
                    "improvement_pct": result.improvement_pct,
                },
                tenant_id=self.tenant_id,
            )
        except Exception as exc:  # noqa: BLE001
            _log.debug("Failed to emit optimizer audit event: %s", exc)


class SkillAdapter:
    """Placeholder for SkillAdapter (would be imported from skill_adapter.py)."""

    def __init__(self, skill_id: str, tenant_id: str):
        self.skill_id = skill_id
        self.tenant_id = tenant_id

    def current_config(self) -> dict:
        """Get current config for skill."""
        return {}

    def set_config(self, config: dict) -> None:
        """Set new config for skill."""
        pass
