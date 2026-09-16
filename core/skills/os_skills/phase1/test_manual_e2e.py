#!/usr/bin/env python3
"""Manual E2E test for Phase 1 Skills (no pytest required).

Run: cd /home/shumway/projects/CorvinOS/core/skills/os_skills/phase1 && python3 test_manual_e2e.py
"""

import sys
import tempfile
from pathlib import Path

# Import locally (relative imports)
from health_monitor import HealthMonitor, HealthLevel
from context_bridge import ContextBridge, SplitReason
from orchestrator import BasicOrchestrator, TaskDefinition
from mock_audit_trail import MockAuditTrail


def test_e2e_health_monitor():
    """E2E Test 1: Health Monitor reachable and audited."""
    print("\n[TEST 1] Health Monitor E2E")

    with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
        audit_file = Path(f.name)

    try:
        audit = MockAuditTrail(audit_file)
        skill = HealthMonitor(tenant_id="_default", audit_trail=audit)

        # Execute
        status = skill.execute({"subsystems": ["audit_chain", "plugin_system"]})

        # Verify output
        assert status is not None, "Status is None"
        assert status.overall_health == HealthLevel.HEALTHY, f"Expected healthy, got {status.overall_health}"
        assert len(status.metrics) > 0, "No metrics recorded"

        # Verify audit
        events = audit.read_events("_default")
        assert len(events) > 0, "No audit events"
        assert events[0]["skill_id"] == "os.health_monitor", f"Wrong skill: {events[0]['skill_id']}"
        assert events[0]["status"] == "success", f"Wrong status: {events[0]['status']}"

        print("✓ Health Monitor works end-to-end")
        print(f"  - Metrics: {list(status.metrics.keys())}")
        print(f"  - Audit events: {len(events)}")
        return True

    except Exception as e:
        print(f"✗ Health Monitor FAILED: {e}")
        import traceback
        traceback.print_exc()
        return False
    finally:
        if audit_file.exists():
            audit_file.unlink()


def test_e2e_context_bridge():
    """E2E Test 2: Context Bridge reachable and makes split decisions."""
    print("\n[TEST 2] Context Bridge E2E")

    with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
        audit_file = Path(f.name)

    try:
        audit = MockAuditTrail(audit_file)
        skill = ContextBridge(tenant_id="_default", audit_trail=audit)

        # Test case 1: No split needed
        should_split, snapshot = skill.execute({
            "current_session_id": "sess_1",
            "tokens_used": 10000,
            "tokens_max": 100000,
            "task_metadata": {},
        })

        assert should_split is False, "Should not split at 10% usage"

        # Test case 2: Split at threshold
        should_split, snapshot = skill.execute({
            "current_session_id": "sess_1",
            "tokens_used": 90000,
            "tokens_max": 100000,
            "task_metadata": {},
        })

        assert should_split is True, "Should split at 90% usage"
        assert snapshot is not None, "Snapshot is None"

        # Verify audit
        events = audit.read_events("_default")
        assert len(events) >= 2, f"Expected >=2 events, got {len(events)}"

        print("✓ Context Bridge works end-to-end")
        print(f"  - No-split decision: OK")
        print(f"  - Split decision: OK ({snapshot.split_reason.value})")
        print(f"  - Audit events: {len(events)}")
        return True

    except Exception as e:
        print(f"✗ Context Bridge FAILED: {e}")
        import traceback
        traceback.print_exc()
        return False
    finally:
        if audit_file.exists():
            audit_file.unlink()


def test_e2e_orchestrator_composition():
    """E2E Test 3: Orchestrator composes Health Monitor and Context Bridge."""
    print("\n[TEST 3] Orchestrator Composition E2E")

    with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
        audit_file = Path(f.name)

    try:
        audit = MockAuditTrail(audit_file)

        # Create all three skills
        health = HealthMonitor(tenant_id="_default", audit_trail=audit)
        bridge = ContextBridge(tenant_id="_default", audit_trail=audit)
        orchestrator = BasicOrchestrator(tenant_id="_default", audit_trail=audit)

        # Create a task
        task = TaskDefinition(
            task_id="task_001",
            task_type="code_gen",
            priority=5,
            input_data={"prompt": "hello"},
            metadata={"user_id": "user_1"},
        )

        # Execute orchestrator (which calls dependencies)
        plan = orchestrator.execute({
            "task": task,
            "health_monitor": health,
            "context_bridge": bridge,
            "available_plugins": ["p1", "p2"],
            "worker_pool_status": {"w0": 2, "w1": 3},
        })

        assert plan is not None, "Plan is None"
        assert plan.task_id == "task_001", f"Wrong task ID: {plan.task_id}"
        assert plan.destination is not None, "Destination is None"

        # Verify all three skills are in audit trail
        events = audit.read_events("_default")
        skill_ids = {e["skill_id"] for e in events}

        assert "os.health_monitor" in skill_ids, "Health Monitor not audited"
        assert "os.context_bridge" in skill_ids, "Context Bridge not audited"
        assert "os.orchestrator" in skill_ids, "Orchestrator not audited"

        print("✓ Orchestrator composition works end-to-end")
        print(f"  - Task routed: {plan.task_id} → {plan.destination}")
        print(f"  - Strategy: {plan.strategy.value}")
        print(f"  - Audit events: {len(events)} (all 3 skills present)")
        return True

    except Exception as e:
        print(f"✗ Orchestrator Composition FAILED: {e}")
        import traceback
        traceback.print_exc()
        return False
    finally:
        if audit_file.exists():
            audit_file.unlink()


