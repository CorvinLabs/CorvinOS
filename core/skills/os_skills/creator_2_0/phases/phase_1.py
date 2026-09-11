"""Phase 1: Ingestion — Use DataHub Skill to collect domain knowledge."""

from dataclasses import dataclass
from typing import Dict, List, Any, Optional
import time
from ..events import PhaseCompletedEvent, LossComponents, LossEmitter


@dataclass
class IngestionRequest:
    """Request for ingestion phase."""
    skill_id: str
    description: str
    intent: str
    mode: str


@dataclass
class IngestionResult:
    """Output of ingestion phase."""
    skill_id: str
    domain_knowledge: Dict[str, Any]  # Structured knowledge about the skill's domain
    examples: List[Dict[str, Any]]  # Example inputs/outputs
    related_skills: List[str]  # Related skills from DataHub


class IngestionPhase:
    """Phase 1: Collect domain knowledge (uses DataHub Skill from Phase 1)."""

    def execute(self, request: IngestionRequest, emitter: LossEmitter) -> IngestionResult:
        """Execute ingestion phase."""
        start = time.time()
        errors = []

        # In a real implementation, this would call the DataHub Skill.
        # For now, we'll use a mock that generates plausible knowledge.
        domain_knowledge = self._collect_knowledge(request.description, request.intent)
        examples = self._generate_examples(request.intent)
        related_skills = self._find_related_skills(request.intent)

        result = IngestionResult(
            skill_id=request.skill_id,
            domain_knowledge=domain_knowledge,
            examples=examples,
            related_skills=related_skills,
        )

        # Estimate loss based on example count and knowledge completeness
        completeness = min(1.0, len(examples) / 3.0)  # Expect ~3 examples
        relevance = 0.85 if domain_knowledge else 0.5
        performance = 1.0  # No performance concerns in ingestion
        maintainability = 0.9 if len(domain_knowledge) > 0 else 0.5

        event = PhaseCompletedEvent(
            phase_num=1,
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
    def _collect_knowledge(description: str, intent: str) -> Dict[str, Any]:
        """Collect domain knowledge (mock implementation)."""
        knowledge = {
            "intent": intent,
            "description": description,
            "required_imports": [],
            "required_dependencies": [],
            "related_concepts": [],
        }

        # Add intent-specific knowledge
        if intent == "validation":
            knowledge["required_imports"] = ["re", "typing"]
            knowledge["related_concepts"] = ["regex", "type checking", "error handling"]
        elif intent == "data_processing":
            knowledge["required_imports"] = ["json", "typing"]
            knowledge["related_concepts"] = ["parsing", "transformation", "error handling"]
        elif intent == "classification":
            knowledge["required_imports"] = ["typing", "enum"]
            knowledge["related_concepts"] = ["categorization", "heuristics", "confidence scoring"]
        elif intent == "api_integration":
            knowledge["required_imports"] = ["http", "typing", "json"]
            knowledge["related_concepts"] = ["requests", "error handling", "retry logic"]
        else:
            knowledge["required_imports"] = ["typing"]
            knowledge["related_concepts"] = ["general utility"]

        return knowledge

    @staticmethod
    def _generate_examples(intent: str) -> List[Dict[str, Any]]:
        """Generate example inputs/outputs (mock)."""
        if intent == "validation":
            return [
                {"input": "test@example.com", "output": True, "description": "Valid email"},
                {"input": "invalid-email", "output": False, "description": "Invalid email"},
            ]
        elif intent == "data_processing":
            return [
                {"input": '{"key": "value"}', "output": {"key": "value"}, "description": "Parse JSON"},
            ]
        elif intent == "classification":
            return [
                {"input": "fast and quick", "output": "adjective", "description": "Classify word"},
            ]
        else:
            return [{"input": "example", "output": "example", "description": "Process example"}]

    @staticmethod
    def _find_related_skills(intent: str) -> List[str]:
        """Find related skills from DataHub (mock)."""
        mapping = {
            "validation": ["email_validator", "url_validator", "schema_validator"],
            "data_processing": ["json_parser", "csv_processor", "xml_parser"],
            "classification": ["text_classifier", "image_classifier", "document_categorizer"],
            "api_integration": ["http_client", "rest_builder", "graphql_client"],
        }
        return mapping.get(intent, ["generic_utility"])
