"""Phase 3b: Checkpoint — Verify plan is sensible before proceeding."""

from dataclasses import dataclass
from typing import Dict, List, Any
import time
from ..events import PhaseCompletedEvent, LossComponents, LossEmitter


@dataclass
class CheckpointRequest:
    """Request for checkpoint phase."""
    skill_id: str
    architecture: Dict[str, Any]
    functions: List[Dict[str, Any]]
    dependencies: List[str]


@dataclass
class CheckpointResult:
    """Output of checkpoint phase."""
    skill_id: str
    is_sensible: bool
    issues: List[str]
    recommendations: List[str]


class CheckpointPhase:
    """Phase 3b: Validate plan before proceeding."""

    def execute(self, request: CheckpointRequest, emitter: LossEmitter) -> CheckpointResult:
        """Execute checkpoint phase."""
        start = time.time()
        issues = []
        recommendations = []

        # Validate architecture
        if not request.functions:
            issues.append("No functions defined")
        if len(request.functions) > 20:
            issues.append("Too many functions (>20); consider breaking into smaller skill")
            recommendations.append("Simplify the skill to focus on one core functionality")

        # Validate dependencies
        if len(request.dependencies) > 10:
            issues.append("Too many dependencies (>10); reduces portability")
            recommendations.append("Consider using standard library only")

        # Check for reasonable function signatures
        for func in request.functions:
            if not func.get("name") or not func.get("returns"):
                issues.append(f"Function {func.get('name', 'unknown')} missing required fields")

        is_sensible = len(issues) == 0

        result = CheckpointResult(
            skill_id=request.skill_id,
            is_sensible=is_sensible,
            issues=issues,
            recommendations=recommendations,
        )

        # Loss: fewer issues = better plan
        relevance = 0.95
        completeness = 1.0 - min(1.0, len(issues) * 0.2)
        performance = 1.0 if len(request.dependencies) <= 5 else 0.8
        maintainability = 1.0 if len(request.functions) <= 5 else 0.85

        event = PhaseCompletedEvent(
            phase_num=3,  # Checkpoint uses phase 3 numbering
            skill_id=request.skill_id,
            duration_ms=(time.time() - start) * 1000,
            errors=issues,
            loss_components=LossComponents(
                relevance=relevance,
                completeness=completeness,
                performance=performance,
                maintainability=maintainability,
            ),
            metadata={"checkpoint_pass": is_sensible},
        )
        emitter.emit(event)

        return result
