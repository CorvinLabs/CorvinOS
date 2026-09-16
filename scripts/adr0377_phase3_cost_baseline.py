#!/usr/bin/env python3
"""
ADR-0377 Phase 3 cost ESTIMATE (not a validation).

Projects what a synthetic 100-task workload would cost under three routing
policies, using the published rate card. The task list below is invented for
comparison; the PRICES are real. It measures a pricing difference, not a
quality outcome, and it observes nothing running in production.

Until 2026-09-16 this was billed as validating "50-70% cost savings" against
model profiles whose accuracy figures were hand-written constants and whose
models (claude-3-opus, gemini-1.5-pro) this install cannot run. See ADR-0857.

Run: python3 scripts/adr0377_phase3_cost_baseline.py
"""

import json
import sys
from pathlib import Path
from dataclasses import dataclass, asdict
from typing import Dict, List

# Add repo to path
_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO))

from core.models.multi_model_router import (
    ModelTier,
    MultiModelRouter,
    default_router,
)


@dataclass
class TaskEstimate:
    """Estimated task with tokens and complexity."""
    task_id: str
    task_type: str  # "code_review", "research", "summary", "refactor"
    estimated_input_tokens: int
    estimated_output_tokens: int
    quality_requirement: float  # 0.0-1.0
    complexity: str  # "simple", "medium", "complex"


