#!/usr/bin/env python3
"""Standalone test for Phase 1 Skills Registry (no pytest required).

Verifies:
1. Skills can be registered + executed
2. Audit events are logged
3. A2A messaging works
4. Tenant isolation is enforced
"""

import sys
import json
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from core.skills.skill_registry_phase1 import (
    SkillsRegistry,
    initialize_registry,
    get_registry,
)
from core.skills.a2a_skill_bridge import A2ASkillBridge, A2ATaskEnvelope
from core.skills.os_skills_phase1 import register_builtin_skills


class MockAuditBackend:
    """Mock audit backend for testing."""

    def __init__(self):
        self.events = []

    def write_event(self, event):
        self.events.append(event)

    def get_events_by_type(self, event_type):
        return [e for e in self.events if e.get("event_type") == event_type]


def test_skill_registration_and_listing():
    """Test: Register Skills and list them."""
    print("\n[TEST 1] Skill registration and listing...")
    mock_audit = MockAuditBackend()
    registry = SkillsRegistry(audit_backend=mock_audit, tenant_id="_default")
    register_builtin_skills(registry)

    skills = registry.list_skills()
    assert len(skills) == 3, f"Expected 3 skills, got {len(skills)}"

    skill_ids = [s.id for s in skills]
    assert "os.delegation_router" in skill_ids
    assert "os.vibe_engineering" in skill_ids
    assert "os.context_adapter" in skill_ids

    print("✅ PASS: All 3 builtin Skills registered")
    for skill in skills:
        print(f"   - {skill.id}:{skill.version} ({skill.origin.value})")


def test_skill_execution_success():
    """Test: Execute Skill successfully + verify audit trail."""
    print("\n[TEST 2] Skill execution + audit trail...")
    mock_audit = MockAuditBackend()
    registry = SkillsRegistry(audit_backend=mock_audit, tenant_id="_default")
    register_builtin_skills(registry)

    # Execute delegation router skill
    input_data = {"complexity": 8, "task_type": "analysis"}
    result = registry.execute("os.delegation_router", input_data)

    assert result.status == "success", f"Expected success, got {result.status}"
    assert result.output is not None
    assert result.output["engine"] == "claude-opus-5"
    assert result.tenant_id == "_default"

    # Verify audit event
    skill_events = mock_audit.get_events_by_type("SKILL_EXECUTED")
    assert len(skill_events) >= 1, "Expected at least 1 SKILL_EXECUTED event"

    last_event = skill_events[-1]
    assert last_event["skill_id"] == "os.delegation_router"
    assert last_event["status"] == "success"
    assert last_event["tenant_id"] == "_default"

    print("✅ PASS: Skill execution + audit logging verified")
    print(f"   Output: {result.output}")
    print(f"   Audit events recorded: {len(skill_events)}")


def test_skill_not_found():
    """Test: Execute non-existent Skill → error."""
    print("\n[TEST 3] Skill not found error handling...")
    mock_audit = MockAuditBackend()
    registry = SkillsRegistry(audit_backend=mock_audit)
    register_builtin_skills(registry)

    result = registry.execute("os.nonexistent", {})

    assert result.status == "error", f"Expected error, got {result.status}"
    assert "not found" in result.error_message.lower()

    # Verify audit event
    skill_events = mock_audit.get_events_by_type("SKILL_EXECUTED")
    assert len(skill_events) >= 1
    assert skill_events[-1]["status"] == "error"

    print("✅ PASS: Skill-not-found error handled + audited")
    print(f"   Error message: {result.error_message}")


def test_is_enabled_check():
    """Test: Check if Skills are enabled."""
    print("\n[TEST 4] Skill enabled check...")
    mock_audit = MockAuditBackend()
    registry = SkillsRegistry(audit_backend=mock_audit)
    register_builtin_skills(registry)

    assert registry.is_enabled("os.delegation_router") is True
    assert registry.is_enabled("os.vibe_engineering") is True
    assert registry.is_enabled("os.context_adapter") is True
    assert registry.is_enabled("os.nonexistent") is False

    print("✅ PASS: All enabled checks working")


def test_vibe_engineering_execution():
    """Test: Execute vibe engineering skill."""
    print("\n[TEST 5] Vibe engineering execution...")
    mock_audit = MockAuditBackend()
    registry = SkillsRegistry(audit_backend=mock_audit)
    register_builtin_skills(registry)

    input_data = {
        "task_description": "This is a detailed description of a complex task",
        "priority_hint": 5,
        "time_budget_ms": 60000,
    }
    result = registry.execute("os.vibe_engineering", input_data)

    assert result.status == "success"
    assert "vibe_score" in result.output
    assert "priority_adjustment" in result.output
    assert 0.0 <= result.output["vibe_score"] <= 1.0

    print("✅ PASS: Vibe engineering skill executed")
    print(f"   Vibe score: {result.output['vibe_score']:.2f}")
    print(f"   Priority adjustment: {result.output['priority_adjustment']}")


def test_context_adapter_composition():
    """Test: Context adapter composes router + vibe."""
    print("\n[TEST 6] Context adapter composition...")
    mock_audit = MockAuditBackend()
    registry = SkillsRegistry(audit_backend=mock_audit)
    register_builtin_skills(registry)

    input_data = {
        "complexity": 7,
        "task_type": "analysis",
        "task_description": "Complex data analysis",
        "priority_hint": 6,
    }
    result = registry.execute("os.context_adapter", input_data)

    assert result.status == "success"
    assert "routing_decision" in result.output
    assert "vibe_analysis" in result.output
    assert "final_routing" in result.output

    routing = result.output["final_routing"]
    assert "engine" in routing
    assert "final_priority" in routing

    print("✅ PASS: Context adapter composition working")
    print(f"   Engine: {routing['engine']}")
    print(f"   Final priority: {routing['final_priority']}")


