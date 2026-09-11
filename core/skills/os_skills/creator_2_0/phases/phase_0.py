"""Phase 0: Intake — Parse user query, identify skill/tool intent."""

from dataclasses import dataclass
from typing import Dict, Any, Optional
from ..events import PhaseCompletedEvent, LossComponents, LossEmitter


@dataclass
class IntakeRequest:
    """User input for skill creation."""
    query: str  # Natural language description of the skill/tool
    mode: str = "skill"  # "skill" or "tool"
    context: Optional[Dict[str, Any]] = None


@dataclass
class IntakeResult:
    """Output of intake phase."""
    skill_id: str  # Generated unique ID
    description: str  # Parsed description
    mode: str  # "skill" or "tool"
    intent: str  # High-level intent (e.g., "data processing", "api validation")


class IntakePhase:
    """Phase 0: Parse user input."""

    def execute(self, request: IntakeRequest, emitter: LossEmitter) -> IntakeResult:
        """Execute intake phase."""
        import time
        start = time.time()

        # Validate input
        if not request.query or not isinstance(request.query, str):
            errors = ["Invalid query: must be non-empty string"]
            event = PhaseCompletedEvent(
                phase_num=0,
                skill_id="unknown",
                duration_ms=(time.time() - start) * 1000,
                errors=errors,
                loss_components=LossComponents(relevance=0.0, completeness=0.0, performance=0.0, maintainability=0.0),
            )
            emitter.emit(event)
            raise ValueError(errors[0])

        mode = request.mode if request.mode in ["skill", "tool"] else "skill"

        # Generate skill ID from query
        skill_id = "skill_" + request.query[:20].lower().replace(" ", "_").replace("-", "_")
        skill_id = "".join(c for c in skill_id if c.isalnum() or c == "_")

        # Parse intent (simple heuristic)
        query_lower = request.query.lower()
        if any(w in query_lower for w in ["validate", "check", "verify"]):
            intent = "validation"
        elif any(w in query_lower for w in ["process", "transform", "convert"]):
            intent = "data_processing"
        elif any(w in query_lower for w in ["classify", "categorize", "identify"]):
            intent = "classification"
        elif any(w in query_lower for w in ["api", "endpoint", "request"]):
            intent = "api_integration"
        else:
            intent = "utility"

        result = IntakeResult(
            skill_id=skill_id,
            description=request.query,
            mode=mode,
            intent=intent,
        )

        # Emit event (phase 0 has high confidence)
        event = PhaseCompletedEvent(
            phase_num=0,
            skill_id=skill_id,
            duration_ms=(time.time() - start) * 1000,
            errors=[],
            loss_components=LossComponents(
                relevance=0.95,  # Intake is pretty straightforward
                completeness=0.90,
                performance=1.0,  # No performance concerns
                maintainability=1.0,
            ),
        )
        emitter.emit(event)

        return result