def test_audit_chain_integrity():
    """E2E Test 4: Audit trail maintains hash-chain integrity."""
    print("\n[TEST 4] Audit Chain Integrity E2E")

    with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
        audit_file = Path(f.name)

    try:
        audit = MockAuditTrail(audit_file)
        skill = HealthMonitor(tenant_id="_default", audit_trail=audit)

        # Execute multiple times to build chain
        for i in range(5):
            skill.execute({"subsystems": ["audit_chain"]})

        # Verify chain is intact
        is_valid = audit.verify_chain("_default")
        assert is_valid is True, "Chain verification failed"

        events = audit.read_events("_default")
        assert len(events) == 5, f"Expected 5 events, got {len(events)}"

        print("✓ Audit chain integrity maintained")
        print(f"  - Events: {len(events)}")
        print(f"  - Chain valid: {is_valid}")
        return True

    except Exception as e:
        print(f"✗ Audit Chain Integrity FAILED: {e}")
        import traceback
        traceback.print_exc()
        return False
    finally:
        if audit_file.exists():
            audit_file.unlink()


def test_tenant_isolation():
    """Adversarial Test 5: Audit trail maintains tenant isolation."""
    print("\n[TEST 5] Tenant Isolation E2E")

    with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
        audit_file = Path(f.name)

    try:
        audit = MockAuditTrail(audit_file)

        # Create skills for two tenants
        skill_t1 = HealthMonitor(tenant_id="tenant_1", audit_trail=audit)
        skill_t2 = HealthMonitor(tenant_id="tenant_2", audit_trail=audit)

        # Execute on both
        skill_t1.execute({})
        skill_t2.execute({})

        # Read events per tenant
        events_t1 = audit.read_events("tenant_1")
        events_t2 = audit.read_events("tenant_2")

        assert len(events_t1) == 1, f"T1 should have 1 event, got {len(events_t1)}"
        assert len(events_t2) == 1, f"T2 should have 1 event, got {len(events_t2)}"
        assert events_t1[0]["tenant_id"] == "tenant_1", "Wrong tenant in T1 events"
        assert events_t2[0]["tenant_id"] == "tenant_2", "Wrong tenant in T2 events"

        print("✓ Tenant isolation works")
        print(f"  - Tenant 1 events: {len(events_t1)} (correct tenant)")
        print(f"  - Tenant 2 events: {len(events_t2)} (correct tenant)")
        return True

    except Exception as e:
        print(f"✗ Tenant Isolation FAILED: {e}")
        import traceback
        traceback.print_exc()
        return False
    finally:
        if audit_file.exists():
            audit_file.unlink()


def main():
    """Run all E2E tests."""
    print("=" * 60)
    print("OS-SKILLS PHASE 1 — MANUAL E2E TEST SUITE")
    print("=" * 60)

    results = []

    # Run tests
    results.append(("Health Monitor", test_e2e_health_monitor()))
    results.append(("Context Bridge", test_e2e_context_bridge()))
    results.append(("Orchestrator Composition", test_e2e_orchestrator_composition()))
    results.append(("Audit Chain Integrity", test_audit_chain_integrity()))
    results.append(("Tenant Isolation", test_tenant_isolation()))

    # Summary
    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)

    passed = sum(1 for _, result in results if result)
    total = len(results)

    for name, result in results:
        status = "✓ PASS" if result else "✗ FAIL"
        print(f"{status}: {name}")

    print(f"\nTotal: {passed}/{total} passed")

    if passed == total:
        print("\n✓ ALL TESTS PASSED — Phase 1 is production-ready!")
        return 0
    else:
        print(f"\n✗ {total - passed} test(s) failed")
        return 1


if __name__ == "__main__":
    sys.exit(main())
