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

# Test 3: Dependency Resolution
print("Test 3: Dependency DAG validation...")
from skills.dependency_resolver import DependencyResolver

registry = {
    'os.delegation_router': {'version': '1.0.0', 'depends_on': []},
    'os.context_adapter': {'version': '1.0.0', 'depends_on': []},
    'os.workflow_optimizer': {
        'version': '1.0.0',
        'depends_on': [{'name': 'os.delegation_router', 'version': '>=1.0.0', 'required': True}]
    }
}

resolver = DependencyResolver(registry)

# Test valid deps
is_valid, errors = resolver.validate_dependencies('os.workflow_optimizer')
assert is_valid is True, f"workflow_optimizer should be valid, got: {errors}"

# Test topological sort
sorted_skills = resolver.topological_sort(['os.workflow_optimizer', 'os.delegation_router'])
assert sorted_skills[0] == 'os.delegation_router', "delegation_router should load first"
assert sorted_skills[1] == 'os.workflow_optimizer', "workflow_optimizer should load second"

print("  Valid DAG detected, topological sort correct")
print("  ✓ PASS\n")

# Test 4: Cycle detection
print("Test 4: Cyclic dependency detection...")
registry_cycle = {
    'skill_a': {'version': '1.0.0', 'depends_on': [{'name': 'skill_b', 'version': '>=1.0.0'}]},
    'skill_b': {'version': '1.0.0', 'depends_on': [{'name': 'skill_a', 'version': '>=1.0.0'}]}
}

resolver_cycle = DependencyResolver(registry_cycle)
is_valid, errors = resolver_cycle.validate_dependencies('skill_a')
assert is_valid is False, "Cyclic deps should be detected"
assert any('Cyclic' in e for e in errors), "Should mention cycle"
print("  Cycle correctly detected and blocked")
print("  ✓ PASS\n")

print("=" * 60)
print("✅ PHASE 2-3 TESTS PASSED")
print("=" * 60)
print("\nSummary:")
print("  ✓ Feedback sanitization (fail-closed PII detection)")
print("  ✓ Optimization loop (Inner + Refinement converges)")
print("  ✓ DAG validation (topological sort)")
print("  ✓ Cycle detection (blocks cyclic deps)")
print("\n🚀 PHASE 2-3 FOUNDATION READY")
