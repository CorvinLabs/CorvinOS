"""DoD_WeightOptimizer: Online learning from feedback (ADR-0722, ADR-2284 decision 1).

Fed by ``POST /v1/console/api/dod/feedback`` (``routes/dod_verifier_dashboard.py``); the
verifier applies ``recommend_next_weights`` (confidence > 0.5 per weight, clamped to
[0.05, 0.6], sum 1). Unknown task types are refused, never folded into another bucket.
"""

from typing import Dict, List, Optional
from dataclasses import dataclass, field


@dataclass
class OptimizationResult:
    """Result of a weight update."""
    task_type: str
    old_weights: Dict[str, float]
    new_weights: Dict[str, float]
    delta: float
    adjustments: List[str]  # Which checks were adjusted
    confidence: Dict[str, float]


class UnknownTaskType(ValueError):
    """A task type with no weight bucket. It is refused, never learned into another type."""


#: Live check keys (``skill.py``'s five checks) -> weight names. A name outside this map
#: adjusts nothing (it used to fall back to ``w_reach`` silently).
CHECK_TO_WEIGHT = {
    "reachability": "w_reach",
    "audit_trail": "w_audit",
    "test_evidence": "w_test",
    "docs_sync": "w_docs",
    "reproducibility": "w_repro",
}


def project_weights(weights: Dict[str, float], lo: float, hi: float) -> Dict[str, float]:
    """Clamp every weight into ``[lo, hi]`` and renormalise to sum 1 (ADR-2284 decision 1).

    Iterative: scale the free weights to the remaining mass, pin the ones that leave the box at
    the violated bound, repeat. Feasible whenever ``n*lo <= 1 <= n*hi`` (5 weights, [0.05, 0.6]).
    """
    keys = list(weights)
    if not keys:
        return {}
    fixed: Dict[str, float] = {}
    for _ in range(len(keys) + 1):
        free = [k for k in keys if k not in fixed]
        if not free:
            break
        remaining = 1.0 - sum(fixed.values())
        total = sum(max(0.0, weights[k]) for k in free)
        scaled = {k: (max(0.0, weights[k]) / total * remaining) if total > 0 else remaining / len(free)
                  for k in free}
        low = {k for k, v in scaled.items() if v < lo - 1e-12}
        high = {k for k, v in scaled.items() if v > hi + 1e-12}
        if not low and not high:
            fixed.update(scaled)
            break
        # pin one side per round: the side with the larger total violation
        if sum(lo - scaled[k] for k in low) >= sum(scaled[k] - hi for k in high):
            fixed.update({k: lo for k in low})
        else:
            fixed.update({k: hi for k in high})
    out = {k: min(hi, max(lo, fixed.get(k, lo))) for k in keys}
    total = sum(out.values())
    return {k: v / total for k, v in out.items()} if total > 0 else out


