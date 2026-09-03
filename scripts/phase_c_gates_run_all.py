#!/usr/bin/env python3
"""Phase C Measurement Gates Runner — ADR-0538

Execute all 5 gates, report pass/fail, decide deletion eligibility.
"""

import sys
import json
from datetime import datetime

sys.path.insert(0, "/home/shumway/projects/CorvinOS")

from core.compliance.phase_c_gates import (
    LearningStabilityGate,
    OldCodeUnreachabilityGate,
    NoDirectImportsGate,
    PluginMigrationGate,
    TenantIsolationGate,
)


def run_all_gates() -> dict:
    """Execute all 5 gates, return summary."""
    print("=" * 80)
    print("PHASE C MEASUREMENT GATES — WEEK 8 EVALUATION")
    print("=" * 80)
    print()

    gates = [
        ("Gate 1: Learning Stability", LearningStabilityGate()),
        ("Gate 2: Old-Code Unreachability", OldCodeUnreachabilityGate()),
        ("Gate 3: No-Direct-Imports", NoDirectImportsGate()),
        ("Gate 4: Plugin Migration", PluginMigrationGate()),
        ("Gate 5: Tenant-Isolation Safety", TenantIsolationGate()),
    ]

    results = {}
    all_passed = True

    for gate_name, gate in gates:
        print(f"\n{gate_name}")
        print("-" * 80)

        result = gate.execute()
        results[gate_name] = result

        status = "✅ PASS" if result.passed else "❌ FAIL"
        print(f"Status: {status}")

        # Gate-specific output
        if hasattr(result, "confidence_mean"):
            print(f"  Confidence Mean: {result.confidence_mean} (threshold: 0.85)")
            print(f"  Trend: {result.confidence_trend}")
            print(f"  Fallback Rate: {result.fallback_rate}%")

        elif hasattr(result, "direct_call_count"):
            print(f"  Direct Calls Found: {result.direct_call_count}")
            if result.violations:
                print(f"  Violations: {len(result.violations)}")
                for v in result.violations[:3]:
                    print(f"    - {v}")

        elif hasattr(result, "migration_rate"):
            print(f"  Migration Rate: {result.migration_rate}% ({result.migrated_plugins}/{result.total_plugins})")
            if result.laggards:
                print(f"  Laggards: {len(result.laggards)}")
                for l in result.laggards:
                    print(f"    - {l['plugin_id']} (owner: {l['owner']})")

        elif hasattr(result, "violations_found"):
            print(f"  Cross-Tenant Violations: {result.violations_found}")
            if result.violations:
                print(f"  Violations: {len(result.violations)}")
                for v in result.violations[:3]:
                    print(f"    - {v}")

        print()
        if not result.passed:
            all_passed = False

    # Summary
    print("=" * 80)
    print("OVERALL DECISION")
    print("=" * 80)
    print()

    if all_passed:
        print("✅ ALL GATES PASSED — READY FOR DELETION")
        print()
        print("Next Steps:")
        print("  1. Review gate evidence (above)")
        print("  2. Sign-off (DATE + OPERATOR)")
        print("  3. Execute deletion:")
        print("       git rm -r core/brain/ core/vibe_engineering/ core/context_engineering/legacy_v1.py")
        print("       git rm core/legacy_compat/")
        print("       git commit -m 'feat(legacy): Phase C — Delete old subsystems (all gates PASS)'")
        exit_code = 0
    else:
        print("❌ GATES FAILED — DELETION BLOCKED")
        print()
        print("Remediation Required:")
        failed_gates = [g for g, r in results.items() if not r.passed]
        for g in failed_gates:
            print(f"  - {g}")
        print()
        print("Action: Extend Phase B/C timeline, investigate failures, retry Week 9.")
        exit_code = 1

    # Write report
    report_path = "/home/shumway/projects/CorvinOS/docs/implementation/PHASE_C_GATES_REPORT.json"
    with open(report_path, "w") as f:
        report = {
            "timestamp": datetime.now().isoformat(),
            "overall_passed": all_passed,
            "gates": {name: {"passed": r.passed, "evidence": r.evidence} for name, r in results.items()},
        }
        json.dump(report, f, indent=2)

    print(f"\nReport saved: {report_path}")
    return results


if __name__ == "__main__":
    results = run_all_gates()
    sys.exit(0 if all(r.passed for r in results.values()) else 1)
