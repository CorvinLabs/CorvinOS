#!/usr/bin/env python3
"""Measure agent performance WITH CEL enabled (Week 2 Days 9-12).

Runs 50 real agent tasks through TaskEngine with CEL and records:
- Memory matches found per task
- Confidence scores
- Routing decisions (native/acs/tde)
- Latency
- Cache hit rate
- Model selection

Compares against baseline_metrics.json for improvement calculation.

Usage:
    uv run scripts/measure_with_cel.py --day 9 --output day9_metrics.json
"""

import sys
import json
import time
import logging
from pathlib import Path
from datetime import datetime
from typing import List, Dict, Any
import argparse

# Add repo root to path BEFORE other imports
repo_root = Path(__file__).parent.parent
if str(repo_root) not in sys.path:
    sys.path.insert(0, str(repo_root))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)


# Real agent task samples (diverse scenarios)
SAMPLE_TASKS = [
    "Fix NoneType error when Discord message handler receives empty content",
    "Add exponential backoff retry logic for failed HTTP requests in voice module",
    "Refactor TaskEngine phase contracts to use dataclass validation decorators",
    "Debug race condition in MemoryLookup cache eviction during concurrent searches",
    "Implement batch processing for file API to handle 10k+ file uploads",
    "Add comprehensive logging for CEL memory search confidence scores",
    "Optimize keyword extraction performance for large task summaries",
    "Document TaskEngine phases and data contracts in architecture guide",
    "Write integration tests for phase boundary contracts and invariants",
    "Profile TaskEngine for latency bottlenecks in production scenarios",
    "Cache memory file metadata during initial directory scan",
    "Resolve audit chain integrity failure on Windows platform",
    "Investigate agent timeout on large data processing tasks",
    "Design Phase 2 (Graph Traversal) API for CEL extension",
    "Plan CEL deployment strategy to production with feature flag",
    "Implement approach synthesis for multi-step task recommendation",
    "Add Prometheus metrics for CEL memory search performance",
    "Wire RichTaskBrief into agent decision logic pipeline",
    "Create monitoring dashboard for memory confidence distribution",
    "Set up rollback procedure for CEL disablement in production",
    "Evaluate skill injection approach for task context enrichment",
    "Implement cross-tenant isolation for CEL memory lookup",
    "Design feedback loop for recording useful memory matches",
    "Calibrate memory confidence scoring for production accuracy",
    "Implement memory deduplication for cache efficiency",
    "Add A/B testing framework for CEL improvement measurement",
    "Design failover strategy for CEL unavailability",
    "Document CEL performance characteristics and limitations",
    "Implement memory garbage collection for stale entries",
    "Create benchmarks for CEL enrichment latency across task types",
]


def measure_task_with_cel(task: str, engine) -> Dict[str, Any]:
    """Measure a single task with CEL enabled.

    Args:
        task: Task description.
        engine: TaskEngine instance with CEL enabled.

    Returns:
        Measurement dict with CEL-specific metrics.
    """
    start = time.perf_counter()
    try:
        result = engine.route_task(task)
        elapsed_ms = (time.perf_counter() - start) * 1000

        # Extract CEL-specific metrics
        memory_matches = 0
        cel_confidence = 0.0
        cache_hit = False

        if result.rich_task_brief is not None:
            memory_matches = len(result.rich_task_brief.memory_context.matches)
            cel_confidence = result.rich_task_brief.memory_context.confidence
            cache_hit = result.rich_task_brief.memory_context.cache_hit

        return {
            "task": task[:60] + "...",
            "decision": result.decision_target.value,
            "confidence": result.confidence,
            "complexity": result.task_complexity,
            "cost_usd": result.estimated_cost_usd,
            "model": result.model_recommendation,
            "latency_ms": elapsed_ms,
            "memory_matches": memory_matches,
            "cel_confidence": cel_confidence,
            "cache_hit": cache_hit,
            "success": True,
            "error": None,
        }
    except Exception as e:
        elapsed_ms = (time.perf_counter() - start) * 1000
        return {
            "task": task[:60] + "...",
            "success": False,
            "error": str(type(e).__name__),
            "error_detail": str(e)[:100],
            "latency_ms": elapsed_ms,
        }


def load_baseline() -> Dict[str, Any]:
    """Load baseline metrics from previous measurement."""
    baseline_file = Path("baseline_metrics.json")
    if baseline_file.exists():
        with open(baseline_file) as f:
            return json.load(f)
    return {"success_rate": 0, "latency_ms": {"p95": 0}}


