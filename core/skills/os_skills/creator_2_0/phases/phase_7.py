"""Phase 7: Optimization — Performance and readability improvements."""

from dataclasses import dataclass
from typing import Dict, List, Any
import time
from ..events import PhaseCompletedEvent, LossComponents, LossEmitter


@dataclass
class OptimizationRequest:
    """Request for optimization phase."""
    skill_id: str
    code_generated: str
    lines_of_code: int


@dataclass
class OptimizationResult:
    """Output of optimization phase."""
    skill_id: str
    optimizations_applied: List[str]
    estimated_speedup: float  # 1.0 = no speedup
    code_quality_score: float  # 0–1


class OptimizationPhase:
    """Phase 7: Optimize for performance and readability."""

    def execute(self, request: OptimizationRequest, emitter: LossEmitter) -> OptimizationResult:
        """Execute optimization phase."""
        start = time.time()
        errors = []

        # Apply optimizations
        optimizations = self._identify_optimizations(request.code_generated)
        speedup = 1.0 + (len(optimizations) * 0.05)  # Each optimization adds ~5% speedup
        quality_score = self._calculate_quality_score(request.lines_of_code, len(optimizations))

        result = OptimizationResult(
            skill_id=request.skill_id,
            optimizations_applied=optimizations,
            estimated_speedup=speedup,
            code_quality_score=quality_score,
        )

        # Loss: more optimizations = better performance
        relevance = 0.9
        completeness = 1.0
        performance = min(1.0, 0.5 + (len(optimizations) * 0.1))  # Based on optimizations
        maintainability = 0.9

        event = PhaseCompletedEvent(
            phase_num=7,
            skill_id=request.skill_id,
            duration_ms=(time.time() - start) * 1000,
            errors=errors,
            loss_components=LossComponents(
                relevance=relevance,
                completeness=completeness,
                performance=performance,
                maintainability=maintainability,
            ),
        )
        emitter.emit(event)

        return result

    @staticmethod
    def _identify_optimizations(code: str) -> List[str]:
        """Identify applicable optimizations."""
        optimizations = []

        # Simple heuristics
        if "for" in code:
            optimizations.append("Consider list comprehensions")
        if "def" in code:
            optimizations.append("Add type hints")
        if len(code) > 500:
            optimizations.append("Break into smaller functions")

        return optimizations[:3]  # Limit to 3

    @staticmethod
    def _calculate_quality_score(loc: int, optimization_count: int) -> float:
        """Calculate code quality score (0–1)."""
        # Ideal: ~100–200 LOC with 2+ optimizations
        loc_score = max(0, 1.0 - abs(loc - 150) / 300)
        opt_score = min(1.0, optimization_count / 3.0)
        return (loc_score + opt_score) / 2.0