class DoD_WeightOptimizer:
    """Learn task-type-specific weights from operator feedback."""

    DEFAULT_WEIGHTS = {
        "w_reach": 0.20,
        "w_audit": 0.25,
        "w_test": 0.20,
        "w_docs": 0.20,
        "w_repro": 0.15,
    }

    #: One bucket per type. ``feature`` is the console verify route's default task type
    #: (ADR-2284 review: it used to be learned into ``cli_command`` silently).
    TASK_TYPES = ["cli_command", "api_endpoint", "lib_function", "plugin", "skill", "feature"]

    #: ADR-2284 decision 1: every applied weight lies in [MIN_WEIGHT, MAX_WEIGHT], sum 1.
    MIN_WEIGHT = 0.05
    MAX_WEIGHT = 0.6

    #: ADR-0723: a learned weight is used only when its confidence is ABOVE this.
    CONFIDENCE_GATE = 0.5

    #: Hard cap on how far ONE feedback event may move ONE weight, before
    #: normalisation. Without it `adjustment = delta * LEARNING_RATE` is
    #: unbounded in ``delta``: a single operator (or a compromised feedback
    #: channel) submitting ``delta=0.99`` moved ``w_audit`` by 0.247 in one
    #: step and could zero out the audit-trail check on the next. The
    #: adversarial test for exactly this ("one malicious feedback must have
    #: limited impact") existed since Phase 3 and had NEVER run, because the
    #: skill package was not importable (2026-09-20 review).
    MAX_ADJUSTMENT_PER_FEEDBACK = 0.05

    #: Proportional step size applied to the operator delta.
    LEARNING_RATE = 0.25

    #: Confidence gained per feedback event for an affected weight. Applied as
    #: `round(conf + STEP, 10)` because binary floats do not sum cleanly: eight
    #: naive `+= 0.1` steps land on 0.7999999999999999, which is BELOW the 0.8
    #: convergence threshold. The gate was therefore unreachable on the exact
    #: path the tests exercise, and `recommend_next_weights` kept returning the
    #: defaults forever — the learning loop looked wired and learned nothing.
    CONFIDENCE_STEP = 0.1

    def __init__(self):
        """Initialize optimizer with default weights."""
        # Per-task-type weight distributions
        self.weights = {
            task_type: self.DEFAULT_WEIGHTS.copy()
            for task_type in self.TASK_TYPES
        }

        # Confidence per weight (starts at 0.0)
        self.confidence = {
            task_type: {w: 0.0 for w in self.DEFAULT_WEIGHTS}
            for task_type in self.TASK_TYPES
        }

        # Sample count per task type (for convergence detection)
        self.sample_count = {task_type: 0 for task_type in self.TASK_TYPES}

    # ── persistence ──────────────────────────────────────────────────────────

    @classmethod
    def from_state(cls, state: Dict[str, Dict]) -> "DoD_WeightOptimizer":
        """An optimizer holding a persisted state (``WeightPersistence.load_state()``).

        Only known task types and weight names are taken; numbers only. Weights and
        confidence are both restored, so the confidence gate survives a restart.
        """
        opt = cls()
        weights = state.get("weights") or {}
        confidence = state.get("confidence") or {}
        samples = state.get("sample_count") or {}
        for tt in cls.TASK_TYPES:
            for name, value in (weights.get(tt) or {}).items():
                if name in cls.DEFAULT_WEIGHTS and _is_number(value):
                    opt.weights[tt][name] = float(value)
            for name, value in (confidence.get(tt) or {}).items():
                if name in cls.DEFAULT_WEIGHTS and _is_number(value):
                    opt.confidence[tt][name] = min(1.0, max(0.0, float(value)))
            if _is_number(samples.get(tt)):
                opt.sample_count[tt] = int(samples[tt])
            opt.weights[tt] = project_weights(opt.weights[tt], cls.MIN_WEIGHT, cls.MAX_WEIGHT)
        return opt

    @classmethod
    def is_learnable(cls, task_type: str) -> bool:
        return task_type in cls.TASK_TYPES

    # ── learning ─────────────────────────────────────────────────────────────

    def observe_feedback(self, task_type: str, delta: float, affected_checks: List[str]) -> OptimizationResult:
        """
        Process operator feedback → adjust weights.

        Args:
            task_type: one of ``TASK_TYPES``; any other raises ``UnknownTaskType``
            delta: Operator correction (positive = skill was too harsh)
            affected_checks: Which checks to adjust (live check keys; others adjust nothing)

        Returns:
            OptimizationResult with old/new weights + confidence
        """
        if task_type not in self.TASK_TYPES:
            raise UnknownTaskType(task_type)

        old_weights = self.weights[task_type].copy()

        if delta > 0.05:  # Operator score much higher than Skill's
            # Skill was too harsh. Reduce weights for affected checks.
            for check_name in affected_checks:
                w_name = self._check_to_weight(check_name)
                if w_name in self.weights[task_type]:
                    old_w = self.weights[task_type][w_name]
                    # Reduce weight (proportional to delta, hard-capped).
                    adjustment = min(
                        delta * self.LEARNING_RATE,
                        self.MAX_ADJUSTMENT_PER_FEEDBACK,
                    )
                    new_w = max(0.0, old_w - adjustment)
                    self.weights[task_type][w_name] = new_w
                    self.confidence[task_type][w_name] = round(
                        self.confidence[task_type][w_name] + self.CONFIDENCE_STEP, 10
                    )

        elif delta < -0.05:  # Operator score much lower than Skill's
            # Skill was too lenient. Increase weights for missing checks.
            for check_name in affected_checks:
                w_name = self._check_to_weight(check_name)
                if w_name in self.weights[task_type]:
                    old_w = self.weights[task_type][w_name]
                    adjustment = min(
                        abs(delta) * self.LEARNING_RATE,
                        self.MAX_ADJUSTMENT_PER_FEEDBACK,
                    )
                    new_w = min(1.0, old_w + adjustment)
                    self.weights[task_type][w_name] = new_w
                    self.confidence[task_type][w_name] = round(
                        self.confidence[task_type][w_name] + self.CONFIDENCE_STEP, 10
                    )

        # Clamp into [MIN_WEIGHT, MAX_WEIGHT] and normalise to sum 1 (ADR-2284): repeated
        # one-sided feedback can no longer drive a weight to 0 or past 0.6.
        self.weights[task_type] = project_weights(
            self.weights[task_type], self.MIN_WEIGHT, self.MAX_WEIGHT)

        # Cap confidence at 1.0
        for w in self.confidence[task_type]:
            self.confidence[task_type][w] = min(1.0, self.confidence[task_type][w])

        # Increment sample count
        self.sample_count[task_type] += 1

        return OptimizationResult(
            task_type=task_type,
            old_weights=old_weights,
            new_weights=self.weights[task_type].copy(),
            delta=delta,
            adjustments=affected_checks,
            confidence=self.confidence[task_type].copy(),
        )

    def recommend_next_weights(self, task_type: str) -> Dict[str, float]:
        """The weights the verifier applies for ``task_type`` (ADR-0723, ADR-2284).

        Per weight: the learned value only when its confidence is > ``CONFIDENCE_GATE``
        (0.5), else the default; then clamped to [0.05, 0.6] and normalised to sum 1. An
        unknown task type gets the defaults (nothing is ever learned for it).
        """
        if task_type not in self.TASK_TYPES:
            return self.DEFAULT_WEIGHTS.copy()

        result = {}
        for w_name, default in self.DEFAULT_WEIGHTS.items():
            conf = self.confidence[task_type].get(w_name, 0.0)
            result[w_name] = (self.weights[task_type][w_name] if conf > self.CONFIDENCE_GATE
                              else default)
        if not self.uses_learned(task_type):
            return result  # exactly the defaults
        return project_weights(result, self.MIN_WEIGHT, self.MAX_WEIGHT)

    def uses_learned(self, task_type: str) -> bool:
        """True when at least one weight of ``task_type`` is past the confidence gate."""
        if task_type not in self.TASK_TYPES:
            return False
        return any(c > self.CONFIDENCE_GATE for c in self.confidence[task_type].values())

    def is_converged(self, task_type: str, threshold: float = 0.8) -> bool:
        """
        Has this task_type converged?

        Converged = all weights have confidence >= threshold
        """
        if task_type not in self.TASK_TYPES:
            return False

        confs = self.confidence[task_type].values()
        return all(c >= threshold for c in confs)

    def _check_to_weight(self, check_name: str) -> Optional[str]:
        """Map a live check key to its weight name; ``None`` for anything else."""
        return CHECK_TO_WEIGHT.get(check_name)


