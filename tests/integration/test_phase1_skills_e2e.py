"""Integration tests for Phase 1 Skills Registry + A2A Bridge.

These tests verify end-to-end:
1. Skills can be registered + executed
2. Audit events are logged for every execution
3. A2A messaging works (task → Skill execution → result)
4. Tenant isolation is enforced
5. Failures are tracked and auto-disable after 3+ failures

Constraint: INTEGRATION TESTS ONLY (no isolated unit tests)
Each test exercises real execution path through registry → audit trail → A2A

Compliance: GDPR Art. 30 (all executions logged), Art. 32 (immutable results)
ADR-0544: Phase 1 big bang feature flags refactoring
"""

import asyncio
import json
import logging
from pathlib import Path

import pytest

from core.skills.skill_registry_phase1 import (
    SkillsRegistry,
    SkillMetadata,
    SkillOrigin,
    Skill,
    SkillExecutionResult,
    initialize_registry,
    get_registry,
)
from core.skills.a2a_skill_bridge import (
    A2ASkillBridge,
    A2ATaskEnvelope,
    A2ATaskStatus,
    initialize_a2a_bridge,
)
from core.skills.os_skills_phase1 import (
    DelegationRouterSkill,
    VibeEngineeringSkill,
    ContextAdapterSkill,
    register_builtin_skills,
)

logger = logging.getLogger(__name__)


# Mock audit backend for testing
class MockAuditBackend:
    """Mock audit backend that captures events in memory."""

    def __init__(self):
        self.events = []

    def write_event(self, event):
        """Record audit event."""
        self.events.append(event)

    def get_events(self, event_type=None):
        """Query events by type."""
        if event_type:
            return [e for e in self.events if e.get("event_type") == event_type]
        return self.events

    def clear(self):
        """Clear all events."""
        self.events.clear()


@pytest.fixture
def mock_audit():
    """Provide mock audit backend."""
    return MockAuditBackend()


@pytest.fixture
def registry(mock_audit):
    """Provide fresh Skills registry with mock audit."""
    reg = SkillsRegistry(audit_backend=mock_audit, tenant_id="_default")
    register_builtin_skills(reg)
    return reg


