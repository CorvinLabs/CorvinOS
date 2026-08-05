"""TaskEngine: Orchestrator for all 6 phases of ADR-0267.

Single entry point for the complete task routing pipeline.
Handles error context and ensures phase contracts are maintained.
"""

from dataclasses import dataclass
from enum import Enum
from .normalizer import TaskNormalizer, InsufficientTaskInfo
from .classifier import TaskClassifier
from .filtering import GraphFilteringPipeline, FilterConfig
from .validation import GraphValidator
from .enrichment import TaskEnricher
from .delegation import DelegationRouter, DelegationTarget


class EngineError(Exception):
    """Base exception for TaskEngine errors."""

    pass


class EnginePhase(Enum):
    """Which phase failed."""

    NORMALIZATION = "normalization"
    CLASSIFICATION = "classification"
    FILTERING = "filtering"
    VALIDATION = "validation"
    ENRICHMENT = "enrichment"
    DELEGATION = "delegation"


@dataclass
class EngineResult:
    """Final output of TaskEngine routing."""

    raw_task: str
    """Original task input."""

    decision_target: DelegationTarget
    """Where to send the task: native | acs | tde."""

    carve_out_reason: str
    """Why delegated (big_data_vocabulary, high_complexity_opus, none)."""

    confidence: float
    """Confidence in delegation decision (0.0–1.0)."""

    estimated_cost_usd: float
    """Estimated cost for this task."""

    model_recommendation: str
    """Recommended model: 'haiku' or 'opus'."""

    task_complexity: float
    """Complexity score (0.0–1.0)."""

    enriched_metadata: dict
    """All metadata from all phases for inspection."""


class TaskEngine:
    """Complete task routing pipeline (Phases 0–5)."""

    def __init__(self, filter_config: FilterConfig = None):
        """Initialize engine components.

        Args:
            filter_config: Filtering configuration (uses defaults if None).
        """
        self.normalizer = TaskNormalizer()
        self.classifier = TaskClassifier()
        self.filter_pipeline = GraphFilteringPipeline()
        self.validator = GraphValidator()
        self.enricher = TaskEnricher()
        self.router = DelegationRouter()
        self.filter_config = filter_config or FilterConfig()

    def route_task(self, raw_task: str) -> EngineResult:
        """Route a task through all 6 phases.

        Pipeline:
        1. Normalize: Extract metadata, validate sufficiency
        2. Classify: Route to 5 graph types, score confidence
        3. Filter: Deduplicate, rank, filter graphs
        4. Validate: Check router completeness, re-rank
        5. Enrich: Calculate complexity, select model, estimate cost
        6. Delegate: Apply ADR-0217 rules, route to native/ACS/TDE

        Args:
            raw_task: Raw task description (typically from user).

        Returns:
            EngineResult with delegation decision + metadata.

        Raises:
            InsufficientTaskInfo: Task is too vague to process.
            EngineError: Any phase fails (includes phase context).
        """
        try:
            # Phase 0: Normalize
            normalized = self.normalizer.normalize(raw_task)

            # Phase 1: Classify
            classified = self.classifier.classify(normalized)

            # Phase 2: Filter
            filtered = self.filter_pipeline.process(
                classified, normalized=normalized, config=self.filter_config
            )

            # Phase 3: Validate
            validated = self.validator.validate(filtered)

            # Phase 4: Enrich
            enriched = self.enricher.enrich(validated)

            # Phase 5: Delegate
            decision = self.router.route(enriched)

            # Build result
            return EngineResult(
                raw_task=raw_task,
                decision_target=decision.delegation_target,
                carve_out_reason=decision.carve_out_reason,
                confidence=decision.confidence,
                estimated_cost_usd=enriched.estimated_cost_usd,
                model_recommendation=enriched.model_recommendation,
                task_complexity=enriched.task_complexity,
                enriched_metadata={
                    "normalized_type": str(normalized.type),
                    "normalized_severity": normalized.severity,
                    "classified_confidence": classified.confidence,
                    "filtered_graphs": filtered.filtered_graphs,
                    "validation_notes": validated.validation_notes,
                    "final_confidence": validated.final_confidence,
                    "estimated_tokens": enriched.estimated_tokens,
                },
            )

        except InsufficientTaskInfo:
            # Re-raise; caller should handle clarification
            raise

        except Exception as e:
            # Wrap with phase context
            phase = self._infer_phase_from_error(e)
            raise EngineError(
                f"TaskEngine failed at {phase.value}: {type(e).__name__}: {str(e)}"
            ) from e

    def _infer_phase_from_error(self, error: Exception) -> EnginePhase:
        """Infer which phase caused the error (best-effort).

        Args:
            error: The caught exception.

        Returns:
            Likely EnginePhase.
        """
        # Simple heuristics based on error type
        error_str = str(error).lower()

        if "type" in error_str or "severity" in error_str:
            return EnginePhase.NORMALIZATION
        elif "classification" in error_str or "graph" in error_str:
            return EnginePhase.CLASSIFICATION
        elif "filter" in error_str or "dedup" in error_str:
            return EnginePhase.FILTERING
        elif "validation" in error_str or "complete" in error_str:
            return EnginePhase.VALIDATION
        elif "complexity" in error_str or "model" in error_str or "cost" in error_str:
            return EnginePhase.ENRICHMENT
        elif "delegate" in error_str or "target" in error_str:
            return EnginePhase.DELEGATION

        # Default: best guess is enrichment (most complex phase)
        return EnginePhase.ENRICHMENT