def main():
    """Run Day 9-12 measurement loop."""
    from operator.task_analysis.engine import TaskEngine

    parser = argparse.ArgumentParser(description="Measure CEL performance")
    parser.add_argument("--day", type=int, default=9, help="Day number (9-12)")
    parser.add_argument("--output", default="day9_metrics.json", help="Output file")
    parser.add_argument("--tasks", type=int, default=50, help="Number of tasks to measure")
    args = parser.parse_args()

    logger.info("=" * 70)
    logger.info(f"ADR-0269 Phase 1 Lite — Day {args.day} Measurement (WITH CEL)")
    logger.info("=" * 70 + "\n")

    # Load baseline
    baseline = load_baseline()
    baseline_sr = baseline.get("success_rate", 0)
    baseline_p95 = baseline.get("latency_ms", {}).get("p95", 0)

    logger.info(f"Baseline (without CEL): {baseline_sr:.1%} success, {baseline_p95:.0f}ms P95\n")

    # Create engine with CEL
    logger.info(f"Measuring {args.tasks} tasks WITH CEL enabled...\n")
    engine = TaskEngine(enable_cel=True)

    measurements = []
    successes = 0
    total_latency_ms = 0
    total_memory_matches = 0
    cache_hits = 0

    # Use sample tasks (repeat if needed to reach target count)
    tasks = (SAMPLE_TASKS * ((args.tasks // len(SAMPLE_TASKS)) + 1))[:args.tasks]

    for i, task in enumerate(tasks, 1):
        if (i - 1) % 10 == 0:
            logger.info(f"Progress: {i}/{args.tasks}")

        result = measure_task_with_cel(task, engine)
        measurements.append(result)

        if result["success"]:
            successes += 1
            total_latency_ms += result["latency_ms"]
            total_memory_matches += result.get("memory_matches", 0)
            if result.get("cache_hit"):
                cache_hits += 1

    # Calculate statistics
    success_rate = successes / len(measurements) if measurements else 0
    avg_latency_ms = total_latency_ms / successes if successes > 0 else 0
    avg_memory_matches = total_memory_matches / successes if successes > 0 else 0

    latencies = sorted([m["latency_ms"] for m in measurements if m["success"]])
    p95_latency = latencies[int(len(latencies) * 0.95)] if latencies else 0

    # Calculate improvement
    improvement_sr = (success_rate - baseline_sr) * 100
    improvement_latency = ((baseline_p95 - p95_latency) / baseline_p95 * 100) if baseline_p95 > 0 else 0

    # Build report
    report = {
        "timestamp": datetime.now().isoformat(),
        "day": args.day,
        "phase": "Week 2 Measurement",
        "cel_enabled": True,
        "sample_size": len(measurements),
        "successes": successes,
        "failures": len(measurements) - successes,
        "success_rate": success_rate,
        "latency_ms": {
            "avg": avg_latency_ms,
            "p95": p95_latency,
            "min": min(latencies) if latencies else 0,
            "max": max(latencies) if latencies else 0,
        },
        "memory_enrichment": {
            "avg_matches": avg_memory_matches,
            "cache_hit_rate": cache_hits / successes if successes > 0 else 0,
        },
        "vs_baseline": {
            "success_rate_improvement": improvement_sr,
            "latency_improvement_pct": improvement_latency,
        },
        "decision_distribution": {},
        "model_distribution": {},
        "measurements": measurements,
    }

    # Aggregate decisions
    for m in measurements:
        if m["success"]:
            decision = m["decision"]
            report["decision_distribution"][decision] = (
                report["decision_distribution"].get(decision, 0) + 1
            )
            model = m["model"]
            report["model_distribution"][model] = (
                report["model_distribution"].get(model, 0) + 1
            )

    # Print report
    logger.info("\n" + "=" * 70)
    logger.info(f"DAY {args.day} RESULTS (WITH CEL)")
    logger.info("=" * 70)
    logger.info(f"Success Rate: {success_rate:.1%} ({successes}/{len(measurements)})")
    logger.info(f"  vs baseline: {improvement_sr:+.1f}%")
    logger.info(f"\nAvg Latency: {avg_latency_ms:.1f} ms")
    logger.info(f"P95 Latency: {p95_latency:.1f} ms")
    logger.info(f"  vs baseline: {improvement_latency:+.1f}%")
    logger.info(f"\nMemory Enrichment:")
    logger.info(f"  Avg matches: {avg_memory_matches:.1f}")
    logger.info(f"  Cache hit rate: {report['memory_enrichment']['cache_hit_rate']:.1%}")
    logger.info(f"\nDecision Distribution:")
    for decision, count in report["decision_distribution"].items():
        pct = count / successes * 100 if successes > 0 else 0
        logger.info(f"  {decision}: {count} ({pct:.0f}%)")
    logger.info(f"\nModel Distribution:")
    for model, count in report["model_distribution"].items():
        pct = count / successes * 100 if successes > 0 else 0
        logger.info(f"  {model}: {count} ({pct:.0f}%)")
    logger.info("=" * 70)

    # Write results
    output_file = Path(args.output)
    with open(output_file, "w") as f:
        json.dump(report, f, indent=2)

    logger.info(f"\nDay {args.day} metrics saved to: {output_file}")
    logger.info(f"\nCumulative Progress: Day {args.day}/12")
    logger.info("Next: Days 10-12 continue measurement, Day 13 final analysis\n")

    return 0 if success_rate > 0 else 1


if __name__ == "__main__":
    sys.exit(main())
