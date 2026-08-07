"""TaskEngine: Orchestrator for all 6 phases (+ Phase 5.5 CEL) of ADR-0267.

Single entry point for the complete task routing pipeline.
Handles error context and ensures phase contracts are maintained.
Integrates Context Engineering Layer (Phase 5.5) for memory enrichment.
"""

import logging
from dataclasses import dataclass
from enum import Enum
from typing import Optional

from .normalizer import TaskNormalizer, InsufficientTaskInfo
from .classifier import TaskClassifier
from .filtering import GraphFilteringPipeline, FilterConfig
from .validation import GraphValidator
from .enrichment import TaskEnricher
from .delegation import DelegationRouter, DelegationTarget
from .metrics import TaskMetrics, MetricsPhase, MetricsOutcome
from .contracts import PhaseContracts, ContractViolation

# Import CEL (Phase 5.5)
try:
    from operator.context_engineering import MemoryLookup, GraphTraversal, SkillInjection
    CEL_AVAILABLE = True
except ImportError:
    CEL_AVAILABLE = False

logger = logging.getLogger(__name__)


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
    CONTEXT_ENGINEERING = "context_engineering"  # Phase 5.5
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

    rich_task_brief: Optional[object] = None
    """RichTaskBrief from Phase 5.5 (CEL), if enabled (can be None)."""


class TaskEngine:
    """Complete task routing pipeline (Phases 0–5 + Phase 5.5 CEL).

    Phases:
    - 0: Normalize (extract metadata)
    - 1: Classify (5 graph routers)
    - 2: Filter (deduplicate)
    - 3: Validate (completeness)
    - 4: Enrich (complexity, model, cost)
    - 5.5: CEL (Context Engineering Layer, memory enrichment, OPTIONAL)
    - 5: Delegate (native/acs/tde)
    """

    def __init__(
        self,
        filter_config: FilterConfig = None,
        metrics: TaskMetrics = None,
        enable_cel: bool = True,
    ):
        """Initialize engine components.

        Args:
            filter_config: Filtering configuration (uses defaults if None).
            metrics: Optional TaskMetrics collector for Prometheus export.
            enable_cel: Enable Context Engineering Layer (Phase 5.5, default: True).
        """
        self.normalizer = TaskNormalizer()
        self.classifier = TaskClassifier()
        self.filter_pipeline = GraphFilteringPipeline()
        self.validator = GraphValidator()
        self.enricher = TaskEnricher()
        self.router = DelegationRouter()
        self.filter_config = filter_config or FilterConfig()
        self.metrics = metrics or TaskMetrics()

        # Phase 5.5: Context Engineering Layer (optional, 3 sub-phases)
        self.cel_memory: Optional[MemoryLookup] = None
        self.cel_graph: Optional[GraphTraversal] = None
        self.cel_skills: Optional[SkillInjection] = None
        if enable_cel and CEL_AVAILABLE:
            try:
                self.cel_memory = MemoryLookup()
                self.cel_graph = GraphTraversal()
                self.cel_skills = SkillInjection()
                logger.info("CEL (Phase 5.5) enabled: MemoryLookup + GraphTraversal + SkillInjection")
            except Exception as e:
                logger.warning(f"Failed to initialize CEL: {e}, continuing without it")
        elif enable_cel and not CEL_AVAILABLE:
            logger.warning("CEL requested but not available, continuing without it")

    @property
    def cel(self) -> Optional[object]:
        """Backward-compat property for legacy code that checks self.cel."""
        return self.cel_memory

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
                # Record redundancy: graphs before filtering vs after filtering
                self.metrics.record_redundancy(
                    len(filtered.ranked_graphs),
                    len(filtered.filtered_graphs)
                )

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

            # Phase 5.5: Context Engineering Layer (3 sub-phases: Memory → Graph → Skills, OPTIONAL)
            rich_brief = None
            if self.cel_memory and self.cel_graph and self.cel_skills:
                try:
                    with self.metrics.phase_timer(MetricsPhase.CEL) as ctx:
                        logger.debug("Running Phase 5.5: Context Engineering Layer (Memory → Graph → Skills)")

                        # Phase 5.5a: Memory Lookup
                        rich_brief = self.cel_memory.enrich_task(enriched)
                        ctx["memory_matches"] = len(rich_brief.memory_context.matches)
                        ctx["cel_confidence"] = rich_brief.memory_context.confidence

                        # Phase 5.5b: Graph Traversal (find related decisions)
                        graph_result = self.cel_graph.find_related_decisions(enriched)
                        related_decisions = graph_result.related_decisions
                        ctx["related_decisions"] = len(related_decisions)
                        ctx["graph_depth"] = graph_result.traversal_depth

                        # Phase 5.5c: Skill Injection (recommend skills)
                        skills_result = self.cel_skills.recommend_skills(enriched, related_decisions)
                        recommended_skills = skills_result.recommended_skills
                        ctx["recommended_skills"] = len(recommended_skills)

                        # Attach to brief for agent decision
                        rich_brief.related_decisions = related_decisions
                        rich_brief.recommended_skills = recommended_skills

                        logger.info(
                            f"Phase 5.5 complete: {len(rich_brief.memory_context.matches)} memory matches, "
                            f"{len(related_decisions)} related decisions, "
                            f"{len(recommended_skills)} recommended skills"
                        )
                except Exception as e:
                    logger.warning(f"Phase 5.5 (CEL) failed: {e}, continuing without it")

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
                rich_task_brief=rich_brief,
                enriched_metadata={
                    "normalized_type": str(normalized.type),
                    "normalized_severity": normalized.severity,
                    "classified_confidence": classified.confidence,
                    "filtered_graphs": filtered.filtered_graphs,
                    "validation_notes": validated.validation_notes,
                    "final_confidence": validated.final_confidence,
                    "estimated_tokens": enriched.estimated_tokens,
                    "metrics_summary": self.metrics.summary(),
                    "cel_enabled": bool(self.cel),
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
        """Infer which phase caused the error (best-effort heuristic).

        NOTE: This is a fallback. Preferred: errors are caught with phase context
        in each phase's metrics.phase_timer() context manager (Zeilen 109-196).
        This method only infers phase if error context was lost.

        Args:
            error: The caught exception.

        Returns:
            Likely EnginePhase (best guess based on error message keywords).
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
