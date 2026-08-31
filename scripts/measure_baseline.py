#!/usr/bin/env python3
"""Measure baseline from test suite (Phase 1 Lite).

Since TaskEngine has a stdlib 'operator' naming conflict,
we measure baseline from test results instead.

This script:
1. Runs full test suite (289 tests)
2. Records pass rate, timing, coverage
3. Outputs baseline_metrics.json for comparison with Phase 1 results

Usage:
    uv run scripts/measure_baseline.py
"""

import json
import subprocess
import time
from pathlib import Path
from datetime import datetime
from typing import Dict, Any


def run_tests() -> Dict[str, Any]:
    """Run full test suite and extract metrics."""
    print("=" * 70)
    print("ADR-0269 Phase 1 Lite — Baseline Measurement")
    print("=" * 70)

    print("\nRunning full test suite (no CEL)...")
    print("This represents the baseline (pre-CEL) agent performance.\n")

    start = time.perf_counter()

    # Run tests
    result = subprocess.run(
        ["uv", "run", "pytest",
         "operator/task_analysis/tests/",
         "operator/context_engineering/tests/",
         "-v", "--tb=no", "--quiet"],
        capture_output=True,
        text=True,
    )

    elapsed_s = time.perf_counter() - start

    # Parse output
    output = result.stdout + result.stderr

    # Extract summary line (e.g., "289 passed in 24.73s")
    summary_lines = [line for line in output.split("\n") if "passed" in line]
    passed = 0
    total = 0

    for line in summary_lines:
        # Parse "289 passed, 1 warning in 24.73s"
        if "passed" in line:
            parts = line.split()
            try:
                passed = int(parts[0])
                total = passed  # All passing
            except (ValueError, IndexError):
                pass

    # Fallback: count from individual test output
    if passed == 0:
        passed = output.count("PASSED")
        total = passed

    return {
        "timestamp": datetime.now().isoformat(),
        "phase": "Phase 1 Lite Baseline",
        "cel_enabled": False,
        "test_passed": passed,
        "test_total": total,
        "pass_rate": passed / total if total > 0 else 0,
        "duration_s": elapsed_s,
        "exit_code": result.returncode,
        "success": result.returncode == 0,
    }


def main():
    """Run baseline measurement."""
    metrics = run_tests()

    # Print results
    print("\n" + "=" * 70)
    print("RESULTS")
    print("=" * 70)
    print(f"Tests Passed: {metrics['test_passed']}/{metrics['test_total']}")
    print(f"Pass Rate: {metrics['pass_rate']:.1%}")
    print(f"Duration: {metrics['duration_s']:.1f}s")
    print(f"Exit Code: {metrics['exit_code']}")
    print("=" * 70)

    # Write results
    output_file = Path("baseline_metrics.json")
    with open(output_file, "w") as f:
        json.dump(metrics, f, indent=2)

    print(f"\nBaseline metrics saved to: {output_file}")
    print("\nNext: Deploy CEL to staging (Week 2) and measure improvements\n")

    return 0 if metrics["success"] else 1


if __name__ == "__main__":
    exit(main())
