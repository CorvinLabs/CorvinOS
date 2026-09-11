"""Phase 5: Content — Implementation of core functionality."""

from dataclasses import dataclass
from typing import Dict, List, Any
import time
from ..events import PhaseCompletedEvent, LossComponents, LossEmitter


@dataclass
class ContentRequest:
    """Request for content phase."""
    skill_id: str
    intent: str
    functions: List[Dict[str, Any]]
    module_structure: Dict[str, List[str]]


@dataclass
class ContentResult:
    """Output of content phase."""
    skill_id: str
    code_generated: str  # Simplified code preview
    function_count: int
    lines_of_code: int


class ContentPhase:
    """Phase 5: Generate core implementation."""

    def execute(self, request: ContentRequest, emitter: LossEmitter) -> ContentResult:
        """Execute content phase."""
        start = time.time()
        errors = []

        # Generate code
        code = self._generate_code(request.skill_id, request.functions)
        loc = len(code.split("\n"))

        result = ContentResult(
            skill_id=request.skill_id,
            code_generated=code,
            function_count=len(request.functions),
            lines_of_code=loc,
        )

        # Loss: more functions = more complete, but need to be well-written
        relevance = 0.85
        completeness = min(1.0, len(request.functions) / 5.0)
        performance = 0.8 if loc < 200 else 0.7  # Longer code = less efficient
        maintainability = min(1.0, len(request.functions) / 6.0)

        event = PhaseCompletedEvent(
            phase_num=5,
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
    def _generate_code(skill_id: str, functions: List[Dict[str, Any]]) -> str:
        """Generate implementation code (simplified)."""
        code = f'"""Implementation of {skill_id}."""\n\n'
        code += "from typing import Any, Dict, List, Optional\n\n"

        for func in functions:
            name = func["name"]
            params = ", ".join(func.get("params", ["input"]))
            returns = func.get("returns", "Any")
            code += f"def {name}({params}) -> {returns}:\n"
            code += f'    """Implementation of {name}."""\n'
            code += "    pass\n\n"

        return code
