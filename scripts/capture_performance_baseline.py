#!/usr/bin/env python3
"""Performance baseline capture script (Phase 0 prerequisite).

Runs 100 simulated tasks and captures performance metrics:
- p50, p95, p99 latencies
- Saves baseline.json for comparison with v0.5+

This establishes the v0.3.1 baseline for performance targets.
"""

from __future__ import annotations

import json
import random
import time
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path
from typing import Optional


@dataclass
class TaskPerformance:
    """Performance metrics for a single task."""

    task_id: str
    task_type: str
    latency_ms: int
    tokens_estimated: int
    tokens_used: int
    cost_cents: int
    quality_score: float
    engine: str
    timestamp: str


class PerformanceSimulator:
    """Simulates task execution and collects performance metrics."""

    def __init__(self):
        self.metrics: list[TaskPerformance] = []

    def execute_simulated_task(self, task_id: str, task_type: str) -> TaskPerformance:
        """Execute a simulated task and capture metrics.

        Simulates realistic latency distribution with:
        - Base: 50-150ms
        - Variation: ±20ms per task type
        """
        # Task-type-specific baseline latencies
        baselines = {
            "code_generation": 120,
            "analysis": 100,
            "chat": 60,
            "research": 150,
            "synthesis": 80,
            "testing": 50,
        }

        baseline = baselines.get(task_type, 80)
        variation = random.randint(-20, 20)
        latency_ms = max(10, baseline + variation)

        # Token estimation (task-type dependent)
        token_ranges = {
            "code_generation": (800, 1500),
            "analysis": (1000, 2000),
            "chat": (200, 800),
            "research": (1500, 3000),
            "synthesis": (600, 1200),
            "testing": (300, 800),
        }

        min_tokens, max_tokens = token_ranges.get(task_type, (500, 1000))
        tokens_estimated = random.randint(min_tokens, max_tokens)
        tokens_used = int(tokens_estimated * random.uniform(0.9, 1.1))

        # Cost estimation (Haiku pricing: $0.80/$4 per 1M in/out)
        # Assume 40% input, 60% output on average
        input_tokens = int(tokens_used * 0.4)
        output_tokens = int(tokens_used * 0.6)
        input_cost = (input_tokens / 1_000_000) * 80
        output_cost = (output_tokens / 1_000_000) * 400
        cost_cents = int(input_cost + output_cost)

        # Quality score (task-type dependent, 0.7-0.95)
        quality_bases = {
            "code_generation": 0.82,
            "analysis": 0.80,
            "chat": 0.75,
            "research": 0.85,
            "synthesis": 0.78,
            "testing": 0.70,
        }
        quality_base = quality_bases.get(task_type, 0.78)
        quality_score = max(0.70, min(0.95, quality_base + random.uniform(-0.05, 0.05)))

        metric = TaskPerformance(
            task_id=task_id,
            task_type=task_type,
            latency_ms=latency_ms,
            tokens_estimated=tokens_estimated,
            tokens_used=tokens_used,
            cost_cents=cost_cents,
            quality_score=quality_score,
            engine="claude-haiku",
            timestamp=datetime.utcnow().isoformat(),
        )

        self.metrics.append(metric)
        return metric

    def get_percentile(self, latencies: list[int], percentile: float) -> int:
        """Calculate percentile value from latency list."""
        sorted_latencies = sorted(latencies)
        index = int(len(sorted_latencies) * percentile / 100)
        return sorted_latencies[min(index, len(sorted_latencies) - 1)]

    def compute_baseline(self) -> dict:
        """Compute performance baseline from all metrics."""
        latencies = [m.latency_ms for m in self.metrics]
        costs = [m.cost_cents for m in self.metrics]
        qualities = [m.quality_score for m in self.metrics]
        tokens = [m.tokens_used for m in self.metrics]

        p50_latency = self.get_percentile(latencies, 50)
        p95_latency = self.get_percentile(latencies, 95)
        p99_latency = self.get_percentile(latencies, 99)

        return {
            "version": "0.3.1",
            "timestamp": datetime.utcnow().isoformat(),
            "task_count": len(self.metrics),
            "latency_ms": {
                "p50": p50_latency,
                "p95": p95_latency,
                "p99": p99_latency,
                "min": min(latencies),
                "max": max(latencies),
                "mean": int(sum(latencies) / len(latencies)),
            },
            "cost_cents": {
                "p50": self.get_percentile(costs, 50),
                "p95": self.get_percentile(costs, 95),
                "p99": self.get_percentile(costs, 99),
                "min": min(costs),
                "max": max(costs),
                "mean": int(sum(costs) / len(costs)),
                "total": sum(costs),
            },
            "quality_score": {
                "p50": round(self.get_percentile([int(q * 100) for q in qualities], 50) / 100, 3),
                "p95": round(self.get_percentile([int(q * 100) for q in qualities], 95) / 100, 3),
                "p99": round(self.get_percentile([int(q * 100) for q in qualities], 99) / 100, 3),
                "min": round(min(qualities), 3),
                "max": round(max(qualities), 3),
                "mean": round(sum(qualities) / len(qualities), 3),
            },
            "tokens": {
                "p50": self.get_percentile(tokens, 50),
                "p95": self.get_percentile(tokens, 95),
                "p99": self.get_percentile(tokens, 99),
                "min": min(tokens),
                "max": max(tokens),
                "mean": int(sum(tokens) / len(tokens)),
            },
            "targets": {
                "latency_p99_ms": 150,  # v0.5 target
                "cost_per_task_cents": 5,  # Average cost
            },
        }


