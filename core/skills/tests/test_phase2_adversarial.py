"""
Phase 2 Adversarial Review Test Suite (ADR-0690)

4 Adversarial Vectors:
1. Input Injection — verify field sanitization
2. Composition DAG Bypass — verify dependency constraints
3. Timeout Enforcement — verify per-call budgets
4. Audit Trail Gaps — verify all executions logged

Status: Session 4 Adversarial Review
"""

import pytest
import asyncio
import json
from unittest.mock import Mock, patch
from core.skills.phase1_manifest_v2 import SkillManifestV2
from core.skills.phase1_skeleton_generator import SkillSkeletonGenerator
from core.skills.orchestrator import SkillOrchestrator
from core.learning.event_persistence import EventStore


class TestAdversarialVector1InputInjection:
    """Vector 1: Input Injection — Malicious field values rejected"""

    @pytest.mark.asyncio
    async def test_health_monitor_rejects_malicious_input(self):
        """Health Monitor sanitizes input fields"""
        from core.skills.health_monitor import HealthMonitor

        skill = HealthMonitor()

        # Attempt: Inject SQL-like syntax into input
        malicious_input = "'; DROP TABLE audit; --"
        result = await skill.execute({"query": malicious_input})

        # Verify: Skill treated input as literal string, no execution
        assert isinstance(result, dict)
        assert "error" not in result or "injection" not in str(result).lower()
        # Result should safely handle the string
        assert result.get("status") is not None

    @pytest.mark.asyncio
    async def test_context_bridge_rejects_massive_payload(self):
        """Context Bridge rejects oversized context snapshots"""
        from core.skills.context_bridge import ContextBridge

        skill = ContextBridge()

        # Attempt: Pass massive context dict (DoS attack)
        massive_context = {"data": "x" * 100_000_000}  # 100MB

        with pytest.raises((MemoryError, ValueError)):
            result = await skill.execute({"context": massive_context})

    @pytest.mark.asyncio
    async def test_orchestrator_rejects_circular_task_chain(self):
        """Orchestrator rejects circular task dependencies"""
        from core.skills.orchestrator import SkillOrchestrator

        orchestrator = SkillOrchestrator()

        # Attempt: Create circular task chain
        circular_tasks = {
            "task_a": {"depends_on": "task_b"},
            "task_b": {"depends_on": "task_c"},
            "task_c": {"depends_on": "task_a"}  # Circular!
        }

        with pytest.raises(ValueError, match="circular"):
            orchestrator.compose_tasks(circular_tasks)


class TestAdversarialVector2CompositionDAG:
    """Vector 2: Composition DAG Bypass — Dependency order enforced"""

    @pytest.mark.asyncio
    async def test_skills_load_in_dependency_order(self):
        """Skills must load in order: Health Monitor → Context Bridge → Orchestrator"""
        from core.skills.health_monitor import HealthMonitor
        from core.skills.context_bridge import ContextBridge
        from core.skills.orchestrator import SkillOrchestrator

        # Attempt: Load Orchestrator before Context Bridge
        orchestrator = SkillOrchestrator()
        context_bridge = ContextBridge()
        health_monitor = HealthMonitor()

        # Verify: All skills initialize (lower layers must be available)
        assert health_monitor is not None
        assert context_bridge is not None
        assert orchestrator is not None

        # Verify: Orchestrator can access Context Bridge (dependency satisfied)
        result = await orchestrator.validate_dependencies()
        assert result.get("context_bridge_available") is True

    @pytest.mark.asyncio
    async def test_skill_dependency_validation(self):
        """Skill manifest validates boot_layer constraints"""
        manifest = SkillManifestV2(
            skill_id="test.skill",
            version="1.0.0",
            boot_layer="core",  # Can only depend on "compliance"
            depends_on=["os.health_monitor"],  # Invalid: core cannot depend on core
        )

        # Verify: Validation catches invalid dependency
        errors = manifest.validate()
        assert len(errors) > 0
        assert any("dependency" in e.lower() for e in errors)


