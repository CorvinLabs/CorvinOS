#!/usr/bin/env python3
"""Run TDE benchmark suite and generate report."""
import asyncio
import sys
from pathlib import Path

# Add parent to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from benchmarking.harness import run_benchmark_suite


async def main():
    """Execute full benchmark suite."""
    print("\n" + "=" * 80)
    print("TDE BENCHMARK SUITE — Token Savings SIMULATION")
    print("=" * 80)
    print("\nThis suite SIMULATES token usage from a deterministic model of")
    print("assumed per-category savings ratios — it executes no TDE code and")
    print("measures no real LLM usage (see harness.py honesty note; use")
    print("operator/orchestration/tde/bench.py for measured runs).\n")

    output_dir = Path(__file__).parent.parent.parent / "benchmark" / "results"
    run_id, results = await run_benchmark_suite(output_dir=output_dir)

    print(f"\n✅ All benchmarks complete!\n")
    print(f"Run ID: {run_id}")
    print(f"Results: {output_dir / run_id}")

    # Print summary
    import json
    analysis_file = output_dir / run_id / "analysis.json"
    if analysis_file.exists():
        data = json.loads(analysis_file.read_text())
        agg = data.get("aggregate", {})
        print(f"\n📊 SUMMARY:")
        print(f"  Total Tokens (CC): {agg.get('total_tokens_cc', 0):,}")
        print(f"  Total Tokens (TDE): {agg.get('total_tokens_tde', 0):,}")
        print(f"  Savings: {agg.get('total_savings', 0):,} ({agg.get('savings_pct', 0):.1f}%)")
        print(f"  Tasks Improved: {agg.get('tasks_improved', 0)}/{agg.get('total_tasks', 0)}")


if __name__ == "__main__":
    asyncio.run(main())
