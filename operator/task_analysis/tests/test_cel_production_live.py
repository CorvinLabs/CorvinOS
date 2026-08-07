"""Live production measurement: CEL Phase 1 + 2 with real TaskEngine (150 tasks)."""

import time
import pytest
from datetime import datetime

# Relative import to avoid stdlib operator conflict
from ..engine import TaskEngine

REAL_TASKS = [
    "Fix concurrent access bug in memory module with thread-safe caching",
    "Implement graph traversal for decision discovery with BFS algorithm",
    "Add skill injection recommendation system based on relevance scoring",
    "Optimize task routing pipeline with graceful degradation for missing CEL",
    "Write end-to-end integration tests for Memory→Graph→Skills flow",
    "Debug phase 5.5 timeout issues when CEL takes more than one second",
    "Refactor SkillInjection cache key to avoid GC identity collisions",
    "Add feature flags for graph_traversal_enabled and skill_injection",
    "Implement monitoring dashboard for CEL adoption and latency metrics",
    "Migrate legacy task routing to use RichTaskBrief from Phase 5.5",
]

@pytest.mark.slow
def test_cel_production_150_tasks():
    """Production readiness: 150 real tasks through TaskEngine with CEL enabled."""

    print("\n" + "="*70)
    print("🚀 CEL PRODUCTION LIVE MEASUREMENT (150 TASKS)")
    print("="*70)

    engine = TaskEngine(enable_cel=True)
    results = {
        "tasks_success": 0,
        "tasks_failed": 0,
        "latencies_ms": [],
        "cel_enabled_count": 0,
        "rich_brief_populated_count": 0,
        "errors": [],
    }

    # Repeat to hit 150 tasks
    task_list = (REAL_TASKS * (150 // len(REAL_TASKS) + 1))[:150]

    start_batch = time.perf_counter()

    for i, task in enumerate(task_list):
        try:
            start = time.perf_counter()
            result = engine.route_task(task)
            latency_ms = (time.perf_counter() - start) * 1000

            results["latencies_ms"].append(latency_ms)
            results["tasks_success"] += 1

            # Check CEL enrichment
            if result.rich_task_brief:
                results["cel_enabled_count"] += 1
                if result.rich_task_brief.memory_context or \
                   result.rich_task_brief.related_decisions or \
                   result.rich_task_brief.recommended_skills:
                    results["rich_brief_populated_count"] += 1

            # Progress update every 30 tasks
            if (i + 1) % 30 == 0:
                elapsed = time.perf_counter() - start_batch
                avg_lat = sum(results["latencies_ms"][-30:]) / 30
                print(f"  [{i+1:3d}/150] avg latency: {avg_lat:6.1f}ms | "
                      f"CEL: {results['cel_enabled_count']:3d} | elapsed: {elapsed:5.1f}s")

        except Exception as e:
            results["tasks_failed"] += 1
            results["errors"].append(str(e))
            if len(results["errors"]) <= 3:
                print(f"  ⚠️  Task {i+1}: {type(e).__name__}")

    elapsed_total = time.perf_counter() - start_batch

    # Calculate stats
    latencies = results["latencies_ms"]
    if latencies:
        stats = {
            "success_rate": results["tasks_success"] / 150,
            "latency_p50": sorted(latencies)[len(latencies)//2],
            "latency_p95": sorted(latencies)[int(len(latencies)*0.95)],
            "latency_p99": sorted(latencies)[int(len(latencies)*0.99)],
            "latency_avg": sum(latencies) / len(latencies),
            "cel_adoption": results["cel_enabled_count"] / max(1, results["tasks_success"]),
        }
    else:
        stats = {k: 0 for k in ["success_rate", "latency_p50", "latency_p95", "latency_p99", "latency_avg", "cel_adoption"]}

    # Report
    print(f"\n{'='*70}")
    print(f"RESULTS")
    print(f"{'='*70}")
    print(f"✅ Success:             {results['tasks_success']}/150 ({stats['success_rate']*100:.1f}%)")
    print(f"❌ Failed:              {results['tasks_failed']}")
    print(f"⏱️  Total time:          {elapsed_total:.1f}s")
    print(f"\nLatency Profile (ms):")
    print(f"  P50:                {stats['latency_p50']:.1f}")
    print(f"  P95:                {stats['latency_p95']:.1f}")
    print(f"  P99:                {stats['latency_p99']:.1f}")
    print(f"  Average:            {stats['latency_avg']:.1f}")
    print(f"\nCEL Adoption:           {stats['cel_adoption']*100:.1f}% ({results['cel_enabled_count']}/{results['tasks_success']})")
    print(f"RichBrief Quality:      {results['rich_brief_populated_count']}/{results['cel_enabled_count']}")

    # Production readiness checks
    print(f"\n{'='*70}")
    print(f"PRODUCTION READINESS CHECKS")
    print(f"{'='*70}")

    checks = {
        "✅ Success Rate ≥99%": stats['success_rate'] >= 0.99,
        "✅ P95 Latency <500ms": stats['latency_p95'] < 500,
        "✅ P99 Latency <1000ms": stats['latency_p99'] < 1000,
        "✅ CEL Adoption ≥85%": stats['cel_adoption'] >= 0.85,
    }

    all_pass = True
    for check, passed in checks.items():
        status = "✅" if passed else "❌"
        print(f"{status} {check}")
        if not passed:
            all_pass = False

    print(f"\n{'='*70}")
    if all_pass:
        print(f"🎉 PRODUCTION READY FOR IMMEDIATE DEPLOYMENT")
    else:
        print(f"⚠️  REVIEW FAILURES BEFORE DEPLOYING")
    print(f"{'='*70}\n")

    # Assertions
    assert stats['success_rate'] >= 0.95, f"Success rate too low: {stats['success_rate']}"
    assert stats['latency_p95'] < 750, f"P95 latency too high: {stats['latency_p95']}ms"
    assert stats['cel_adoption'] >= 0.80, f"CEL adoption too low: {stats['cel_adoption']}"