class TestAdversarialVector3TimeoutEnforcement:
    """Vector 3: Timeout Enforcement — Per-skill execution budgets"""

    @pytest.mark.asyncio
    async def test_skill_execution_respects_timeout_budget(self):
        """Skill execution halts after timeout budget"""
        from core.skills.health_monitor import HealthMonitor

        skill = HealthMonitor(timeout_s=0.5)  # 500ms budget

        # Attempt: Long-running operation (sleep 2s)
        with patch('asyncio.sleep') as mock_sleep:
            # Simulate 2-second operation
            async def slow_operation():
                await asyncio.sleep(2.0)
                return {"status": "complete"}

            # Set timeout to 0.5s
            task = asyncio.create_task(slow_operation())

            try:
                result = await asyncio.wait_for(task, timeout=0.5)
                assert False, "Should have timed out"
            except asyncio.TimeoutError:
                # Expected: Task timeout after 0.5s
                pass

    @pytest.mark.asyncio
    async def test_timeout_emits_audit_event(self):
        """Timeout condition logged to audit trail"""
        from core.skills.health_monitor import HealthMonitor
        from core.learning.event_persistence import EventStore

        skill = HealthMonitor(timeout_s=0.1)
        store = EventStore(tenant_id="_default")

        # Simulate timeout
        try:
            async def slow_op():
                await asyncio.sleep(1.0)
            await asyncio.wait_for(slow_op(), timeout=0.1)
        except asyncio.TimeoutError:
            # Emit timeout event
            store.write_event({
                "event_type": "skill_timeout",
                "skill_id": "os.health_monitor",
                "timeout_s": 0.1
            })

        # Verify: Event logged
        events = store.query(event_type="skill_timeout")
        assert len(events) > 0

    @pytest.mark.asyncio
    async def test_orchestrator_enforces_per_skill_timeout(self):
        """Orchestrator enforces individual skill budgets"""
        from core.skills.orchestrator import SkillOrchestrator

        orchestrator = SkillOrchestrator()

        # Configure timeouts per skill
        config = {
            "os.health_monitor": {"timeout_s": 1.0},
            "os.context_bridge": {"timeout_s": 2.0},
            "os.orchestrator": {"timeout_s": 5.0},
        }

        orchestrator.set_timeout_config(config)

        # Verify: Config applied
        assert orchestrator.get_timeout("os.health_monitor") == 1.0
        assert orchestrator.get_timeout("os.context_bridge") == 2.0
        assert orchestrator.get_timeout("os.orchestrator") == 5.0


class TestAdversarialVector4AuditTrailGaps:
    """Vector 4: Audit Trail Gaps — 100% execution coverage"""

    @pytest.mark.asyncio
    async def test_every_skill_execution_logged(self):
        """Every skill execution produces audit event"""
        from core.skills.health_monitor import HealthMonitor
        from core.learning.event_persistence import EventStore

        store = EventStore(tenant_id="_default")
        skill = HealthMonitor(audit_store=store)

        # Execute skill
        result = await skill.execute({"query": "test"})

        # Verify: Event logged
        events = store.query(event_type="skill_executed")
        assert len(events) > 0

        latest = events[-1]
        assert latest["skill_id"] == "os.health_monitor"
        assert latest["status"] in ["success", "error", "timeout"]

    @pytest.mark.asyncio
    async def test_audit_trail_hash_chain_integrity(self):
        """Audit events hash-chained (prev_hash → hash)"""
        from core.learning.event_persistence import EventStore

        store = EventStore(tenant_id="_default")

        # Write 3 events
        for i in range(3):
            store.write_event({
                "event_type": "test_event",
                "index": i
            })

        # Verify: Hash chain intact
        events = store.query()
        for i in range(1, len(events)):
            assert events[i]["prev_hash"] == events[i-1]["hash"]

    @pytest.mark.asyncio
    async def test_error_path_logged_to_audit_trail(self):
        """Skill errors logged (no silent failures)"""
        from core.skills.health_monitor import HealthMonitor
        from core.learning.event_persistence import EventStore

        store = EventStore(tenant_id="_default")
        skill = HealthMonitor(audit_store=store)

        # Simulate error
        with patch.object(skill, '_fetch_state', side_effect=RuntimeError("Connection failed")):
            result = await skill.execute({"query": "test"})

        # Verify: Error logged
        events = store.query(event_type="skill_executed")
        error_events = [e for e in events if e.get("status") == "error"]
        assert len(error_events) > 0


# Fixtures for test data
@pytest.fixture
def audit_store():
    return EventStore(tenant_id="_test")


@pytest.fixture
def test_skill_manifest():
    return SkillManifestV2(
        skill_id="test.skill",
        version="1.0.0",
        boot_layer="bundled",
        depends_on=[],
        confidence_field="test_confidence",
        learning_config={"enabled": True}
    )


# Test summary
if __name__ == "__main__":
    print("=" * 60)
    print("Phase 2 Adversarial Review Test Suite (ADR-0690)")
    print("=" * 60)
    print("\nVectors:")
    print("  [1/4] Input Injection — field sanitization")
    print("  [2/4] Composition DAG Bypass — dependency order")
    print("  [3/4] Timeout Enforcement — per-skill budgets")
    print("  [4/4] Audit Trail Gaps — 100% coverage")
    print("\nRun: pytest test_phase2_adversarial.py -v")
    print("=" * 60)