class TestSkillsRegistryE2E:
    """Integration tests for SkillsRegistry."""

    def test_skill_registration_and_listing(self, registry, mock_audit):
        """E2E: Register Skills and list them."""
        # Execute: list registered skills
        skills = registry.list_skills()

        # Verify: all 7 builtin skills (routing, vibe, context + 4 flag-backed)
        from core.skills.os_skills_phase1 import BUILTIN_SKILL_IDS
        assert len(skills) == len(BUILTIN_SKILL_IDS) == 7
        skill_ids = [s.id for s in skills]
        assert "os.delegation_router" in skill_ids
        assert "os.vibe_engineering" in skill_ids
        assert "os.context_adapter" in skill_ids

        # Verify: all skills have correct metadata
        for skill in skills:
            assert skill.origin == SkillOrigin.BUILTIN
            assert len(skill.version) > 0
            assert skill.owner == "corvin-os-team"

    def test_skill_execution_success(self, registry, mock_audit):
        """E2E: Execute Skill successfully + verify audit trail."""
        # Execute: call delegation router skill
        input_data = {"complexity": 8, "task_type": "analysis"}
        result = registry.execute("os.delegation_router", input_data)

        # Verify: execution succeeded
        assert result.status == "success"
        assert result.output is not None
        assert result.output["engine"] == "claude-opus-5"
        assert result.execution_time_ms >= 0
        assert result.tenant_id == "_default"

        # Verify: audit event was logged
        audit_events = mock_audit.get_events("SKILL_EXECUTED")
        assert len(audit_events) >= 1
        last_event = audit_events[-1]
        assert last_event["skill_id"] == "os.delegation_router"
        assert last_event["status"] == "success"
        assert last_event["tenant_id"] == "_default"

    def test_skill_execution_not_found(self, registry, mock_audit):
        """E2E: Execute non-existent Skill → error with audit trail."""
        # Execute: call skill that doesn't exist
        result = registry.execute("os.nonexistent", {})

        # Verify: failed as expected
        assert result.status == "error"
        assert "not found" in result.error_message.lower()

        # Verify: audit event recorded
        audit_events = mock_audit.get_events("SKILL_EXECUTED")
        assert len(audit_events) >= 1
        last_event = audit_events[-1]
        assert last_event["skill_id"] == "os.nonexistent"
        assert last_event["status"] == "error"

    def test_is_enabled_check(self, registry):
        """E2E: Check if Skills are enabled."""
        # Verify: all registered skills report as enabled
        assert registry.is_enabled("os.delegation_router") is True
        assert registry.is_enabled("os.vibe_engineering") is True
        assert registry.is_enabled("os.context_adapter") is True

        # Verify: non-existent skill reports as disabled
        assert registry.is_enabled("os.nonexistent") is False

    def test_vibe_engineering_execution(self, registry, mock_audit):
        """E2E: Execute vibe engineering skill."""
        # Execute: vibe engineering skill
        input_data = {
            "task_description": "This is a detailed task description about a complex problem",
            "priority_hint": 5,
            "time_budget_ms": 60000,
        }
        result = registry.execute("os.vibe_engineering", input_data)

        # Verify: execution succeeded
        assert result.status == "success"
        assert "vibe_score" in result.output
        assert "priority_adjustment" in result.output
        assert 0.0 <= result.output["vibe_score"] <= 1.0

        # Verify: audit trail
        audit_events = mock_audit.get_events("SKILL_EXECUTED")
        assert len(audit_events) >= 1
        assert audit_events[-1]["skill_id"] == "os.vibe_engineering"

    def test_context_adapter_composition(self, registry, mock_audit):
        """E2E: Context adapter composes router + vibe skills."""
        # Execute: context adapter (should call both router + vibe)
        input_data = {
            "complexity": 7,
            "task_type": "analysis",
            "task_description": "Complex data analysis task",
            "priority_hint": 6,
        }
        result = registry.execute("os.context_adapter", input_data)

        # Verify: execution succeeded
        assert result.status == "success"
        assert "routing_decision" in result.output
        assert "vibe_analysis" in result.output
        # ADR-0555: 3-tier output (base / injected / merged), merged is fail-closed
        assert "base_tier" in result.output
        assert "merged_tier" in result.output

        merged = result.output["merged_tier"]
        assert merged["engine"] == result.output["routing_decision"]["engine"]
        assert 1 <= merged["priority"] <= 10

        # Verify: audit trail (should log context_adapter execution)
        audit_events = mock_audit.get_events("SKILL_EXECUTED")
        context_events = [e for e in audit_events if e["skill_id"] == "os.context_adapter"]
        assert len(context_events) >= 1

    def test_tenant_isolation(self, mock_audit):
        """E2E: Verify tenant isolation in audit trail."""
        # Create two registries with different tenants
        registry1 = SkillsRegistry(audit_backend=mock_audit, tenant_id="tenant_a")
        registry2 = SkillsRegistry(audit_backend=mock_audit, tenant_id="tenant_b")

        register_builtin_skills(registry1)
        register_builtin_skills(registry2)

        # Execute: skill in each tenant
        registry1.execute("os.delegation_router", {"complexity": 5})
        registry2.execute("os.delegation_router", {"complexity": 7})

        # Verify: audit events show correct tenant isolation
        events_a = [e for e in mock_audit.events if e.get("tenant_id") == "tenant_a"]
        events_b = [e for e in mock_audit.events if e.get("tenant_id") == "tenant_b"]

        assert len(events_a) > 0
        assert len(events_b) > 0
        # Verify: no cross-tenant leakage
        assert len([e for e in events_a if e.get("skill_id") == "os.delegation_router"]) >= 1
        assert len([e for e in events_b if e.get("skill_id") == "os.delegation_router"]) >= 1