# Benchmark dataset: 100 representative tasks
BENCHMARK_TASKS: List[TaskEstimate] = [
    # Simple tasks (40) — code reviews, summaries
    TaskEstimate("simple_001", "code_review", 500, 100, 0.80, "simple"),
    TaskEstimate("simple_002", "summary", 1000, 200, 0.80, "simple"),
    TaskEstimate("simple_003", "code_review", 800, 150, 0.80, "simple"),
    TaskEstimate("simple_004", "summary", 600, 100, 0.80, "simple"),
    TaskEstimate("simple_005", "refactor", 400, 200, 0.80, "simple"),
    TaskEstimate("simple_006", "code_review", 1000, 200, 0.80, "simple"),
    TaskEstimate("simple_007", "summary", 700, 150, 0.80, "simple"),
    TaskEstimate("simple_008", "code_review", 600, 100, 0.80, "simple"),
    TaskEstimate("simple_009", "summary", 900, 150, 0.80, "simple"),
    TaskEstimate("simple_010", "refactor", 500, 150, 0.80, "simple"),
    TaskEstimate("simple_011", "code_review", 800, 200, 0.80, "simple"),
    TaskEstimate("simple_012", "summary", 650, 120, 0.80, "simple"),
    TaskEstimate("simple_013", "code_review", 700, 150, 0.80, "simple"),
    TaskEstimate("simple_014", "refactor", 600, 180, 0.80, "simple"),
    TaskEstimate("simple_015", "summary", 800, 100, 0.80, "simple"),
    TaskEstimate("simple_016", "code_review", 550, 100, 0.80, "simple"),
    TaskEstimate("simple_017", "summary", 750, 150, 0.80, "simple"),
    TaskEstimate("simple_018", "refactor", 700, 200, 0.80, "simple"),
    TaskEstimate("simple_019", "code_review", 900, 150, 0.80, "simple"),
    TaskEstimate("simple_020", "summary", 600, 100, 0.80, "simple"),
    TaskEstimate("simple_021", "code_review", 650, 120, 0.80, "simple"),
    TaskEstimate("simple_022", "refactor", 550, 150, 0.80, "simple"),
    TaskEstimate("simple_023", "summary", 800, 200, 0.80, "simple"),
    TaskEstimate("simple_024", "code_review", 700, 100, 0.80, "simple"),
    TaskEstimate("simple_025", "summary", 900, 150, 0.80, "simple"),
    TaskEstimate("simple_026", "refactor", 600, 100, 0.80, "simple"),
    TaskEstimate("simple_027", "code_review", 750, 150, 0.80, "simple"),
    TaskEstimate("simple_028", "summary", 550, 100, 0.80, "simple"),
    TaskEstimate("simple_029", "code_review", 800, 120, 0.80, "simple"),
    TaskEstimate("simple_030", "refactor", 700, 180, 0.80, "simple"),
    TaskEstimate("simple_031", "summary", 650, 150, 0.80, "simple"),
    TaskEstimate("simple_032", "code_review", 900, 200, 0.80, "simple"),
    TaskEstimate("simple_033", "refactor", 500, 100, 0.80, "simple"),
    TaskEstimate("simple_034", "summary", 750, 120, 0.80, "simple"),
    TaskEstimate("simple_035", "code_review", 600, 100, 0.80, "simple"),
    TaskEstimate("simple_036", "summary", 700, 150, 0.80, "simple"),
    TaskEstimate("simple_037", "refactor", 800, 200, 0.80, "simple"),
    TaskEstimate("simple_038", "code_review", 550, 150, 0.80, "simple"),
    TaskEstimate("simple_039", "summary", 900, 100, 0.80, "simple"),
    TaskEstimate("simple_040", "refactor", 650, 180, 0.80, "simple"),

    # Medium tasks (35) — more complex code reviews, refactoring
    TaskEstimate("medium_001", "code_review", 3000, 800, 0.90, "medium"),
    TaskEstimate("medium_002", "refactor", 2500, 1200, 0.90, "medium"),
    TaskEstimate("medium_003", "research", 2000, 1000, 0.90, "medium"),
    TaskEstimate("medium_004", "code_review", 3500, 1000, 0.90, "medium"),
    TaskEstimate("medium_005", "refactor", 2200, 900, 0.90, "medium"),
    TaskEstimate("medium_006", "code_review", 2800, 700, 0.90, "medium"),
    TaskEstimate("medium_007", "research", 2400, 1200, 0.90, "medium"),
    TaskEstimate("medium_008", "refactor", 3000, 1100, 0.90, "medium"),
    TaskEstimate("medium_009", "code_review", 2600, 900, 0.90, "medium"),
    TaskEstimate("medium_010", "research", 3200, 1500, 0.90, "medium"),
    TaskEstimate("medium_011", "refactor", 2400, 800, 0.90, "medium"),
    TaskEstimate("medium_012", "code_review", 2900, 1000, 0.90, "medium"),
    TaskEstimate("medium_013", "research", 2700, 1300, 0.90, "medium"),
    TaskEstimate("medium_014", "code_review", 3100, 800, 0.90, "medium"),
    TaskEstimate("medium_015", "refactor", 2500, 1000, 0.90, "medium"),
    TaskEstimate("medium_016", "code_review", 2800, 900, 0.90, "medium"),
    TaskEstimate("medium_017", "research", 3000, 1200, 0.90, "medium"),
    TaskEstimate("medium_018", "refactor", 2300, 800, 0.90, "medium"),
    TaskEstimate("medium_019", "code_review", 3200, 1000, 0.90, "medium"),
    TaskEstimate("medium_020", "research", 2600, 1100, 0.90, "medium"),
    TaskEstimate("medium_021", "refactor", 2900, 1200, 0.90, "medium"),
    TaskEstimate("medium_022", "code_review", 2400, 700, 0.90, "medium"),
    TaskEstimate("medium_023", "research", 3100, 1500, 0.90, "medium"),
    TaskEstimate("medium_024", "code_review", 2700, 900, 0.90, "medium"),
    TaskEstimate("medium_025", "refactor", 3000, 1000, 0.90, "medium"),
    TaskEstimate("medium_026", "research", 2500, 1200, 0.90, "medium"),
    TaskEstimate("medium_027", "code_review", 2800, 800, 0.90, "medium"),
    TaskEstimate("medium_028", "refactor", 2600, 900, 0.90, "medium"),
    TaskEstimate("medium_029", "research", 3200, 1300, 0.90, "medium"),
    TaskEstimate("medium_030", "code_review", 2400, 1000, 0.90, "medium"),
    TaskEstimate("medium_031", "refactor", 2700, 800, 0.90, "medium"),
    TaskEstimate("medium_032", "research", 2900, 1200, 0.90, "medium"),
    TaskEstimate("medium_033", "code_review", 3100, 900, 0.90, "medium"),
    TaskEstimate("medium_034", "refactor", 2300, 1000, 0.90, "medium"),
    TaskEstimate("medium_035", "research", 3300, 1500, 0.90, "medium"),

    # Complex tasks (25) — research, deep refactoring
    TaskEstimate("complex_001", "research", 5000, 2000, 0.95, "complex"),
    TaskEstimate("complex_002", "refactor", 4500, 2000, 0.95, "complex"),
    TaskEstimate("complex_003", "research", 6000, 2500, 0.95, "complex"),
    TaskEstimate("complex_004", "code_review", 5500, 1500, 0.95, "complex"),
    TaskEstimate("complex_005", "refactor", 5000, 2200, 0.95, "complex"),
    TaskEstimate("complex_006", "research", 5500, 2000, 0.95, "complex"),
    TaskEstimate("complex_007", "code_review", 6000, 1800, 0.95, "complex"),
    TaskEstimate("complex_008", "refactor", 4800, 2500, 0.95, "complex"),
    TaskEstimate("complex_009", "research", 5200, 2200, 0.95, "complex"),
    TaskEstimate("complex_010", "code_review", 5800, 1500, 0.95, "complex"),
    TaskEstimate("complex_011", "refactor", 5500, 2000, 0.95, "complex"),
    TaskEstimate("complex_012", "research", 6200, 2500, 0.95, "complex"),
    TaskEstimate("complex_013", "code_review", 5000, 1800, 0.95, "complex"),
    TaskEstimate("complex_014", "refactor", 5300, 2200, 0.95, "complex"),
    TaskEstimate("complex_015", "research", 5700, 2000, 0.95, "complex"),
    TaskEstimate("complex_016", "code_review", 6100, 1500, 0.95, "complex"),
    TaskEstimate("complex_017", "refactor", 4900, 2500, 0.95, "complex"),
    TaskEstimate("complex_018", "research", 5400, 2200, 0.95, "complex"),
    TaskEstimate("complex_019", "code_review", 5600, 1800, 0.95, "complex"),
    TaskEstimate("complex_020", "refactor", 5100, 2000, 0.95, "complex"),
    TaskEstimate("complex_021", "research", 6300, 2500, 0.95, "complex"),
    TaskEstimate("complex_022", "code_review", 5200, 1500, 0.95, "complex"),
    TaskEstimate("complex_023", "refactor", 5400, 2200, 0.95, "complex"),
    TaskEstimate("complex_024", "research", 5900, 2000, 0.95, "complex"),
    TaskEstimate("complex_025", "code_review", 6200, 1800, 0.95, "complex"),
]


