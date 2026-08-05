"""Phase 4: Task Enrichment & Context Injection (ADR-0267).

Calculate task complexity, select model (Haiku vs Opus), estimate costs.
"""

from dataclasses import dataclass
from .validation import ValidatedGraphs


@dataclass
class EnrichedTask:
    """Output of Phase 4 enrichment."""

    validated: ValidatedGraphs
    """Original validated graphs from Phase 3."""

    task_complexity: float
    """0.0–1.0 complexity score based on task type, severity, scope."""

    model_recommendation: str
    """'haiku' or 'opus' based on complexity + task type."""

    estimated_tokens: int
    """Rough token estimate for the full turn."""

    estimated_cost_usd: float
    """Estimated cost in USD (based on model + tokens)."""

    model_cost_per_1m: dict[str, float] = None
    """Cost per 1M tokens for each model (default OpenAI pricing)."""

    def __post_init__(self):
        """Set default model pricing if not provided."""
        if self.model_cost_per_1m is None:
            self.model_cost_per_1m = {
                "haiku": 0.80,  # $0.80 per 1M tokens
                "opus": 3.0,  # $3.00 per 1M tokens
            }


class TaskComplexityCalculator:
    """Calculate task complexity from normalized task + graphs."""

    def calculate(self, validated: ValidatedGraphs) -> float:
        """Calculate complexity score (0.0–1.0).

        Factors:
        - Task type (BUG_FIX=0.3, FEATURE=0.5, REFACTOR=0.6, INCIDENT=0.7, ...)
        - Severity (low=+0.0, medium=+0.1, high=+0.2)
        - Component count (normalized to 0.0–0.2 bonus)
        - Graph count (more graphs = more context needed)
        - File count touched (more files = more scope)
        """
        normalized = validated.filtered.classified.normalized
        filtered = validated.filtered

        # Base score from task type
        type_scores = {
            "bug_fix": 0.3,
            "feature": 0.5,
            "refactor": 0.6,
            "incident": 0.7,
            "documentation": 0.2,
            "performance": 0.5,
            "unknown": 0.4,
        }

        task_type_str = (
            getattr(normalized.type, "value", str(normalized.type))
            .lower()
            .replace("tasktype.", "")
        )
        base_score = type_scores.get(task_type_str, 0.4)

        # Severity bonus
        severity_bonus = {
            "low": 0.0,
            "medium": 0.1,
            "high": 0.2,
        }.get(getattr(normalized, "severity", "medium"), 0.1)

        # Component count bonus (capped at 0.2)
        component_count = len(getattr(normalized, "components", []))
        component_bonus = min(0.2, component_count * 0.02)

        # Graph count bonus (more graphs = more AI reasoning needed)
        graph_count = len(filtered.filtered_graphs)
        graph_bonus = min(0.1, graph_count * 0.02)

        # Final score: clamp to [0.0, 1.0]
        complexity = base_score + severity_bonus + component_bonus + graph_bonus
        return min(1.0, max(0.0, complexity))


class ModelSelector:
    """Select model (Haiku vs Opus) based on complexity."""

    def select(self, complexity: float, task_severity: str = "medium") -> str:
        """Select model based on complexity + severity.

        Rules:
        - complexity >= 0.6 → Opus
        - severity == "high" → Opus
        - Otherwise → Haiku (default)

        Args:
            complexity: 0.0–1.0 complexity score.
            task_severity: "low", "medium", or "high".

        Returns:
            "haiku" or "opus".
        """
        if complexity >= 0.6 or task_severity == "high":
            return "opus"
        return "haiku"


class CostEstimator:
    """Estimate token consumption and USD cost."""

    def estimate(
        self,
        validated: ValidatedGraphs,
        model: str,
        model_pricing: dict[str, float] = None,
    ) -> tuple[int, float]:
        """Estimate tokens + cost for this task.

        Estimation logic:
        1. Task description length → tokens (rough: chars / 4)
        2. Graph file count → tokens (rough: chars / 4)
        3. Buffer for reasoning + output (~2000 tokens)
        4. Model-specific pricing

        Args:
            validated: ValidatedGraphs.
            model: "haiku" or "opus".
            model_pricing: {model: cost_per_1m_tokens}.

        Returns:
            (estimated_tokens, estimated_usd_cost).
        """
        if model_pricing is None:
            model_pricing = {"haiku": 0.80, "opus": 3.0}

        # Task description tokens
        description = getattr(validated.filtered.classified.normalized, "description", "")
        description_tokens = len(description) // 4 if description else 0

        # Graph files tokens (rough)
        files_tokens = 0
        for file_list in validated.filtered.deduplicated_files.values():
            files_tokens += len(str(file_list)) // 4

        # Buffer for reasoning + output
        buffer_tokens = 2000

        total_tokens = description_tokens + files_tokens + buffer_tokens

        # Cost calculation
        cost_per_1m = model_pricing.get(model, 3.0)
        cost_usd = (total_tokens / 1_000_000) * cost_per_1m

        return total_tokens, cost_usd


class TaskEnricher:
    """Orchestrates enrichment pipeline."""

    def __init__(self):
        """Initialize enricher components."""
        self.complexity_calc = TaskComplexityCalculator()
        self.model_selector = ModelSelector()
        self.cost_estimator = CostEstimator()

    def enrich(self, validated: ValidatedGraphs) -> EnrichedTask:
        """Enrich task with complexity, model, and cost estimates.

        Args:
            validated: ValidatedGraphs from Phase 3.

        Returns:
            EnrichedTask with all enrichment fields.
        """
        # Calculate complexity
        complexity = self.complexity_calc.calculate(validated)

        # Select model
        severity = getattr(validated.filtered.classified.normalized, "severity", "medium")
        model = self.model_selector.select(complexity, severity)

        # Estimate cost
        estimated_tokens, estimated_cost_usd = self.cost_estimator.estimate(
            validated, model
        )

        return EnrichedTask(
            validated=validated,
            task_complexity=complexity,
            model_recommendation=model,
            estimated_tokens=estimated_tokens,
            estimated_cost_usd=estimated_cost_usd,
        )
