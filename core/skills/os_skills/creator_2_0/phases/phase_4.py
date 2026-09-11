"""Phase 4: Structure — Code structure and file layout."""

from dataclasses import dataclass
from typing import Dict, List, Any
import time
from ..events import PhaseCompletedEvent, LossComponents, LossEmitter


@dataclass
class StructureRequest:
    """Request for structure phase."""
    skill_id: str
    intent: str
    functions: List[Dict[str, Any]]
    dependencies: List[str]


@dataclass
class StructureResult:
    """Output of structure phase."""
    skill_id: str
    file_layout: Dict[str, str]  # filename → purpose
    module_structure: Dict[str, List[str]]  # module → functions
    imports_needed: List[str]


class StructurePhase:
    """Phase 4: Design code structure and layout."""

    def execute(self, request: StructureRequest, emitter: LossEmitter) -> StructureResult:
        """Execute structure phase."""
        start = time.time()
        errors = []

        # Design file layout
        file_layout = self._design_file_layout(request.skill_id, len(request.functions))
        module_structure = self._organize_functions(request.functions)
        imports_needed = request.dependencies

        result = StructureResult(
            skill_id=request.skill_id,
            file_layout=file_layout,
            module_structure=module_structure,
            imports_needed=imports_needed,
        )

        # Loss: clear structure = high maintainability
        relevance = 0.9
        completeness = 1.0 if len(file_layout) >= 2 else 0.7
        performance = 1.0  # No performance impact at structure phase
        maintainability = min(1.0, len(file_layout) * 0.25)  # More files = more modular

        event = PhaseCompletedEvent(
            phase_num=4,
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
    def _design_file_layout(skill_id: str, num_functions: int) -> Dict[str, str]:
        """Design file layout."""
        if num_functions <= 2:
            return {
                "__init__.py": "Package init",
                "skill.py": "Main skill implementation",
            }
        elif num_functions <= 5:
            return {
                "__init__.py": "Package init",
                "skill.py": "Main skill class",
                "utils.py": "Helper functions",
                "types.py": "Type definitions",
            }
        else:
            return {
                "__init__.py": "Package init",
                "skill.py": "Main skill class",
                "core.py": "Core functionality",
                "utils.py": "Helper functions",
                "types.py": "Type definitions",
                "errors.py": "Custom exceptions",
            }

    @staticmethod
    def _organize_functions(functions: List[Dict[str, Any]]) -> Dict[str, List[str]]:
        """Organize functions into modules."""
        core_funcs = [f["name"] for f in functions if f["name"] not in ["get_categories", "get_rules", "get_validation_rules"]]
        util_funcs = [f["name"] for f in functions if f["name"] in ["get_categories", "get_rules", "get_validation_rules"]]

        structure = {
            "core": core_funcs,
        }
        if util_funcs:
            structure["utils"] = util_funcs

        return structure