# Complexity -> minimum capability tier. An EXPLICIT policy input to this
# estimate, stated here so a reader can disagree with it. The previous version
# hid the equivalent choice inside per-model "aptitude" constants.
_TIER_FOR_COMPLEXITY = {
    "simple": ModelTier.FAST_CHEAP,
    "medium": ModelTier.BALANCED,
    "complex": ModelTier.BEST_QUALITY,
}

# The counterfactual: what the same traffic would cost entirely on the
# top non-frontier tier.
BASELINE_MODEL = "claude-opus-5"


def _cost(router, model: str, task: "TaskEstimate") -> float:
    """Cost for one task, or abort — an unpriced model must not read as $0.

    estimate_task_cost returns None when the rate card does not price a model.
    Silently adding 0.0 would understate the total and make an unrunnable
    model look free.
    """
    value = router.estimate_task_cost(
        model, task.estimated_input_tokens, task.estimated_output_tokens
    )
    if value is None:
        raise SystemExit(
            f"Model {model!r} is not on the published rate card, so this "
            f"estimate cannot be produced. Check the engine registry."
        )
    return value


def _pick(router, min_tier: "ModelTier") -> str:
    """Cheapest model the registry declares at or above ``min_tier``.

    Resolved from the registry rather than written as a literal: it declares
    dated ids (claude-haiku-4-5-20251001), so a hardcoded "claude-haiku-4-5"
    is not a model this install has.
    """
    ranking = router.rank_models(min_tier=min_tier, allow_local=False)
    if ranking.recommended_model is None:
        raise SystemExit(
            f"No runnable model clears tier {min_tier.name}. "
            f"Check the engine registry."
        )
    return ranking.recommended_model


