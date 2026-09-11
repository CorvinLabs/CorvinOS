"""Phase 2: Clarification — Ask user questions if needed (max 3)."""

from dataclasses import dataclass
from typing import Dict, List, Any, Optional
import time
from ..events import PhaseCompletedEvent, LossComponents, LossEmitter


@dataclass
class ClarificationRequest:
    """Request for clarification phase."""
    skill_id: str
    description: str
    intent: str
    domain_knowledge: Dict[str, Any]
    user_clarifications: Optional[Dict[str, str]] = None  # Pre-provided answers


@dataclass
class ClarificationResult:
    """Output of clarification phase."""
    skill_id: str
    clarifications: Dict[str, str]  # Questions → Answers
    required_clarifications_asked: List[str]  # Questions that were asked


class ClarificationPhase:
    """Phase 2: Ask clarification questions (max 3)."""

    def execute(
        self, request: ClarificationRequest, emitter: LossEmitter
    ) -> ClarificationResult:
        """Execute clarification phase."""
        start = time.time()
        errors = []

        # Generate clarification questions (max 3)
        questions = self._generate_questions(request.intent, request.description)
        questions = questions[:3]  # Limit to 3

        clarifications = request.user_clarifications or {}

        # Provide default answers for any missing clarifications
        for q in questions:
            if q not in clarifications:
                clarifications[q] = self._default_answer(q, request.intent)

        result = ClarificationResult(
            skill_id=request.skill_id,
            clarifications=clarifications,
            required_clarifications_asked=questions,
        )

        # Estimate loss: fewer questions asked = higher completeness
        relevance = 0.85
        completeness = 1.0 - (len(questions) * 0.1)  # Each question reduces by 10%
        performance = 1.0
        maintainability = 0.9

        event = PhaseCompletedEvent(
            phase_num=2,
            skill_id=request.skill_id,
            duration_ms=(time.time() - start) * 1000,
            errors=errors,
            loss_components=LossComponents(
                relevance=relevance,
                completeness=completeness,
                performance=performance,
                maintainability=maintainability,
            ),
            metadata={"questions_asked": len(questions)},
        )
        emitter.emit(event)

        return result

    @staticmethod
    def _generate_questions(intent: str, description: str) -> List[str]:
        """Generate clarification questions (max 3)."""
        questions = []

        if intent == "validation":
            questions = [
                "What format should be validated (email, URL, regex pattern)?",
                "Should validation be strict or lenient?",
                "What error messages should be returned?",
            ]
        elif intent == "data_processing":
            questions = [
                "What input format is expected (JSON, CSV, XML)?",
                "What transformations should be applied?",
                "How should errors be handled?",
            ]
        elif intent == "classification":
            questions = [
                "What categories should be classified into?",
                "What confidence threshold is required?",
                "Should multi-label classification be supported?",
            ]
        elif intent == "api_integration":
            questions = [
                "Which API endpoint should be called?",
                "What authentication method is required?",
                "What response format is expected?",
            ]
        else:
            questions = [
                "What is the primary use case?",
                "What inputs and outputs are expected?",
                "What dependencies are allowed?",
            ]

        return questions[:3]  # Safety limit

    @staticmethod
    def _default_answer(question: str, intent: str) -> str:
        """Provide a default answer to a question."""
        if "format" in question.lower():
            return "JSON"
        elif "strict" in question.lower() or "lenient" in question.lower():
            return "strict"
        elif "error" in question.lower():
            return "Raise exceptions on invalid input"
        elif "transform" in question.lower() or "transformation" in question.lower():
            return "Basic transformation"
        elif "categor" in question.lower():
            return "2-3 main categories"
        elif "authentication" in question.lower() or "auth" in question.lower():
            return "No authentication required"
        elif "response" in question.lower():
            return "JSON response"
        elif "use case" in question.lower():
            return "General-purpose utility"
        elif "input" in question.lower() or "output" in question.lower():
            return "String input, boolean/string output"
        elif "dependenc" in question.lower():
            return "Standard library only"
        else:
            return "To be determined"
