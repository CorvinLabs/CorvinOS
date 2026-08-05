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
from .metrics import TaskMetrics, MetricsPhase, MetricsOutcome
from .contracts import PhaseContracts, ContractViolation


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

    def __init__(self, filter_config: FilterConfig = None, metrics: TaskMetrics = None):
        """Initialize engine components.

        Args:
            filter_config: Filtering configuration (uses defaults if None).
            metrics: Optional TaskMetrics collector for Prometheus export.
        """
        self.normalizer = TaskNormalizer()
        self.classifier = TaskClassifier()
        self.filter_pipeline = GraphFilteringPipeline()
        self.validator = GraphValidator()
        self.enricher = TaskEnricher()
        self.router = DelegationRouter()
        self.filter_config = filter_config or FilterConfig()
        self.metrics = metrics or TaskMetrics()

    def route_task(self, raw_task: str) -> EngineResult:
        """Route a task through all 6 phases (with Prometheus metrics).

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
        self.metrics.reset()

        try:
            # Phase 0: Normalize
            with self.metrics.phase_timer(MetricsPhase.NORMALIZATION) as ctx:
                normalized = self.normalizer.normalize(raw_task)
                try:
                    PhaseContracts.validate_phase0_output(normalized)
                except ContractViolation as e:
                    ctx["contract_violation"] = True
                    ctx["violation_details"] = str(e)
                    raise

            # Phase 1: Classify
            with self.metrics.phase_timer(MetricsPhase.CLASSIFICATION) as ctx:
                classified = self.classifier.classify(normalized)
                try:
                    PhaseContracts.validate_phase1_output(classified)
                except ContractViolation as e:
                    ctx["contract_violation"] = True
                    ctx["violation_details"] = str(e)
                    raise
                self.metrics.record_confidence(classified.confidence)

            # Phase 2: Filter
            with self.metrics.phase_timer(MetricsPhase.FILTERING) as ctx:
                filtered = self.filter_pipeline.process(
                    classified, normalized=normalized, config=self.filter_config
                )
                try:
                    PhaseContracts.validate_phase2_output(filtered)
                except ContractViolation as e:
                    ctx["contract_violation"] = True
                    ctx["violation_details"] = str(e)
                    raise
                # Record redundancy: ratio of deduped graphs
                original_count = len(classified.classification) if classified.classification else 0
                filtered_count = len(filtered.filtered_graphs)
                if original_count > 0:
                    self.metrics.record_redundancy(original_count, filtered_count)

            # Phase 3: Validate
            with self.metrics.phase_timer(MetricsPhase.VALIDATION) as ctx:
                validated = self.validator.validate(filtered)
                try:
                    PhaseContracts.validate_phase3_output(validated)
                except ContractViolation as e:
                    ctx["contract_violation"] = True
                    ctx["violation_details"] = str(e)
                    raise
                self.metrics.record_confidence(validated.final_confidence)

            # Phase 4: Enrich
            with self.metrics.phase_timer(MetricsPhase.ENRICHMENT) as ctx:
                enriched = self.enricher.enrich(validated)
                try:
                    PhaseContracts.validate_phase4_output(enriched)
                except ContractViolation as e:
                    ctx["contract_violation"] = True
                    ctx["violation_details"] = str(e)
                    raise
                self.metrics.record_model_selection(enriched.model_recommendation)
                self.metrics.record_cost(enriched.estimated_cost_usd)

            # Phase 5: Delegate
            with self.metrics.phase_timer(MetricsPhase.DELEGATION) as ctx:
                decision = self.router.route(enriched)
                try:
                    PhaseContracts.validate_phase5_output(decision)
                except ContractViolation as e:
                    ctx["contract_violation"] = True
                    ctx["violation_details"] = str(e)
                    raise
                self.metrics.record_decision(
                    decision.delegation_target.value, decision.carve_out_reason
                )
                self.metrics.record_confidence(decision.confidence)

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
                    "metrics_summary": self.metrics.summary(),
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
