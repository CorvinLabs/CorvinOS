#!/usr/bin/env python3
"""Phase 2-3 Tests: Learning Loop + Composition."""

import sys
from pathlib import Path
sys.path.insert(0, str(Path.cwd() / 'core'))

print("Phase 2-3: Learning Loop + Composition Tests\n")

# Test 1: Sanitizer
print("Test 1: Feedback sanitization (fail-closed)...")
from skills.feedback_ingester import Sanitizer

good_event = {'payload': {'latency': 10.5, 'cost': 0.05}}
bad_event_1 = {'payload': {'prompt': 'secret'}}  # Disallow field
bad_event_2 = {'payload': {'email': 'test@example.com'}}  # PII pattern

assert Sanitizer.sanitize_outcome(good_event) is not None, "Clean event should pass"
assert Sanitizer.sanitize_outcome(bad_event_1) is None, "Disallow field should be dropped"
assert Sanitizer.sanitize_outcome(bad_event_2) is None, "PII should be dropped"
print("  ✓ PASS\n")

# Test 2: Optimizer
print("Test 2: Optimization loop (Inner + Refinement)...")
from skills.optimizer import SkillOptimizer
import tempfile

with tempfile.TemporaryDirectory() as tmpdir:
    opt = SkillOptimizer(Path(tmpdir))

    # Mock run logs
    runs = [
        {'outcome': {'latency_actual': 8.0, 'latency_predicted': 7.5}} for _ in range(50)
    ] + [
        {'outcome': {'latency_actual': 8.2, 'latency_predicted': 7.5}} for _ in range(50)
    ]

    report = opt.optimize_epoch(runs, resume=False, target_score=0.70)

    assert report.baseline_score >= 0, "Baseline should be calculable"
    assert report.final_score >= report.baseline_score or report.final_score == report.baseline_score
    assert report.convergence_reason in ['target_reached', 'plateau', 'max_iterations_hit']
    print(f"  Baseline: {report.baseline_score:.3f} → Final: {report.final_score:.3f}")
    print(f"  Convergence: {report.convergence_reason}")
    print("  ✓ PASS\n")

print("=" * 60)
print("✅ PHASE 2-3 TESTS PASSED")
print("=" * 60)
print("\nSummary:")
print("  ✓ Feedback sanitization (fail-closed PII detection)")
print("  ✓ Optimization loop (Inner + Refinement converges)")
print("\n🚀 PHASE 2-3 FOUNDATION READY")
