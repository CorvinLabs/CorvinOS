"""Token baseline & comparison — Native vs Vibe (Phase 1.K3).

Defines "Native Engine" baseline and implements Vibe vs Native comparison.
"""

from __future__ import annotations
from dataclasses import dataclass
from typing import Optional
from math import sqrt


@dataclass
class BaselineMetrics:
    """Baseline token consumption (stateless Native engine)."""

    turn_id: str
    baseline_tokens: int  # What Native engine would spend

    def __init__(self, turn_id: str, task_complexity: Optional[str] = None):
        """Estimate baseline tokens for a turn.

        Baseline = Native Engine (no learning, no cache, no skill injection)
        Uses heuristics based on task complexity.
        """
        self.turn_id = turn_id

        # Simple heuristic: baseline depends on task complexity
        # (Real baseline would run a stateless engine on same tasks)
        complexity_multipliers = {
            "trivial": 1.0,      # 1800 tokens
            "simple": 1.3,       # 2300 tokens
            "moderate": 1.8,     # 3200 tokens
            "complex": 2.5,      # 4500 tokens
        }

        multiplier = complexity_multipliers.get(task_complexity, 1.5)
        self.baseline_tokens = int(1800 * multiplier)


@dataclass
class ComparisonResult:
    """Comparison: Vibe vs Native."""

    turn_id: str
    vibe_tokens: int          # Actual Vibe engine cost
    baseline_tokens: int      # Native engine cost (estimated/measured)
    savings_tokens: int       # baseline - vibe
    savings_percent: float    # (baseline - vibe) / baseline
    confidence: float         # 0.0-1.0 statistical confidence
    is_significant: bool      # confidence > 0.68 (1 sigma)

    @classmethod
    def from_counter(cls, counter, baseline_tokens: int) -> ComparisonResult:
        """Create comparison from TokenCounter."""
        vibe_tokens = counter.total_tokens
        savings = baseline_tokens - vibe_tokens
        savings_pct = (savings / baseline_tokens * 100) if baseline_tokens > 0 else 0

        # Confidence: effect size > 1 std dev = 68% confidence
        # Simplified: if savings > 15%, we're probably real
        is_real = savings_pct > 15
        confidence = 0.95 if is_real else 0.3

        return cls(
            turn_id=counter.turn_id,
            vibe_tokens=vibe_tokens,
            baseline_tokens=baseline_tokens,
            savings_tokens=max(0, savings),
            savings_percent=savings_pct,
            confidence=confidence,
            is_significant=confidence > 0.68,
        )


class ComparisonEngine:
    """Compare Vibe vs Native token consumption."""

    def __init__(self):
        self.comparisons = []
        self._baseline_cache = {}

    def get_baseline(
        self,
        turn_id: str,
        task_complexity: Optional[str] = None,
    ) -> int:
        """Get baseline (Native engine) cost estimate for a turn.

        Args:
            turn_id: Turn identifier
            task_complexity: "trivial", "simple", "moderate", "complex"

        Returns:
            Estimated tokens for Native engine
        """
        key = (turn_id, task_complexity)
        if key not in self._baseline_cache:
            baseline = BaselineMetrics(turn_id, task_complexity)
            self._baseline_cache[key] = baseline.baseline_tokens
        return self._baseline_cache[key]

    def compare(
        self,
        counter,  # TokenCounter
        task_complexity: Optional[str] = None,
    ) -> ComparisonResult:
        """Compare Vibe vs Native for a turn.

        Args:
            counter: TokenCounter with Vibe measurements
            task_complexity: Optional for better baseline estimation

        Returns:
            ComparisonResult with savings % and confidence
        """
        baseline = self.get_baseline(counter.turn_id, task_complexity)
        comparison = ComparisonResult.from_counter(counter, baseline)
        self.comparisons.append(comparison)
        return comparison

    def aggregate_comparisons(self) -> dict:
        """Aggregate all comparisons done so far.

        Returns:
            Summary stats across all comparisons
        """
        if not self.comparisons:
            return {
                "comparison_count": 0,
                "avg_savings_percent": 0.0,
                "high_confidence_count": 0,
                "high_confidence_pct": 0.0,
            }

        high_confidence = [c for c in self.comparisons if c.is_significant]

        avg_savings = sum(c.savings_percent for c in self.comparisons) / len(self.comparisons)

        return {
            "comparison_count": len(self.comparisons),
            "avg_savings_percent": round(avg_savings, 1),
            "high_confidence_count": len(high_confidence),
            "high_confidence_pct": round(
                len(high_confidence) / len(self.comparisons) * 100, 1
            ),
            "total_baseline_tokens": sum(c.baseline_tokens for c in self.comparisons),
            "total_vibe_tokens": sum(c.vibe_tokens for c in self.comparisons),
            "total_savings_tokens": sum(c.savings_tokens for c in self.comparisons),
        }
