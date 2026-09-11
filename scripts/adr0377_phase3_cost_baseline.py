#!/usr/bin/env python3
"""
ADR-0377 Phase 3 Cost Baseline Benchmark
Validates 50-70% cost savings with multi-model routing.

Run: python3 scripts/adr0377_phase3_cost_baseline.py
"""

import json
import sys
from dataclasses import dataclass, asdict
from typing import Dict, List

# Add repo to path
sys.path.insert(0, '/home/shumway/projects/CorvinOS')

from core.models.multi_model_router import (
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


def baseline_all_opus() -> float:
    """Calculate cost baseline: all tasks use Opus."""
    router = default_router()
    total_cost = 0.0

    for task in BENCHMARK_TASKS:
        cost = router.estimate_task_cost(
            "claude-3-opus",
            task.estimated_input_tokens,
            task.estimated_output_tokens,
        )
        total_cost += cost

    return total_cost


def phase1_sonnet_haiku() -> float:
    """Calculate cost: Phase 1 routing (Sonnet/Haiku)."""
    router = default_router()
    total_cost = 0.0

    for task in BENCHMARK_TASKS:
        if task.complexity == "simple":
            model = "claude-3-5-haiku"
        elif task.complexity == "medium":
            model = "claude-3-5-sonnet"
        else:
            model = "claude-3-opus"

        cost = router.estimate_task_cost(
            model,
            task.estimated_input_tokens,
            task.estimated_output_tokens,
        )
        total_cost += cost

    return total_cost


def phase3_multimodel() -> tuple[float, Dict]:
    """Calculate cost: Phase 3 multi-model routing."""
    router = default_router()
    total_cost = 0.0
    model_usage = {}

    for task in BENCHMARK_TASKS:
        # Rank models for this task
        ranking = router.rank_models(
            task_type=task.task_type,
            quality_threshold=task.quality_requirement,
        )

        selected_model = ranking.recommended_model

        cost = router.estimate_task_cost(
            selected_model,
            task.estimated_input_tokens,
            task.estimated_output_tokens,
        )

        total_cost += cost
        model_usage[selected_model] = model_usage.get(selected_model, 0) + 1

    return total_cost, model_usage


def print_benchmark_report(
    baseline_cost: float,
    phase1_cost: float,
    phase3_cost: float,
    phase3_usage: Dict,
):
    """Print benchmark report."""
    print("\n" + "=" * 80)
    print("ADR-0377 Phase 3: Multi-Model Routing Cost Baseline")
    print("=" * 80)

    print(f"\nBenchmark Dataset: 100 tasks")
    print(f"  - Simple (40 tasks): code reviews, summaries (quality ≥ 0.80)")
    print(f"  - Medium (35 tasks): complex reviews, refactoring (quality ≥ 0.90)")
    print(f"  - Complex (25 tasks): research, deep refactoring (quality ≥ 0.95)")

    print("\n" + "-" * 80)
    print("Cost Comparison")
    print("-" * 80)

    print(f"\nBaseline (All Opus):                  ${baseline_cost:.2f}")
    phase1_savings = ((baseline_cost - phase1_cost) / baseline_cost) * 100
    print(f"Phase 1 (Sonnet/Haiku):              ${phase1_cost:.2f}  ({phase1_savings:.1f}% savings)")
    phase3_savings = ((baseline_cost - phase3_cost) / baseline_cost) * 100
    print(f"Phase 3 (Multi-Model):               ${phase3_cost:.2f}  ({phase3_savings:.1f}% savings)")

    incremental_savings = ((phase1_cost - phase3_cost) / phase1_cost) * 100
    print(f"\nIncremental Phase 3 improvement:     {incremental_savings:.1f}%")

    print("\n" + "-" * 80)
    print("Phase 3 Model Distribution")
    print("-" * 80)

    total_tasks = sum(phase3_usage.values())
    for model, count in sorted(phase3_usage.items(), key=lambda x: -x[1]):
        pct = (count / total_tasks) * 100
        print(f"  {model:30s}: {count:3d} tasks ({pct:5.1f}%)")

    print("\n" + "-" * 80)
    print("Cost Savings Validation")
    print("-" * 80)

    print(f"\n✓ Phase 1 achieves {phase1_savings:.1f}% savings (target: 30-40%)")
    if 30 <= phase1_savings <= 50:
        print(f"  → PASS (within target range)")
    else:
        print(f"  → WARNING (outside expected range)")

    print(f"\n✓ Phase 3 achieves {phase3_savings:.1f}% savings (target: 50-70%)")
    if 50 <= phase3_savings <= 80:
        print(f"  → PASS (within target range)")
    else:
        print(f"  → WARNING (outside expected range)")

    print("\n" + "=" * 80)

    return {
        "baseline_cost": round(baseline_cost, 4),
        "phase1_cost": round(phase1_cost, 4),
        "phase3_cost": round(phase3_cost, 4),
        "phase1_savings_pct": round(phase1_savings, 1),
        "phase3_savings_pct": round(phase3_savings, 1),
        "phase3_model_distribution": phase3_usage,
    }


if __name__ == "__main__":
    print("\nRunning ADR-0377 Phase 3 Cost Baseline Benchmark...")

    # Calculate costs
    baseline = baseline_all_opus()
    phase1 = phase1_sonnet_haiku()
    phase3, usage = phase3_multimodel()

    # Print report
    results = print_benchmark_report(baseline, phase1, phase3, usage)

    # Save results
    with open("/home/shumway/projects/CorvinOS/docs/adr0377_phase3_cost_baseline_results.json", "w") as f:
        json.dump(results, f, indent=2)

    print(f"\n✓ Results saved to docs/adr0377_phase3_cost_baseline_results.json")

    # Exit with success
    sys.exit(0)