def baseline_all_opus() -> float:
    """Counterfactual: every task on the top non-frontier tier."""
    router = default_router()
    model = _pick(router, ModelTier.BEST_QUALITY)
    return sum(_cost(router, model, t) for t in BENCHMARK_TASKS)


def tier_routed() -> tuple[float, Dict]:
    """Each task on the cheapest model that clears its complexity's tier.

    There is ONE routed scenario here, not the two ("Phase 1" fixed mapping
    and "Phase 3" multi-model) this script used to report. Those differed only
    because Phase 3 ranked on per-task-type `aptitude` constants that were
    invented; once model choice is driven by the declared tier ordering alone,
    a fixed complexity->tier mapping and "cheapest model clearing that tier"
    are the same policy. The 70.5% "incremental improvement" between them was
    an artefact of those constants.
    """
    router = default_router()
    total_cost = 0.0
    model_usage: Dict[str, int] = {}
    for task in BENCHMARK_TASKS:
        model = _pick(router, _TIER_FOR_COMPLEXITY.get(task.complexity, ModelTier.BALANCED))
        total_cost += _cost(router, model, task)
        model_usage[model] = model_usage.get(model, 0) + 1
    return total_cost, model_usage


def print_benchmark_report(baseline_cost: float, routed_cost: float, usage: Dict):
    """Print the estimate.

    Reports ONE routed scenario. The previous version printed "Phase 1" and
    "Phase 3" plus a 70.5% incremental improvement between them; that gap came
    entirely from per-task-type `aptitude` constants that were invented, and
    disappears once model choice follows the declared tier ordering.

    It also printed "Cost Savings Validation" with PASS lines against a
    50-70% target. Nothing here is validated: this compares published RATES
    over a synthetic task list. It never observed a production run.
    """
    savings = ((baseline_cost - routed_cost) / baseline_cost) * 100 if baseline_cost else 0.0

    print("\n" + "=" * 80)
    print("ADR-0377: multi-model routing cost ESTIMATE")
    print("=" * 80)
    print("\nSynthetic dataset: 100 tasks (40 simple / 35 medium / 25 complex).")
    print("Task sizes are invented for comparison; per-token RATES are the")
    print("published card. This is a pricing projection, not a measurement.")

    print("\n" + "-" * 80)
    print("Estimated cost")
    print("-" * 80)
    print(f"\nAll on the top non-frontier tier:  ${baseline_cost:8.2f}")
    print(f"Tier-routed by complexity:        ${routed_cost:8.2f}   ({savings:.1f}% lower)")
    print("\nThe difference is a property of the rate card, not evidence that")
    print("routing preserved quality: this estimate measures no outcomes.")

    print("\n" + "-" * 80)
    print("Model distribution (tier-routed)")
    print("-" * 80)
    total_tasks = sum(usage.values()) or 1
    for model, count in sorted(usage.items(), key=lambda x: -x[1]):
        print(f"  {model:34s}: {count:3d} tasks ({count / total_tasks * 100:5.1f}%)")
    print("\n" + "=" * 80)

    return {
        "kind": "estimate",
        "basis": "published rate card applied to a synthetic 100-task workload",
        "measures_quality": False,
        "baseline_cost_usd": round(baseline_cost, 4),
        "tier_routed_cost_usd": round(routed_cost, 4),
        "rate_difference_pct": round(savings, 1),
        "model_distribution": usage,
    }


if __name__ == "__main__":
    print("\nEstimating ADR-0377 multi-model routing cost...")

    baseline = baseline_all_opus()
    routed, usage = tier_routed()

    results = print_benchmark_report(baseline, routed, usage)

    _out = _REPO / "docs" / "adr0377_phase3_cost_baseline_results.json"
    with open(_out, "w") as f:
        json.dump(results, f, indent=2)

    print(f"\n\u2713 Results saved to {_out}")
    sys.exit(0)
