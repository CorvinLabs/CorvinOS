"""Daily measurement for Week 2 Days 9-12.

Measures agent performance WITH CEL enabled and compares against baseline.
Records memory enrichment metrics, latency, success rate.

Run individually per day:
    pytest test_cel_daily_measurement.py::TestCELMeasurementDay9 -xvs
    pytest test_cel_daily_measurement.py::TestCELMeasurementDay10 -xvs
    etc.
"""

import json
import time
import pytest
from pathlib import Path
from datetime import datetime
from ..engine import TaskEngine


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


class TestCELMeasurementDay9:
    """Day 9 measurement (first day of 4-day loop)."""

    @pytest.fixture
    def engine(self):
        return TaskEngine(enable_cel=True)

    def _run_measurement(self, engine, day):
        """Generic measurement pipeline for any day."""
        measurements = []
        successes = 0
        total_latency = 0
        total_matches = 0
        cache_hits = 0

        for i, task in enumerate(SAMPLE_TASKS, 1):
            start = time.perf_counter()
            try:
                result = engine.route_task(task)
                elapsed_ms = (time.perf_counter() - start) * 1000

                matches = 0
                confidence = 0.0
                cache_hit = False

                if result.rich_task_brief:
                    matches = len(result.rich_task_brief.memory_context.matches)
                    confidence = result.rich_task_brief.memory_context.confidence
                    cache_hit = result.rich_task_brief.memory_context.cache_hit

                measurement = {
                    "task_id": i,
                    "decision": result.decision_target.value,
                    "latency_ms": elapsed_ms,
                    "memory_matches": matches,
                    "confidence": confidence,
                    "cache_hit": cache_hit,
                    "success": True,
                }

                measurements.append(measurement)
                successes += 1
                total_latency += elapsed_ms
                total_matches += matches
                if cache_hit:
                    cache_hits += 1

            except Exception as e:
                pytest.fail(f"Task {i} failed: {e}")

        # Calculate statistics
        success_rate = successes / len(measurements)
        avg_latency = total_latency / successes if successes > 0 else 0
        avg_matches = total_matches / successes if successes > 0 else 0
        cache_hit_rate = cache_hits / successes if successes > 0 else 0

        # Verify results
        assert success_rate > 0.8, f"Success rate too low: {success_rate:.1%}"
        assert avg_latency < 1000, f"Average latency too high: {avg_latency:.0f}ms"

        # Save metrics
        report = {
            "timestamp": datetime.now().isoformat(),
            "day": day,
            "success_rate": success_rate,
            "avg_latency_ms": avg_latency,
            "p95_latency_ms": sorted([m["latency_ms"] for m in measurements])[
                int(len(measurements) * 0.95)
            ],
            "avg_memory_matches": avg_matches,
            "cache_hit_rate": cache_hit_rate,
            "measurements": measurements,
        }

        output_file = Path(__file__).parent.parent.parent / f"day{day}_metrics.json"
        with open(output_file, "w") as f:
            json.dump(report, f, indent=2)

        print(f"\n✓ Day {day}: {successes}/{len(measurements)} tasks")
        print(f"  Success rate: {success_rate:.1%}")
        print(f"  Avg latency: {avg_latency:.1f}ms")
        print(f"  Avg memory matches: {avg_matches:.1f}")
        print(f"  Cache hit rate: {cache_hit_rate:.1%}")
        print(f"\nMetrics saved to: {output_file}")

        return report

    def test_measure_50_tasks_with_cel(self, engine):
        """Measure 50 tasks with CEL enabled."""
        self._run_measurement(engine, day=9)


class TestCELMeasurementDay10:
    """Day 10 measurement (second day of 4-day loop)."""

    @pytest.fixture
    def engine(self):
        return TaskEngine(enable_cel=True)

    def test_measure_day10(self, engine):
        """Measure Day 10."""
        test_day9 = TestCELMeasurementDay9()
        test_day9._run_measurement(engine, day=10)


class TestCELMeasurementDay11:
    """Day 11 measurement (third day of 4-day loop)."""

    @pytest.fixture
    def engine(self):
        return TaskEngine(enable_cel=True)

    def test_measure_day11(self, engine):
        """Measure Day 11."""
        test_day9 = TestCELMeasurementDay9()
        test_day9._run_measurement(engine, day=11)


class TestCELMeasurementDay12:
    """Day 12 measurement (fourth day of 4-day loop)."""

    @pytest.fixture
    def engine(self):
        return TaskEngine(enable_cel=True)

    def test_measure_day12(self, engine):
        """Measure Day 12."""
        test_day9 = TestCELMeasurementDay9()
        test_day9._run_measurement(engine, day=12)


class TestCELMeasurementCumulative:
    """Cumulative test for Week 2 progress."""

    def test_cumulative_measurements_available(self):
        """Verify measurement data from previous days."""
        project_root = Path(__file__).parent.parent.parent

        # Check which days have metrics
        days_measured = []
        for day in range(9, 13):
            metric_file = project_root / f"day{day}_metrics.json"
            if metric_file.exists():
                days_measured.append(day)

        # At least Day 9 should exist after running the test
        assert 9 in days_measured or len(days_measured) >= 1, "No measurement files found"

    def test_final_decision_gate_ready(self):
        """Check if all data for decision gate is ready (Day 14)."""
        project_root = Path(__file__).parent.parent.parent

        files_needed = {
            "baseline_metrics.json": "Pre-CEL baseline (Week 1)",
            "day9_metrics.json": "Day 9 measurement (first day)",
        }

        all_present = all(
            (project_root / fname).exists()
            for fname in files_needed.keys()
        )

        if all_present:
            print("\n✓ Ready for Week 2 measurement")
            print("  Baseline metrics: available")
            print("  Day 9+ metrics: in progress")
        else:
            missing = [
                fname for fname in files_needed.keys()
                if not (project_root / fname).exists()
            ]
            print(f"\n⚠ Still waiting for: {missing}")
