#!/usr/bin/env python3
"""
Standalone E2E Test Runner: 6 Critical CorvinOS Flows with Real Opus

Usage: python3 tests/run_e2e_critical_paths.py
"""

import sys
import os
import json
import tempfile
from pathlib import Path
from datetime import datetime
import time

# Add repo to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# Import test functions
from tests.test_critical_paths_e2e_real_opus import (
    MockAuditBackend,
    MockLearningBackend,
    test_flow_1_task_routing_real_opus,
    test_flow_2_skill_execution_learning,
    test_flow_3_feedback_optimization,
    test_flow_4_console_dashboard_live_data,
    test_flow_5_consent_house_rules,
    test_flow_6_tenant_isolation,
)

from core.skills.skill_registry_phase1 import SkillsRegistry
from core.skills.os_skills_phase1 import register_builtin_skills


def setup_e2e_env():
    """Setup E2E environment."""
    tmpdir = Path(tempfile.mkdtemp(prefix="e2e_"))

    audit = MockAuditBackend(tmpdir)
    learning = MockLearningBackend()

    # Create skills registry
    reg = SkillsRegistry(
        audit_backend=audit,
        tenant_id="_default",
        learning_backend=learning,
    )

    # Register builtin skills
    try:
        register_builtin_skills(reg)
    except Exception as e:
        print(f"Warning: Could not register builtin skills: {e}")

    return {
        "tmpdir": tmpdir,
        "audit": audit,
        "learning": learning,
        "registry": reg,
        "tenant_id": "_default",
        "lom": "tests/run_e2e_critical_paths.py:setup_e2e_env",
    }


def run_test(test_func, env, test_num):
    """Run a single test and return result."""
    test_name = test_func.__name__
    print(f"\n{'='*80}")
    print(f"RUNNING TEST {test_num}: {test_name.upper()}")
    print(f"{'='*80}")

    start = time.time()
    try:
        result = test_func(env)
        elapsed = time.time() - start

        result["elapsed_seconds"] = elapsed
        result["status"] = "PASS"
        print(f"\n✓ TEST {test_num} PASSED in {elapsed:.1f}s")
        return result

    except AssertionError as e:
        elapsed = time.time() - start
        print(f"\n✗ TEST {test_num} FAILED: {e}")
        return {
            "test_name": test_name,
            "status": "FAIL",
            "error": str(e),
            "elapsed_seconds": elapsed,
        }
    except Exception as e:
        elapsed = time.time() - start
        print(f"\n✗ TEST {test_num} ERROR: {type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()
        return {
            "test_name": test_name,
            "status": "ERROR",
            "error": f"{type(e).__name__}: {str(e)[:200]}",
            "elapsed_seconds": elapsed,
        }


def main():
    """Run all 6 tests and generate report."""
    print("\n" + "=" * 80)
    print("CORVINOS E2E TEST SUITE: 6 CRITICAL FLOWS WITH REAL OPUS")
    print("=" * 80)
    print(f"Start time: {datetime.utcnow().isoformat()}Z")

    suite_start = time.time()

    # Setup environment
    print("\n[SETUP] Creating E2E environment...")
    env = setup_e2e_env()
    print(f"  ✓ Temp directory: {env['tmpdir']}")

    # Run all 6 tests
    tests = [
        (test_flow_1_task_routing_real_opus, "Task Routing with Real Opus"),
        (test_flow_2_skill_execution_learning, "Skill Execution + Learning Events"),
        (test_flow_3_feedback_optimization, "User Feedback + Optimization"),
        (test_flow_4_console_dashboard_live_data, "Console Dashboard Live Data"),
        (test_flow_5_consent_house_rules, "Consent + House-Rules Enforcement"),
        (test_flow_6_tenant_isolation, "Tenant Isolation (GDPR)"),
    ]

    results = []
    for i, (test_func, description) in enumerate(tests, 1):
        result = run_test(test_func, env, i)
        result["description"] = description
        results.append(result)

    # Summary
    suite_elapsed = time.time() - suite_start

    print("\n" + "=" * 80)
    print("E2E TEST SUMMARY")
    print("=" * 80)

    passed = sum(1 for r in results if r["status"] == "PASS")
    failed = sum(1 for r in results if r["status"] == "FAIL")
    errors = sum(1 for r in results if r["status"] == "ERROR")

    for i, r in enumerate(results, 1):
        status_icon = "✓" if r["status"] == "PASS" else ("✗" if r["status"] == "FAIL" else "⚠")
        elapsed = r.get("elapsed_seconds", 0)
        print(f"{status_icon} TEST {i}: {r.get('description', 'N/A')}")
        print(f"   Status: {r['status']} ({elapsed:.1f}s)")
        if r.get("error"):
            print(f"   Error: {r['error'][:100]}")

    print(f"\nRESULTS: {passed} PASS, {failed} FAIL, {errors} ERROR (Total: {suite_elapsed:.1f}s)")

    # Save report
    report = {
        "timestamp": datetime.utcnow().isoformat() + "Z",
        "total_tests": len(results),
        "passed": passed,
        "failed": failed,
        "errors": errors,
        "total_time_seconds": suite_elapsed,
        "results": results,
    }

    report_path = env["tmpdir"] / "e2e_test_report.json"
    with open(report_path, "w") as f:
        json.dump(report, f, indent=2)

    print(f"\n✓ Report saved: {report_path}")

    # Return exit code
    if failed > 0 or errors > 0:
        print(f"\n✗ {failed + errors} tests did not pass")
        return 1
    else:
        print(f"\n✓ All {passed} tests PASSED!")
        return 0


if __name__ == "__main__":
    sys.exit(main())