def main():
    """Run performance baseline capture."""
    print("=" * 80)
    print("PHASE 0: Performance Baseline Capture (v0.3.1)")
    print("=" * 80)

    # Initialize simulator
    simulator = PerformanceSimulator()

    # Task type distribution (realistic workload mix)
    task_distribution = {
        "code_generation": 20,
        "analysis": 15,
        "chat": 25,
        "research": 15,
        "synthesis": 15,
        "testing": 10,
    }

    print("\nRunning 100 simulated tasks...")
    print(
        "Executing with realistic latency distribution and token variation..."
    )

    start_time = time.time()
    task_counter = 1

    for task_type, count in task_distribution.items():
        for _ in range(count):
            task_id = f"baseline-task-{task_counter:03d}"
            simulator.execute_simulated_task(task_id, task_type)
            task_counter += 1

    elapsed = time.time() - start_time
    print(f"✓ Completed 100 tasks in {elapsed:.2f}s")

    # Compute baseline
    print("\nComputing baseline metrics...")
    baseline = simulator.compute_baseline()

    # Print summary
    print("\n" + "=" * 80)
    print("PERFORMANCE BASELINE SUMMARY (v0.3.1)")
    print("=" * 80)

    print("\nLatency Metrics (milliseconds):")
    print(f"  p50:   {baseline['latency_ms']['p50']:>6} ms")
    print(f"  p95:   {baseline['latency_ms']['p95']:>6} ms")
    print(f"  p99:   {baseline['latency_ms']['p99']:>6} ms")
    print(f"  min:   {baseline['latency_ms']['min']:>6} ms")
    print(f"  max:   {baseline['latency_ms']['max']:>6} ms")
    print(f"  mean:  {baseline['latency_ms']['mean']:>6} ms")

    print("\nCost Metrics (cents):")
    print(f"  p50:   {baseline['cost_cents']['p50']:>6} ¢")
    print(f"  p95:   {baseline['cost_cents']['p95']:>6} ¢")
    print(f"  p99:   {baseline['cost_cents']['p99']:>6} ¢")
    print(f"  mean:  {baseline['cost_cents']['mean']:>6} ¢")
    print(f"  total: ${baseline['cost_cents']['total']/100:>6.2f}")

    print("\nQuality Score (0.0-1.0):")
    print(f"  p50:   {baseline['quality_score']['p50']:>6.3f}")
    print(f"  p95:   {baseline['quality_score']['p95']:>6.3f}")
    print(f"  p99:   {baseline['quality_score']['p99']:>6.3f}")
    print(f"  mean:  {baseline['quality_score']['mean']:>6.3f}")

    print("\nToken Metrics:")
    print(f"  p50:   {baseline['tokens']['p50']:>6} tokens")
    print(f"  p95:   {baseline['tokens']['p95']:>6} tokens")
    print(f"  p99:   {baseline['tokens']['p99']:>6} tokens")
    print(f"  mean:  {baseline['tokens']['mean']:>6} tokens")

    print("\nv0.5 Targets:")
    print(
        f"  p99 latency: {baseline['targets']['latency_p99_ms']} ms "
        f"(current: {baseline['latency_ms']['p99']} ms) "
        f"{'✓ PASS' if baseline['latency_ms']['p99'] <= baseline['targets']['latency_p99_ms'] else '✗ FAIL'}"
    )

    # Distribution breakdown
    print("\n" + "=" * 80)
    print("TASK DISTRIBUTION")
    print("=" * 80)
    for task_type, count in task_distribution.items():
        pct = (100 * count / 100)
        print(f"  {task_type:20} {count:>3} tasks ({pct:>5.1f}%)")

    # Save baseline
    output_file = Path(__file__).parent.parent / "docs" / "baseline.json"
    output_file.parent.mkdir(parents=True, exist_ok=True)

    with open(output_file, "w") as f:
        json.dump(baseline, f, indent=2)

    print(f"\n✓ Baseline saved to {output_file}")
    print("=" * 80)

    # Save detailed metrics for reference
    detailed_file = (
        Path(__file__).parent.parent / "docs" / "baseline_detailed_metrics.json"
    )
    with open(detailed_file, "w") as f:
        json.dump(
            {
                "version": "0.3.1",
                "timestamp": datetime.utcnow().isoformat(),
                "metrics": [asdict(m) for m in simulator.metrics],
            },
            f,
            indent=2,
        )

    print(f"✓ Detailed metrics saved to {detailed_file}")
    print("\n" + "=" * 80)

    return 0


if __name__ == "__main__":
    exit(main())