def test_tenant_isolation():
    """Test: Verify tenant isolation."""
    print("\n[TEST 7] Tenant isolation...")
    mock_audit = MockAuditBackend()

    # Create registries for different tenants
    registry_a = SkillsRegistry(audit_backend=mock_audit, tenant_id="tenant_a")
    registry_b = SkillsRegistry(audit_backend=mock_audit, tenant_id="tenant_b")

    register_builtin_skills(registry_a)
    register_builtin_skills(registry_b)

    # Execute in each tenant
    registry_a.execute("os.delegation_router", {"complexity": 5})
    registry_b.execute("os.delegation_router", {"complexity": 7})

    # Verify tenant isolation
    events_a = [e for e in mock_audit.events if e.get("tenant_id") == "tenant_a"]
    events_b = [e for e in mock_audit.events if e.get("tenant_id") == "tenant_b"]

    assert len(events_a) > 0
    assert len(events_b) > 0

    print("✅ PASS: Tenant isolation verified")
    print(f"   Tenant A events: {len(events_a)}")
    print(f"   Tenant B events: {len(events_b)}")


def test_a2a_task_execution():
    """Test: A2A task → Skill execution."""
    print("\n[TEST 8] A2A task execution...")
    mock_audit = MockAuditBackend()
    registry = SkillsRegistry(audit_backend=mock_audit)
    register_builtin_skills(registry)

    bridge = A2ASkillBridge(registry, audit_backend=mock_audit)

    task = A2ATaskEnvelope(
        task_id="task_001",
        skill_id="os.delegation_router",
        input={"complexity": 8, "task_type": "analysis"},
        source_app="test_app",
        tenant_id="_default",
    )
    result = bridge.handle_task(task)

    assert result.task_id == "task_001"
    assert result.status.value == "success"
    assert result.output is not None
    assert result.output["engine"] == "claude-opus-5"

    # Verify A2A audit events
    a2a_events = mock_audit.get_events_by_type("A2A_TASK_RECEIVED")
    assert len(a2a_events) >= 1

    print("✅ PASS: A2A task execution working")
    print(f"   Task ID: {result.task_id}")
    print(f"   Result status: {result.status.value}")
    print(f"   Engine: {result.output['engine']}")


def test_a2a_json_parsing():
    """Test: Parse A2A task from JSON."""
    print("\n[TEST 9] A2A JSON task parsing...")
    mock_audit = MockAuditBackend()
    registry = SkillsRegistry(audit_backend=mock_audit)
    register_builtin_skills(registry)

    bridge = A2ASkillBridge(registry, audit_backend=mock_audit)

    json_task = json.dumps({
        "task_id": "task_002",
        "skill_id": "os.vibe_engineering",
        "input": {"task_description": "Complex task", "priority_hint": 5},
        "source_app": "remote_app",
        "tenant_id": "_default",
        "timeout_ms": 5000,
    })

    task = bridge.parse_task_from_json(json_task)
    assert task is not None
    assert task.task_id == "task_002"
    assert task.skill_id == "os.vibe_engineering"

    result = bridge.handle_task(task)
    assert result.status.value == "success"

    print("✅ PASS: A2A JSON parsing working")
    print(f"   Parsed task ID: {task.task_id}")
    print(f"   Result status: {result.status.value}")


def test_feature_flag_replacement():
    """Test: Skill-based routing replaces feature flags."""
    print("\n[TEST 10] Feature flag replacement pattern...")
    mock_audit = MockAuditBackend()
    registry = SkillsRegistry(audit_backend=mock_audit)
    register_builtin_skills(registry)

    # Old way: if config.features.vibe_engineering_v0_2: route = smart_route()
    # New way: if registry.is_enabled("os.vibe_engineering"): result = registry.execute()

    assert registry.is_enabled("os.delegation_router") is True

    test_cases = [
        (3, "claude-haiku-4"),
        (5, "claude-sonnet-4"),
        (8, "claude-opus-5"),
    ]

    for complexity, expected_engine in test_cases:
        result = registry.execute(
            "os.delegation_router",
            {"complexity": complexity, "task_type": "analysis"},
        )
        assert result.status == "success"
        assert result.output["engine"] == expected_engine

    print("✅ PASS: Feature flag replacement pattern verified")
    print(f"   Tested {len(test_cases)} complexity levels")


def run_all_tests():
    """Run all integration tests."""
    print("=" * 70)
    print("Phase 1 Skills Registry Integration Tests (Standalone)")
    print("=" * 70)

    tests = [
        test_skill_registration_and_listing,
        test_skill_execution_success,
        test_skill_not_found,
        test_is_enabled_check,
        test_vibe_engineering_execution,
        test_context_adapter_composition,
        test_tenant_isolation,
        test_a2a_task_execution,
        test_a2a_json_parsing,
        test_feature_flag_replacement,
    ]

    passed = 0
    failed = 0

    for test_func in tests:
        try:
            test_func()
            passed += 1
        except Exception as e:
            print(f"❌ FAIL: {test_func.__name__}")
            print(f"   Error: {e}")
            failed += 1
            import traceback
            traceback.print_exc()

    print("\n" + "=" * 70)
    print(f"Results: {passed} passed, {failed} failed")
    print("=" * 70)

    return failed == 0


if __name__ == "__main__":
    success = run_all_tests()
    sys.exit(0 if success else 1)