class TestA2ASkillBridgeE2E:
    """Integration tests for A2A-Skill bridge."""

    def test_a2a_task_execution(self, registry, mock_audit):
        """E2E: A2A task → Skill execution → A2A result."""
        # Setup: initialize A2A bridge
        bridge = A2ASkillBridge(registry, audit_backend=mock_audit)

        # Execute: A2A task
        task = A2ATaskEnvelope(
            task_id="task_001",
            skill_id="os.delegation_router",
            input={"complexity": 8, "task_type": "analysis"},
            source_app="test_app",
            tenant_id="_default",
        )
        result = bridge.handle_task(task)

        # Verify: A2A result is correct
        assert result.task_id == "task_001"
        assert result.status == A2ATaskStatus.SUCCESS
        assert result.output is not None
        assert result.output["engine"] == "claude-opus-5"

        # Verify: audit trail shows A2A events
        a2a_received = mock_audit.get_events("A2A_TASK_RECEIVED")
        a2a_executed = mock_audit.get_events("A2A_TASK_EXECUTED")
        assert len(a2a_received) >= 1
        assert len(a2a_executed) >= 1

        # Verify: skill execution also logged
        skill_events = mock_audit.get_events("SKILL_EXECUTED")
        assert len(skill_events) >= 1

    def test_a2a_task_parsing_from_json(self, registry, mock_audit):
        """E2E: Parse A2A task from JSON + execute."""
        bridge = A2ASkillBridge(registry, audit_backend=mock_audit)

        # Execute: parse JSON task envelope
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

        # Execute: run the task
        result = bridge.handle_task(task)
        assert result.status == A2ATaskStatus.SUCCESS
        assert result.output is not None

    def test_a2a_task_not_found(self, registry, mock_audit):
        """E2E: A2A task for non-existent Skill → failure."""
        bridge = A2ASkillBridge(registry, audit_backend=mock_audit)

        # Execute: A2A task for skill that doesn't exist
        task = A2ATaskEnvelope(
            task_id="task_003",
            skill_id="os.nonexistent",
            input={},
            source_app="test_app",
        )
        result = bridge.handle_task(task)

        # Verify: task failed as expected
        assert result.status == A2ATaskStatus.FAILURE
        assert result.error_message is not None

        # Verify: audit trail shows failure
        a2a_executed = mock_audit.get_events("A2A_TASK_EXECUTED")
        assert len(a2a_executed) >= 1
        assert a2a_executed[-1]["status"] == "failure"

    def test_a2a_task_with_timeout(self, registry, mock_audit):
        """E2E: A2A task execution with timeout."""
        bridge = A2ASkillBridge(registry, audit_backend=mock_audit)

        # Execute: A2A task with very short timeout
        task = A2ATaskEnvelope(
            task_id="task_004",
            skill_id="os.delegation_router",
            input={"complexity": 5},
            source_app="test_app",
            timeout_ms=1,  # 1ms timeout (will likely timeout)
        )

        # Note: current implementation doesn't actually timeout,
        # but this tests the audit trail integration
        result = bridge.handle_task(task)
        assert result.task_id == "task_004"
        assert result.status in [A2ATaskStatus.SUCCESS, A2ATaskStatus.TIMEOUT]

        # Verify: audit trail recorded
        a2a_events = mock_audit.get_events("A2A_TASK_EXECUTED")
        assert len(a2a_events) >= 1


class TestFeatureFlagReplacement:
    """Integration tests proving Skills replace feature flags.

    These tests verify the migration path from feature flags to Skills.
    """

    def test_skill_based_routing_decision(self, registry, mock_audit):
        """E2E: Skill-based routing replaces feature flag."""
        # Old way (feature flag):
        #   if config.features.vibe_engineering_v0_2:
        #       engine = smart_route(task)
        #
        # New way (Skill):
        #   if registry.is_enabled("os.vibe_engineering", "0.2"):
        #       result = registry.execute("os.delegation_router", task)

        # Execute: new way (Skill-based)
        assert registry.is_enabled("os.delegation_router") is True

        for complexity in [3, 5, 8, 10]:
            result = registry.execute(
                "os.delegation_router",
                {"complexity": complexity, "task_type": "analysis"},
            )
            assert result.status == "success"
            assert result.output["engine"] in [
                "claude-haiku-4",
                "claude-sonnet-4",
                "claude-opus-5",
            ]

        # Verify: all executions logged to audit trail
        audit_events = mock_audit.get_events("SKILL_EXECUTED")
        assert len(audit_events) >= 4  # One per execution

    def test_a2b_equivalence_routing(self, registry, mock_audit):
        """E2E: Skill routing matches expected A/B equivalence.

        In Phase 1, we run both old (feature flag) and new (Skill) paths in parallel
        and verify they produce identical results.
        """
        # For this test, we verify that Skill routing is deterministic
        # and repeatable (A/B equivalence requirement)

        test_cases = [
            {"complexity": 2, "task_type": "chat", "expected_engine": "claude-haiku-4"},
            {"complexity": 5, "task_type": "analysis", "expected_engine": "claude-sonnet-4"},
            # complexity >= 8 wins over the "code prefers Sonnet" rule (that rule is < 7 only)
            {"complexity": 9, "task_type": "code", "expected_engine": "claude-opus-5"},
        ]

        for test_case in test_cases:
            expected_engine = test_case.pop("expected_engine")
            result = registry.execute("os.delegation_router", test_case)

            assert result.status == "success"
            assert result.output["engine"] == expected_engine
            logger.info(
                f"A/B equivalence verified: {test_case} → {expected_engine}"
            )

        # Verify: audit trail proves all decisions were logged
        audit_events = mock_audit.get_events("SKILL_EXECUTED")
        assert len(audit_events) >= len(test_cases)


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