def _is_number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and value == value


# Unit Tests
class TestDoD_WeightOptimizer:
    """Test weight optimization."""

    def test_learn_from_too_harsh_feedback(self):
        """Optimizer reduces weight when feedback says score was too harsh."""
        opt = DoD_WeightOptimizer()
        old_w_audit = opt.weights["api_endpoint"]["w_audit"]

        result = opt.observe_feedback(
            task_type="api_endpoint",
            delta=0.20,  # Operator much higher
            affected_checks=["audit_trail"]
        )

        new_w_audit = result.new_weights["w_audit"]
        assert new_w_audit < old_w_audit  # Weight reduced
        assert result.delta == 0.20

    def test_learn_from_too_lenient_feedback(self):
        """Optimizer increases weight when feedback says score was too lenient."""
        opt = DoD_WeightOptimizer()
        old_w_reach = opt.weights["cli_command"]["w_reach"]

        result = opt.observe_feedback(
            task_type="cli_command",
            delta=-0.15,  # Operator much lower
            affected_checks=["reachability"]
        )

        new_w_reach = result.new_weights["w_reach"]
        assert new_w_reach > old_w_reach  # Weight increased

    def test_weights_normalize(self):
        """Weights always sum to 1.0 after update."""
        opt = DoD_WeightOptimizer()

        for _ in range(10):
            opt.observe_feedback(
                task_type="api_endpoint",
                delta=0.10,
                affected_checks=["audit_trail"]
            )

        total = sum(opt.weights["api_endpoint"].values())
        assert abs(total - 1.0) < 0.01

    def test_convergence_detection(self):
        """Convergence detected when confidence >= 0.8."""
        opt = DoD_WeightOptimizer()

        # Not converged initially
        assert not opt.is_converged("api_endpoint")

        # Simulate 8+ feedback events (confidence = 0.8+) on EVERY check: convergence
        # means all five weights are confident, so feedback on one check never converges.
        for _ in range(8):
            opt.observe_feedback(
                task_type="api_endpoint",
                delta=0.10,
                affected_checks=list(CHECK_TO_WEIGHT),
            )

        # Should be converged
        assert opt.is_converged("api_endpoint")

    def test_recommend_uses_learned_weights(self):
        """Recommend uses learned weights if confidence >= 0.8."""
        opt = DoD_WeightOptimizer()

        # Low confidence: use defaults
        weights_low_conf = opt.recommend_next_weights("api_endpoint")
        assert weights_low_conf["w_audit"] == opt.DEFAULT_WEIGHTS["w_audit"]

        # Build confidence
        for _ in range(8):
            opt.observe_feedback(
                task_type="api_endpoint",
                delta=0.15,
                affected_checks=["audit_trail"]
            )

        # High confidence: use learned
        weights_high_conf = opt.recommend_next_weights("api_endpoint")
        assert weights_high_conf["w_audit"] != opt.DEFAULT_WEIGHTS["w_audit"]


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])
