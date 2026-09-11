"""Phase 3: Planning — Architecture planning and design."""

from dataclasses import dataclass
from typing import Dict, List, Any, Optional
import time
from ..events import PhaseCompletedEvent, LossComponents, LossEmitter


@dataclass
class PlanningRequest:
    """Request for planning phase."""
    skill_id: str
    intent: str
    description: str
    domain_knowledge: Dict[str, Any]
    clarifications: Dict[str, str]


@dataclass
class PlanningResult:
    """Output of planning phase."""
    skill_id: str
    architecture: Dict[str, Any]  # Overall architecture plan
    functions: List[Dict[str, Any]]  # Function signatures to implement
    dependencies: List[str]  # Required dependencies
    data_flow: str  # Description of data flow


class PlanningPhase:
    """Phase 3: Create architecture plan."""

    def execute(self, request: PlanningRequest, emitter: LossEmitter) -> PlanningResult:
        """Execute planning phase."""
        start = time.time()
        errors = []

        # Design architecture based on intent
        architecture = self._design_architecture(request.intent, request.description)
        functions = self._design_functions(request.intent)
        dependencies = self._identify_dependencies(request.intent)
        data_flow = self._describe_data_flow(request.intent, functions)

        result = PlanningResult(
            skill_id=request.skill_id,
            architecture=architecture,
            functions=functions,
            dependencies=dependencies,
            data_flow=data_flow,
        )

        # Estimate loss: more functions = more complete plan but potentially less focused
        relevance = 0.9
        completeness = min(1.0, len(functions) / 5.0)  # Expect ~5 functions
        performance = 0.85 if len(dependencies) < 3 else 0.75  # Too many deps = lower performance
        maintainability = 0.9 if len(functions) <= 6 else 0.7

        event = PhaseCompletedEvent(
            phase_num=3,
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
    def _design_architecture(intent: str, description: str) -> Dict[str, Any]:
        """Design high-level architecture."""
        return {
            "pattern": "monolithic" if len(description) < 100 else "modular",
            "layers": ["input_validation", "processing", "output_formatting"],
            "error_handling": "exception-based",
            "testing_approach": "unit_tests",
        }

    @staticmethod
    def _design_functions(intent: str) -> List[Dict[str, Any]]:
        """Design function signatures."""
        if intent == "validation":
            return [
                {"name": "validate", "params": ["input"], "returns": "bool"},
                {"name": "validate_detailed", "params": ["input"], "returns": "dict"},
                {"name": "get_validation_rules", "params": [], "returns": "dict"},
            ]
        elif intent == "data_processing":
            return [
                {"name": "process", "params": ["input"], "returns": "Any"},
                {"name": "parse_input", "params": ["input"], "returns": "dict"},
                {"name": "transform", "params": ["data"], "returns": "Any"},
                {"name": "format_output", "params": ["data"], "returns": "str"},
            ]
        elif intent == "classification":
            return [
                {"name": "classify", "params": ["input"], "returns": "str"},
                {"name": "classify_with_confidence", "params": ["input"], "returns": "dict"},
                {"name": "get_categories", "params": [], "returns": "list"},
            ]
        elif intent == "api_integration":
            return [
                {"name": "call_api", "params": ["endpoint", "params"], "returns": "dict"},
                {"name": "build_request", "params": ["endpoint", "params"], "returns": "dict"},
                {"name": "parse_response", "params": ["response"], "returns": "dict"},
                {"name": "handle_errors", "params": ["error"], "returns": "str"},
            ]
        else:
            return [
                {"name": "execute", "params": ["input"], "returns": "Any"},
            ]

    @staticmethod
    def _identify_dependencies(intent: str) -> List[str]:
        """Identify required dependencies."""
        base = ["typing"]
        if intent == "validation":
            return base + ["re"]
        elif intent == "data_processing":
            return base + ["json"]
        elif intent == "classification":
            return base + ["enum"]
        elif intent == "api_integration":
            return base + ["http.client", "json"]
        else:
            return base

    @staticmethod
    def _describe_data_flow(intent: str, functions: List[Dict[str, Any]]) -> str:
        """Describe the data flow through the skill."""
        flow_steps = [f["name"] for f in functions[:3]]
        return f"Input → {' → '.join(flow_steps)} → Output"
