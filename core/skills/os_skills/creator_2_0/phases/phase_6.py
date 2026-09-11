"""Phase 6: Hooks — Integration points and error handling."""

from dataclasses import dataclass
from typing import Dict, List, Any
import time
from ..events import PhaseCompletedEvent, LossComponents, LossEmitter


@dataclass
class HooksRequest:
    """Request for hooks phase."""
    skill_id: str
    code_generated: str
    functions: List[Dict[str, Any]]


@dataclass
class HooksResult:
    """Output of hooks phase."""
    skill_id: str
    error_handlers: List[str]
    integration_points: List[str]
    hooks_defined: Dict[str, str]


class HooksPhase:
    """Phase 6: Add error handling and integration hooks."""

    def execute(self, request: HooksRequest, emitter: LossEmitter) -> HooksResult:
        """Execute hooks phase."""
        start = time.time()
        errors = []

        # Design error handlers and hooks
        error_handlers = self._design_error_handlers(request.functions)
        integration_points = self._identify_integration_points(request.functions)
        hooks = self._define_hooks()

        result = HooksResult(
            skill_id=request.skill_id,
            error_handlers=error_handlers,
            integration_points=integration_points,
            hooks_defined=hooks,
        )

        # Loss: good error handling = high completeness
        relevance = 0.85
        completeness = min(1.0, len(error_handlers) / 2.0)
        performance = 0.9
        maintainability = min(1.0, (len(error_handlers) + len(integration_points)) / 4.0)

        event = PhaseCompletedEvent(
            phase_num=6,
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
    def _design_error_handlers(functions: List[Dict[str, Any]]) -> List[str]:
        """Design error handlers."""
        handlers = []
        for func in functions:
            name = func["name"]
            handlers.append(f"try/except for {name}()")

        return handlers

    @staticmethod
    def _identify_integration_points(functions: List[Dict[str, Any]]) -> List[str]:
        """Identify where this skill integrates with other systems."""
        points = []
        if len(functions) > 2:
            points.append("Middleware hook for validation")
        if any("api" in f["name"].lower() for f in functions):
            points.append("API endpoint registration")
        if any("classify" in f["name"].lower() for f in functions):
            points.append("Event-based classification")
        return points

    @staticmethod
    def _define_hooks() -> Dict[str, str]:
        """Define hook functions."""
        return {
            "on_load": "Initialize skill",
            "on_execute": "Execute before main function",
            "on_error": "Handle errors gracefully",
            "on_unload": "Cleanup resources",
        }
